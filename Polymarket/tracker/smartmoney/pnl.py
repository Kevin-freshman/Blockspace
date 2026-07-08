"""收益引擎：现金流法计算每个地址的盈亏与行为统计。

现金流口径（单位 USDC）：
  cash_in  = 卖出所得 + merge 返还 + redeem 赔付
  cash_out = 买入支出 + split 投入
  cash_net = cash_in - cash_out
  total_pnl = cash_net + 当前持仓市值（Data API positions, size * curPrice）

只统计 fills 里 maker == 该地址 的记录：每个用户自己的订单成交时
都会以自己为 maker 发出一条 OrderFilled，因此 maker 侧已完整覆盖、且不重复。

胜率口径：只看已揭晓市场；该市场现金流净额 > 0 记为一胜。
"""
import datetime
import statistics

from .db import connect
from .util import http_get_json, setup_logging

log = setup_logging()

LATE_WINDOW_H = 24  # "临近截止买入" 的时间窗


def _refresh_positions(cfg: dict, conn, address: str) -> float:
    """从 Data API 拉当前持仓，返回持仓总市值。"""
    base = cfg["data_api"]["base_url"]
    page = cfg["data_api"]["positions_page_size"]
    max_pages = cfg["data_api"]["max_positions_pages"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn.execute("DELETE FROM positions_cache WHERE address=?", (address,))
    total = 0.0
    for i in range(max_pages):
        data = http_get_json(f"{base}/positions", params={
            "user": address, "limit": page, "offset": i * page})
        if not isinstance(data, list) or not data:
            break
        for p in data:
            size = float(p.get("size") or 0)
            cur_price = float(p.get("curPrice") or 0)
            value = float(p.get("currentValue") or size * cur_price)
            total += value
            conn.execute(
                "INSERT OR REPLACE INTO positions_cache"
                "(address, token_id, size, cur_price, value, title, fetched_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (address, str(p.get("asset")), size, cur_price, value, p.get("title"), now))
        if len(data) < page:
            break
    return total


def _official_pnl(conn, address: str):
    row = conn.execute(
        "SELECT pnl FROM leaderboard_snapshots WHERE address=? AND time_period='ALL'"
        " ORDER BY fetched_at DESC LIMIT 1", (address,)).fetchone()
    return row["pnl"] if row else None


def compute_address(cfg: dict, conn, address: str, refresh_positions: bool = True) -> dict:
    """计算单个地址的全部统计，写入 address_stats / address_market_pnl。"""
    addr = address.lower()

    # token -> market 映射与市场信息
    token_cond = {r["token_id"]: r["condition_id"]
                  for r in conn.execute("SELECT token_id, condition_id FROM token_map")}
    mrows = conn.execute(
        "SELECT condition_id, resolved, end_ts FROM markets").fetchall()
    m_resolved = {r["condition_id"]: bool(r["resolved"]) for r in mrows}
    m_end_ts = {r["condition_id"]: r["end_ts"] for r in mrows}

    # 每市场聚合: cash_net / buy_volume / n_fills / first_buy_ts
    per_market = {}

    def mk(cid):
        return per_market.setdefault(cid, {
            "cash_net": 0.0, "buy_volume": 0.0, "n_fills": 0, "first_buy_ts": None})

    buy_vol = sell_vol = 0.0
    buy_cost_sum = 0.0   # 买入加权均价用：sum(usdc)，权重即买入 token 数
    buy_qty_sum = 0.0
    late_buy_vol = 0.0
    entry_leads = []     # 每市场：首次买入距市场截止的小时数

    # 注意：地址级现金流必须统计【全部】成交，与 token 是否映射到市场无关；
    # 市场级（per_market）仅对已映射的部分做拆解，用于胜率和证据展示。
    # 否则映射缺口会造成买入被漏计、PnL 严重失真。
    fills = conn.execute(
        "SELECT * FROM fills WHERE maker=? ORDER BY block_number, log_index", (addr,)).fetchall()
    for f in fills:
        if f["maker_asset_id"] == "0":
            # 买入：付出 USDC，得到 token（fee 从收到的 token 里扣，对现金流无影响）
            usdc, qty, token = f["maker_amount"], f["taker_amount"], f["taker_asset_id"]
            cid = token_cond.get(token)
            buy_vol += usdc
            buy_cost_sum += usdc
            buy_qty_sum += qty
            if cid:
                m = mk(cid)
                m["cash_net"] -= usdc
                m["buy_volume"] += usdc
                m["n_fills"] += 1
                if m["first_buy_ts"] is None and f["ts"]:
                    m["first_buy_ts"] = f["ts"]
                end_ts = m_end_ts.get(cid)
                if end_ts and f["ts"] and end_ts - f["ts"] <= LATE_WINDOW_H * 3600:
                    late_buy_vol += usdc
        else:
            # 卖出：付出 token，收到 USDC 减 fee
            usdc = f["taker_amount"] - f["fee"]
            token = f["maker_asset_id"]
            cid = token_cond.get(token)
            sell_vol += usdc
            if cid:
                m = mk(cid)
                m["cash_net"] += usdc
                m["n_fills"] += 1

    extra_cash = 0.0  # 不挂在具体市场上的收入：做市奖励、maker 返佣
    ctf_cash = 0.0    # split/merge/redeem 的现金流净额（地址级，与市场映射无关）
    for e in conn.execute(
            "SELECT * FROM ctf_events WHERE address=? AND parent_zero=1", (addr,)):
        if e["kind"] in ("reward", "rebate") or not e["condition_id"]:
            extra_cash += e["amount"]
            continue
        delta = -e["amount"] if e["kind"] == "split" else e["amount"]
        ctf_cash += delta
        m = mk(e["condition_id"])
        m["cash_net"] += delta
        m["n_fills"] += 1

    # 地址级现金流：必须用全量口径（含未映射到市场的成交），
    # per_market 只做已映射部分的拆解（胜率/证据），两者分开算
    cash_net = extra_cash + ctf_cash + sell_vol - buy_vol

    # 市场级统计
    n_resolved = wins = 0
    realized = extra_cash
    for cid, m in per_market.items():
        resolved = m_resolved.get(cid, False)
        if resolved:
            n_resolved += 1
            realized += m["cash_net"]
            if m["cash_net"] > 0:
                wins += 1
        end_ts = m_end_ts.get(cid)
        if end_ts and m["first_buy_ts"]:
            entry_leads.append((end_ts - m["first_buy_ts"]) / 3600.0)

    position_value = _refresh_positions(cfg, conn, addr) if refresh_positions else 0.0
    official = _official_pnl(conn, addr)
    total_pnl = cash_net + position_value

    stats = {
        "address": addr,
        "computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "n_fills": len(fills),
        "n_markets": len(per_market),
        "n_resolved": n_resolved,
        "wins": wins,
        "win_rate": wins / n_resolved if n_resolved else None,
        "buy_volume": buy_vol,
        "sell_volume": sell_vol,
        "cash_net": cash_net,
        "position_value": position_value,
        "total_pnl": total_pnl,
        "realized_pnl": realized,
        "avg_entry_price": buy_cost_sum / buy_qty_sum if buy_qty_sum else None,
        "median_entry_lead_h": statistics.median(entry_leads) if entry_leads else None,
        "late_buy_share": late_buy_vol / buy_vol if buy_vol else None,
        "official_pnl": official,
        "pnl_diff": (total_pnl - official) if official is not None else None,
    }
    cols = ", ".join(stats.keys())
    ph = ", ".join("?" * len(stats))
    conn.execute(f"INSERT OR REPLACE INTO address_stats({cols}) VALUES({ph})",
                 tuple(stats.values()))

    conn.execute("DELETE FROM address_market_pnl WHERE address=?", (addr,))
    for cid, m in per_market.items():
        resolved = m_resolved.get(cid, False)
        conn.execute(
            "INSERT INTO address_market_pnl(address, condition_id, cash_net, buy_volume,"
            " n_fills, first_buy_ts, resolved, won) VALUES(?,?,?,?,?,?,?,?)",
            (addr, cid, m["cash_net"], m["buy_volume"], m["n_fills"], m["first_buy_ts"],
             int(resolved), int(resolved and m["cash_net"] > 0)))
    conn.commit()
    return stats


def run_pnl(cfg: dict, refresh_positions: bool = True) -> int:
    """对所有有链上数据的候选地址计算收益。返回处理的地址数。"""
    conn = connect(cfg)
    addrs = [r["address"] for r in conn.execute(
        "SELECT c.address FROM candidates c"
        " WHERE EXISTS(SELECT 1 FROM fills f WHERE f.maker=c.address)"
        "    OR EXISTS(SELECT 1 FROM ctf_events e WHERE e.address=c.address)")]
    log.info("有链上数据的地址: %d 个", len(addrs))
    for i, a in enumerate(addrs, 1):
        try:
            s = compute_address(cfg, conn, a, refresh_positions=refresh_positions)
            log.info("[%d/%d] %s total_pnl=%.0f 胜率=%s",
                     i, len(addrs), a[:10],
                     s["total_pnl"],
                     f"{s['win_rate']:.0%}" if s["win_rate"] is not None else "-")
        except Exception as e:  # noqa: BLE001 单地址失败不拖垮整批
            log.warning("地址 %s 计算失败: %s", a, e)
    conn.close()
    return len(addrs)
