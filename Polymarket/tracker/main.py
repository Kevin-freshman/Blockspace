"""Polymarket 聪明钱追踪器 CLI。

用法：
  python main.py seed              # 拉排行榜候选地址
  python main.py sync              # Data API 回填/增量同步（主数据源）
  python main.py verify            # 链上抽样校验 API 数据（需要 RPC）
  python main.py sync-chain [--reset]  # 纯链上同步（需要付费档 RPC，备用）
  python main.py probe             # RPC 连通性与事件 topic 自检
  python main.py markets           # 补齐/刷新市场元数据
  python main.py pnl [--no-positions]  # 现金流法算收益
  python main.py profile           # 生成画像表 output/profiles.md
  python main.py run               # 追踪循环（seed+sync+pnl+profile 定时跑）
  python main.py status            # 数据库统计
"""
import argparse

from smartmoney.config import load_config
from smartmoney.util import setup_logging

log = setup_logging()


def cmd_status(cfg):
    from smartmoney.db import connect
    conn = connect(cfg)
    for table in ("candidates", "fills", "ctf_events", "markets", "token_map",
                  "address_stats", "positions_cache"):
        n = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        print(f"{table:20s} {n:>10,}")
    for r in conn.execute("SELECT stream, last_block FROM sync_state"):
        print(f"sync[{r['stream']}] -> block {r['last_block']:,}")
    conn.close()


def main():
    p = argparse.ArgumentParser(description="Polymarket 聪明钱追踪器")
    p.add_argument("--config", default=None, help="config.yaml 路径")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    sub.add_parser("probe")
    sub.add_parser("sync")
    vp = sub.add_parser("verify")
    vp.add_argument("--sample", type=int, default=20, help="抽样笔数")
    sp = sub.add_parser("sync-chain")
    sp.add_argument("--reset", action="store_true", help="忽略断点，从回填起点重扫")
    sub.add_parser("markets")
    pp = sub.add_parser("pnl")
    pp.add_argument("--no-positions", action="store_true", help="跳过 Data API 持仓刷新")
    sub.add_parser("profile")
    sub.add_parser("run")
    sub.add_parser("status")
    args = p.parse_args()

    cfg = load_config(args.config)

    if args.cmd == "seed":
        from smartmoney.seed import run_seed
        run_seed(cfg)
    elif args.cmd == "probe":
        from smartmoney.ingest import run_probe
        run_probe(cfg)
    elif args.cmd == "sync":
        from smartmoney.ingest_api import run_sync_api
        run_sync_api(cfg)
    elif args.cmd == "verify":
        from smartmoney.verify import run_verify
        run_verify(cfg, sample=args.sample)
    elif args.cmd == "sync-chain":
        from smartmoney.ingest import run_sync
        print(run_sync(cfg, reset=args.reset))
    elif args.cmd == "markets":
        from smartmoney.markets import run_markets
        run_markets(cfg)
    elif args.cmd == "pnl":
        from smartmoney.pnl import run_pnl
        run_pnl(cfg, refresh_positions=not args.no_positions)
    elif args.cmd == "profile":
        from smartmoney.profile import run_profile
        print(run_profile(cfg))
    elif args.cmd == "run":
        from smartmoney.tracker import run_loop
        run_loop(cfg)
    elif args.cmd == "status":
        cmd_status(cfg)


if __name__ == "__main__":
    main()
