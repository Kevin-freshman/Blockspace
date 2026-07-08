"""配置加载：config.yaml + 默认值合并。"""
import os

import yaml

DEFAULTS = {
    "rpc": {
        "url": "https://polygon.drpc.org",
        "min_interval_ms": 150,
        "max_retries": 5,
        "confirmations": 30,
    },
    "seed": {
        "categories": ["OVERALL", "CRYPTO"],
        "time_periods": ["MONTH", "ALL"],
        "order_by": "PNL",
        "page_size": 50,
        "max_per_board": 500,
    },
    "ingest": {
        "backfill_days": 180,
        "chunk_size": 20000,
        "min_chunk_size": 50,
        "address_batch_size": 40,
        "track_taker_side": False,
    },
    "clob": {
        "base_url": "https://clob.polymarket.com",
        "min_interval_ms": 120,
    },
    "data_api": {
        "base_url": "https://data-api.polymarket.com",
        "min_interval_ms": 150,
        "max_activity_pages_per_address": 400,
        "positions_page_size": 500,
        "max_positions_pages": 10,
    },
    "tracker": {
        "poll_interval_minutes": 30,
        "seed_refresh_hours": 24,
    },
    "profile": {
        "top_detail": 20,
    },
    "storage": {
        "db_path": "data/smartmoney.db",
        "output_dir": "output",
    },
    "contracts": {
        "ctf_exchange": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        "neg_risk_ctf_exchange": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
        "ctf_exchange_v2": "0xE111180000d2663C0091e4f400237545B87B996B",
        "neg_risk_ctf_exchange_v2": "0xe2222d279d744050d28e00520010520000310F59",
        "conditional_tokens": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        "usdc": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "pusd": "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str = None) -> dict:
    """加载 config.yaml，缺失的键用默认值补齐。相对路径基于项目根目录。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = path or os.path.join(root, "config.yaml")
    user_cfg = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, user_cfg)
    cfg["_root"] = root

    # 相对路径转成绝对路径
    st = cfg["storage"]
    for key in ("db_path", "output_dir"):
        if not os.path.isabs(st[key]):
            st[key] = os.path.join(root, st[key])
    return cfg
