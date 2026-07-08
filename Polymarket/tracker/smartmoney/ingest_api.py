"""Data API 采集模块（混合方案的主数据源）。

用官方 Data API 的 /activity 端点拉每个地址的完整账户活动
（TRADE / SPLIT / MERGE / REDEEM / REWARD / MAKER_REBATE...），
写入与链上采集相同的 fills / ctf_events 表，收益引擎无需感知数据来源。

为什么不用链上：Alchemy 免费档 eth_getLogs 限制 10 块/次，公共节点更差；
Data API 免费、免鉴权、按地址直查。每条记录仍带 transactionHash 和 timestamp，
可随时用链上抽样校验（见 verify 命令）。

分页策略：offset 翻页在 >3000 条时会截断（社区实测），
所以按时间窗口翻页：sortDirection=DESC，每页翻完把 end 参数移到本页最旧时间戳，
重叠部分靠去重键（tx_hash + 记录内容哈希的合成 log_index）幂等。

增量策略：api_sync 表记录每地址已同步到的时间戳，DESC 翻页翻到旧数据即停。
"""
import datetime
import hashlib
import time

from .db import connect
from .util import RateLimiter, http_get_json, setup_logging

log = setup_logging()

PAGE_LIMIT = 500
# 一个 tx 里可能有多条记录，Data API 没有 log_index，
# 用记录内容哈希合成一个稳定的伪 log_index 做去重
def _pseudo_log_index(rec: dict) -> int:
    key = "|".join(str(rec.get(k, "")) for k in
                   ("transactionHash", "type", "asset", "conditionId", "side",
                    "size", "usdcSize", "price", "timestamp", "outcomeIndex"))
    return int(hashlib.sha1(key.encode()).hexdigest()[:12], 16)


def _store_record(conn, addr: str, rec: dict) -> bool:
    """把一条 activity 记录写入 fills 或 ctf_events。返回是否新插入。"""
    rtype = (rec.get("type") or "").upper()
    tx = rec.get("transactionHash") or ""
    ts = int(rec.get("timestamp") or 0)
    usdc = float(rec.get("usdcSize") or 0)
    size = float(rec.get("size") or 0)
    idx = _pseudo_log_index(rec)

    if rtype == "TRADE":
        side = (rec.get("side") or "").upper()
        asset = str(rec.get("asset") or "")
        if not asset or side not in ("BUY", "SELL"):
            return False
        if side == "BUY":
            maker_asset, taker_asset = "0", asset
            maker_amt, taker_amt = usdc, size
        else:
            maker_asset, taker_asset = asset, "0"
            maker_amt, taker_amt = size, usdc
        cur = conn.execute(
            "INSERT OR IGNORE INTO fills(tx_hash, log_index, block_number, ts, exchange,"
            " order_hash, maker, taker, maker_asset_id, taker_asset_id, maker_amount,"
            " taker_amount, fee) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tx, idx, 0, ts, "api", None, addr, None, maker_asset, taker_asset,
             maker_amt, taker_amt, 0.0))
        # activity 记录自带 token -> 市场映射和标题，直接在源头建映射。
        # combo/parlay 市场（isCombo）用合成 conditionId，Gamma 查不到，
        # 但标题在这里就有，元数据模块只做补充增强。
        cid = (rec.get("conditionId") or "").lower()
        if cid:
            conn.execute(
                "INSERT OR IGNORE INTO token_map(token_id, condition_id, outcome,"
                " outcome_index) VALUES(?,?,?,?)",
                (asset, cid, rec.get("outcome") or None, rec.get("outcomeIndex")))
            title = rec.get("title") or None
            if title:
                conn.execute(
                    "INSERT OR IGNORE INTO markets(condition_id, question) VALUES(?,?)",
                    (cid, title))
        return cur.rowcount > 0

    kind = {"SPLIT": "split", "MERGE": "merge", "REDEEM": "redeem",
            "REWARD": "reward", "MAKER_REBATE": "rebate"}.get(rtype)
    if kind is None:  # CONVERSION 等暂不纳入现金流（与链上口径一致，见 README 局限）
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO ctf_events(tx_hash, log_index, block_number, ts, kind,"
        " address, condition_id, collateral, parent_zero, amount)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (tx, idx, 0, ts, kind, addr, (rec.get("conditionId") or "").lower(),
         "", 1, usdc))
    return cur.rowcount > 0


