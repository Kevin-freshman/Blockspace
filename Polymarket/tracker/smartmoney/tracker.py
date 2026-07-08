"""追踪循环：定时增量同步 -> 更新元数据 -> 重算收益 -> 刷新画像表。"""
import datetime
import time

from .ingest_api import run_sync_api
from .markets import run_markets
from .pnl import run_pnl
from .profile import run_profile
from .seed import run_seed
from .util import setup_logging

log = setup_logging()


def run_loop(cfg: dict):
    interval = cfg["tracker"]["poll_interval_minutes"] * 60
    seed_every = cfg["tracker"]["seed_refresh_hours"] * 3600
    last_seed = 0.0

    log.info("追踪循环启动：每 %d 分钟一轮，Ctrl+C 停止", interval // 60)
    while True:
        cycle_start = time.time()
        try:
            if time.time() - last_seed >= seed_every:
                run_seed(cfg)
                last_seed = time.time()
            new_events = run_sync_api(cfg)
            run_markets(cfg)
            if new_events:
                run_pnl(cfg)
                run_profile(cfg)
            log.info("本轮完成：新增事件 %d 条，用时 %.0fs",
                     new_events, time.time() - cycle_start)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 循环不能因单轮失败退出
            log.error("本轮失败（下轮重试）: %s", e)
        sleep_left = max(0, interval - (time.time() - cycle_start))
        log.info("休眠 %.0f 分钟（%s 下一轮）", sleep_left / 60,
                 (datetime.datetime.now() + datetime.timedelta(seconds=sleep_left))
                 .strftime("%H:%M"))
        try:
            time.sleep(sleep_left)
        except KeyboardInterrupt:
            log.info("已停止")
            return
