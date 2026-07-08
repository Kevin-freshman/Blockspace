"""SQLite 存储层：schema 初始化与通用读写。"""
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    address     TEXT PRIMARY KEY,          -- proxy wallet，小写 0x
    username    TEXT,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
    address     TEXT NOT NULL,
    category    TEXT NOT NULL,
    time_period TEXT NOT NULL,
    rank        INTEGER,
    pnl         REAL,
    vol         REAL,
    fetched_at  TEXT NOT NULL,             -- ISO 日期（天级），同一天重复抓取覆盖
    PRIMARY KEY (address, category, time_period, fetched_at)
);

-- CTF Exchange / NegRisk CTF Exchange 的 OrderFilled 原始记录
CREATE TABLE IF NOT EXISTS fills (
    tx_hash        TEXT NOT NULL,
    log_index      INTEGER NOT NULL,
    block_number   INTEGER NOT NULL,
    ts             INTEGER,                -- unix 秒
    exchange       TEXT NOT NULL,          -- ctf / negrisk
    order_hash     TEXT,
    maker          TEXT NOT NULL,          -- 订单所有者（我们追踪的主体）
    taker          TEXT,
    maker_asset_id TEXT NOT NULL,          -- '0' = USDC，否则为 CTF token id（十进制字符串）
    taker_asset_id TEXT NOT NULL,
    maker_amount   REAL NOT NULL,          -- 已除以 1e6
    taker_amount   REAL NOT NULL,
    fee            REAL NOT NULL,
    PRIMARY KEY (tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_fills_maker ON fills(maker);

-- ConditionalTokens 的 split / merge / redeem
CREATE TABLE IF NOT EXISTS ctf_events (
    tx_hash      TEXT NOT NULL,
    log_index    INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    ts           INTEGER,
    kind         TEXT NOT NULL,            -- split / merge / redeem
    address      TEXT NOT NULL,            -- stakeholder / redeemer
    condition_id TEXT NOT NULL,            -- 0x 开头 hex
    collateral   TEXT,
    parent_zero  INTEGER NOT NULL,         -- parentCollectionId 是否为 0（只有为 0 才计入现金流）
    amount       REAL NOT NULL,            -- split/merge: amount, redeem: payout（已除以 1e6）
    PRIMARY KEY (tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_ctf_addr ON ctf_events(address);

CREATE TABLE IF NOT EXISTS markets (
    condition_id   TEXT PRIMARY KEY,
    question       TEXT,
    slug           TEXT,
    category       TEXT,
    end_date       TEXT,                   -- ISO
    end_ts         INTEGER,                -- unix 秒
    closed         INTEGER DEFAULT 0,
    resolved       INTEGER DEFAULT 0,
    winner_outcome TEXT,
    outcome_prices TEXT,
    clob_token_ids TEXT,
    neg_risk       INTEGER DEFAULT 0,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS token_map (
    token_id      TEXT PRIMARY KEY,        -- 十进制字符串
    condition_id  TEXT NOT NULL,
    outcome       TEXT,
    outcome_index INTEGER
);

CREATE TABLE IF NOT EXISTS sync_state (
    stream     TEXT PRIMARY KEY,
    last_block INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS block_times (
    block_number INTEGER PRIMARY KEY,
    ts           INTEGER NOT NULL
);

-- Data API 拉的当前持仓快照（算浮盈用）
CREATE TABLE IF NOT EXISTS positions_cache (
    address    TEXT NOT NULL,
    token_id   TEXT NOT NULL,
    size       REAL,
    cur_price  REAL,
    value      REAL,
    title      TEXT,
    fetched_at TEXT,
    PRIMARY KEY (address, token_id)
);

CREATE TABLE IF NOT EXISTS address_stats (
    address              TEXT PRIMARY KEY,
    computed_at          TEXT,
    n_fills              INTEGER,
    n_markets            INTEGER,
    n_resolved           INTEGER,
    wins                 INTEGER,
    win_rate             REAL,
    buy_volume           REAL,
    sell_volume          REAL,
    cash_net             REAL,              -- 全部现金流净额（不含持仓市值）
    position_value       REAL,              -- 当前持仓市值（Data API）
    total_pnl            REAL,              -- cash_net + position_value
    realized_pnl         REAL,              -- 仅已揭晓市场的现金流净额
    avg_entry_price      REAL,              -- 买入量加权均价
    median_entry_lead_h  REAL,              -- 首次买入距市场截止的中位提前小时数
    late_buy_share       REAL,              -- 截止前 24h 内买入量占比
    official_pnl         REAL,              -- 官方排行榜 ALL 窗口 pnl
    pnl_diff             REAL
);

-- 每个地址在每个市场上的现金流拆解（画像表证据用）
CREATE TABLE IF NOT EXISTS address_market_pnl (
    address      TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    cash_net     REAL,
    buy_volume   REAL,
    n_fills      INTEGER,
    first_buy_ts INTEGER,
    resolved     INTEGER,
    won          INTEGER,
    PRIMARY KEY (address, condition_id)
);
"""


def connect(cfg: dict) -> sqlite3.Connection:
    path = cfg["storage"]["db_path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn


def get_sync_state(conn, stream: str):
    row = conn.execute("SELECT last_block FROM sync_state WHERE stream=?", (stream,)).fetchone()
    return row["last_block"] if row else None


def set_sync_state(conn, stream: str, block: int):
    conn.execute(
        "INSERT INTO sync_state(stream, last_block) VALUES(?,?) "
        "ON CONFLICT(stream) DO UPDATE SET last_block=excluded.last_block",
        (stream, block),
    )
    conn.commit()


def candidate_addresses(conn) -> list:
    return [r["address"] for r in conn.execute("SELECT address FROM candidates ORDER BY address")]
