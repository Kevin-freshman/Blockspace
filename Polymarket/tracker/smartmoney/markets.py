"""市场元数据模块：补齐市场的截止时间、揭晓状态、胜出方、分类。

数据源：CLOB API `GET /markets/{condition_id}`（免鉴权）。
实测覆盖率优于 Gamma API——V2 市场、体育盘、combo 市场在 Gamma 上大量缺失，
CLOB 全部能查到，且直接带 winner 标记。

token -> condition 的基础映射在采集时就从 activity 记录写入 token_map，
这里只负责市场级信息的补齐与未揭晓市场的状态刷新。
"""
import datetime

import requests

from .db import connect
from .util import RateLimiter, http_get_json, setup_logging

log = setup_logging()

# 泛化 tag，不适合当类别
_GENERIC_TAGS = {"games", "sports", "all", "polymarket"}


def _category_from_tags(tags) -> str:
    if not tags:
        return None
    for t in tags:
        if str(t).lower() not in _GENERIC_TAGS:
            return str(t)
    return str(tags[0])


def _store_clob_market(conn, m: dict) -> bool:
    cid = (m.get("condition_id") or "").lower()
    if not cid:
        return False
    end_date = m.get("end_date_iso")
    end_ts = None
    if end_date:
        try:
            end_ts = int(datetime.datetime.fromisoformat(
                end_date.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    tokens = m.get("tokens") or []
    winner = next((t.get("outcome") for t in tokens if t.get("winner")), None)
    closed = bool(m.get("closed"))
    resolved = int(closed and winner is not None)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO markets(condition_id, question, slug, category, end_date, end_ts,"
        " closed, resolved, winner_outcome, neg_risk, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(condition_id) DO UPDATE SET"
        "  question=COALESCE(excluded.question, question),"
        "  slug=excluded.slug, category=excluded.category,"
        "  end_date=excluded.end_date, end_ts=excluded.end_ts,"
        "  closed=excluded.closed, resolved=excluded.resolved,"
        "  winner_outcome=excluded.winner_outcome,"
        "  neg_risk=excluded.neg_risk, updated_at=excluded.updated_at",
        (cid, m.get("question"), m.get("market_slug"),
         _category_from_tags(m.get("tags")), end_date, end_ts,
         int(closed), resolved, winner, int(bool(m.get("neg_risk"))), now))
    for i, t in enumerate(tokens):
        tid = str(t.get("token_id") or "")
        if tid:
            conn.execute(
                "INSERT OR IGNORE INTO token_map(token_id, condition_id, outcome,"
                " outcome_index) VALUES(?,?,?,?)",
                (tid, cid, t.get("outcome"), i))
    return True


def _pending_condition_ids(conn) -> list:
    """需要查询的市场：没有元数据的 + 尚未揭晓的（刷新状态）。"""
    rows = conn.execute("""
        SELECT DISTINCT cid FROM (
            SELECT condition_id AS cid FROM token_map
            UNION SELECT condition_id FROM ctf_events WHERE condition_id != ''
        )
        WHERE cid NOT IN (SELECT condition_id FROM markets WHERE resolved=1)
    """).fetchall()
    return [r["cid"] for r in rows]


def run_markets(cfg: dict) -> int:
    conn = connect(cfg)
    base = cfg.get("clob", {}).get("base_url", "https://clob.polymarket.com")
    limiter = RateLimiter(cfg.get("clob", {}).get("min_interval_ms", 120))

    cids = _pending_condition_ids(conn)
    log.info("待查/待刷新市场: %d 个", len(cids))
    updated = missed = 0
    for i, cid in enumerate(cids, 1):
        limiter.wait()
        try:
            m = http_get_json(f"{base}/markets/{cid}", max_retries=3)
        except (RuntimeError, requests.HTTPError):
            # 404 多为 combo/parlay 合成市场，CLOB 上不存在；标题已在采集时入库
            missed += 1
            continue
        if isinstance(m, dict) and _store_clob_market(conn, m):
            updated += 1
        else:
            missed += 1
        if i % 200 == 0 or i == len(cids):
            conn.commit()
            log.info("市场元数据进度 %d/%d（成功 %d，未找到 %d）",
                     i, len(cids), updated, missed)
    conn.commit()
    log.info("市场元数据完成：更新 %d，未找到 %d", updated, missed)
    conn.close()
    return updated
