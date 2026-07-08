"""种子模块：从官方排行榜 API 拉候选地址。

排行榜端点: GET {data_api}/v1/leaderboard
单页上限 50，用 offset 翻页；offset 实际能翻多深由 API 决定，
翻到空页或重复数据即停止。
"""
import datetime

from .db import connect
from .util import http_get_json, setup_logging

log = setup_logging()


def _fetch_board(base_url: str, category: str, period: str, order_by: str,
                 page_size: int, max_entries: int) -> list:
    """翻页拉取单个榜单，返回 entry 列表。"""
    entries = []
    seen = set()
    offset = 0
    while offset < max_entries:
        params = {
            "category": category,
            "timePeriod": period,
            "orderBy": order_by,
            "limit": page_size,
            "offset": offset,
        }
        data = http_get_json(f"{base_url}/v1/leaderboard", params=params)
        if isinstance(data, dict):
            # 兼容可能的包装结构
            data = data.get("leaderboard") or data.get("data") or []
        if not data:
            break
        new = 0
        for e in data:
            addr = (e.get("proxyWallet") or "").lower()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            new += 1
            entries.append(e)
        if new == 0:  # API 到底了，开始返回重复数据
            break
        offset += page_size
    return entries


def run_seed(cfg: dict) -> int:
    """拉全部配置的榜单，写入 candidates 和 leaderboard_snapshots。返回候选地址总数。"""
    conn = connect(cfg)
    scfg = cfg["seed"]
    base = cfg["data_api"]["base_url"]
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    total_new = 0
    for category in scfg["categories"]:
        for period in scfg["time_periods"]:
            entries = _fetch_board(
                base, category, period, scfg["order_by"],
                scfg["page_size"], scfg["max_per_board"],
            )
            log.info("榜单 %s/%s: 拉到 %d 个地址", category, period, len(entries))
            for e in entries:
                addr = (e.get("proxyWallet") or "").lower()
                cur = conn.execute(
                    "INSERT INTO candidates(address, username, added_at) VALUES(?,?,?) "
                    "ON CONFLICT(address) DO UPDATE SET username=COALESCE(excluded.username, username)",
                    (addr, e.get("userName"), now),
                )
                total_new += cur.rowcount
                try:
                    rank = int(e.get("rank") or 0)
                except (TypeError, ValueError):
                    rank = None
                conn.execute(
                    "INSERT OR REPLACE INTO leaderboard_snapshots"
                    "(address, category, time_period, rank, pnl, vol, fetched_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (addr, category, period, rank, e.get("pnl"), e.get("vol"), today),
                )
            conn.commit()

    n = conn.execute("SELECT COUNT(*) c FROM candidates").fetchone()["c"]
    log.info("候选地址总数: %d", n)
    conn.close()
    return n