def _sync_address(cfg, conn, limiter, addr: str, cutoff_ts: int) -> int:
    """同步单个地址，返回新增记录数。"""
    base = cfg["data_api"]["base_url"]
    row = conn.execute("SELECT last_ts FROM api_sync WHERE address=?", (addr,)).fetchone()
    last_ts = row["last_ts"] if row else 0
    stop_ts = max(last_ts, cutoff_ts)

    max_pages = cfg["data_api"].get("max_activity_pages_per_address", 400)
    n_new = 0
    n_pages = 0
    newest_seen = last_ts
    end_ts = None            # 时间窗口翻页游标（None = 从最新开始）
    empty_guard = 0
    while True:
        if n_pages >= max_pages:
            log.info("%s 达到单地址翻页上限（%d 页），丢弃更早历史（高频地址）",
                     addr[:10], max_pages)
            break
        params = {"user": addr, "limit": PAGE_LIMIT,
                  "sortBy": "TIMESTAMP", "sortDirection": "DESC"}
        if end_ts is not None:
            params["end"] = end_ts
        limiter.wait()
        data = http_get_json(f"{base}/activity", params=params)
        n_pages += 1
        if not isinstance(data, list) or not data:
            break
        page_new = 0
        oldest = None
        for rec in data:
            ts = int(rec.get("timestamp") or 0)
            oldest = ts if oldest is None else min(oldest, ts)
            newest_seen = max(newest_seen, ts)
            if ts < stop_ts:
                continue
            if _store_record(conn, addr, rec):
                page_new += 1
        n_new += page_new
        if oldest is None or oldest < stop_ts:
            break               # 已翻到目标窗口之外
        if len(data) < PAGE_LIMIT:
            break               # 最后一页
        # 时间窗口前移；同一时间戳大量记录时防止死循环
        if end_ts is not None and oldest >= end_ts:
            empty_guard += 1
            if empty_guard >= 3:
                log.warning("%s 在 ts=%d 处翻页停滞，跳过剩余历史", addr[:10], oldest)
                break
            oldest -= 1
        else:
            empty_guard = 0
        end_ts = oldest
    conn.execute(
        "INSERT INTO api_sync(address, last_ts) VALUES(?,?) "
        "ON CONFLICT(address) DO UPDATE SET last_ts=excluded.last_ts",
        (addr, newest_seen))
    conn.commit()
    return n_new


def run_sync_api(cfg: dict) -> int:
    """对全部候选地址做 Data API 同步（历史回填 + 增量共用）。返回新增记录总数。"""
    conn = connect(cfg)
    conn.execute("CREATE TABLE IF NOT EXISTS api_sync("
                 "address TEXT PRIMARY KEY, last_ts INTEGER NOT NULL)")
    addrs = [r["address"] for r in conn.execute("SELECT address FROM candidates")]
    if not addrs:
        raise RuntimeError("candidates 表为空，先运行 seed")
    cutoff = int(time.time()) - cfg["ingest"]["backfill_days"] * 86400
    limiter = RateLimiter(cfg["data_api"].get("min_interval_ms", 150))
    log.info("Data API 同步 %d 个地址（窗口 %s 天）", len(addrs),
             cfg["ingest"]["backfill_days"])

    total = 0
    t0 = time.time()
    for i, addr in enumerate(addrs, 1):
        try:
            n = _sync_address(cfg, conn, limiter, addr, cutoff)
            total += n
        except Exception as e:  # noqa: BLE001 单地址失败不拖垮整批
            log.warning("地址 %s 同步失败: %s", addr, e)
            continue
        if i % 10 == 0 or i == len(addrs):
            rate = i / (time.time() - t0) * 60
            eta = (len(addrs) - i) / max(rate, 1e-9)
            log.info("进度 %d/%d（新增 %d 条，约 %.1f 地址/分，剩余约 %.0f 分钟）",
                     i, len(addrs), total, rate, eta)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log.info("Data API 同步完成 @ %s：新增 %d 条", ts, total)
    conn.close()
    return total
