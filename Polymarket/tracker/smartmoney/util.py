"""HTTP / 速率控制 / 重试等通用工具。"""
import logging
import sys
import time

import requests

log = logging.getLogger("smartmoney")


def setup_logging():
    if not log.handlers:
        # Windows 控制台默认 GBK，中文日志会乱码
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


class RateLimiter:
    """两次调用之间保证最小间隔。"""

    def __init__(self, min_interval_ms: int):
        self.min_interval = min_interval_ms / 1000.0
        self._last = 0.0

    def wait(self):
        now = time.monotonic()
        delta = now - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()


def http_get_json(url: str, params=None, max_retries: int = 5, timeout: int = 30):
    """带指数退避重试的 GET，返回解析后的 JSON。"""
    backoff = 1.0
    last_err = None
    for _ in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = RuntimeError(f"HTTP {resp.status_code} from {url}")
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout, ValueError) as e:
            last_err = e
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"GET {url} 重试耗尽: {last_err}")


def with_retries(fn, max_retries: int = 5, retriable=(Exception,), on_error=None):
    """通用重试包装（指数退避）。retriable 之外的异常直接抛出。"""
    backoff = 1.0
    last_err = None
    for _ in range(max_retries):
        try:
            return fn()
        except retriable as e:
            last_err = e
            if on_error and not on_error(e):
                raise
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"重试耗尽: {last_err}")
