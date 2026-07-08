"""链上抽样校验：随机抽取 Data API 采集的成交，用交易回执核对。

eth_getTransactionReceipt 不受 Alchemy 免费档 getLogs 的 10 块范围限制，
可以逐笔核对：回执里确实存在该地址作为 maker 的 OrderFilled 事件。
"""
import random

from web3 import Web3

from .db import connect
from .ingest import SIG_ORDER_FILLED, SIG_ORDER_FILLED_V2, _topic_of, _poa_middleware
from .util import RateLimiter, setup_logging

log = setup_logging()


def run_verify(cfg: dict, sample: int = 20) -> bool:
    conn = connect(cfg)
    rows = conn.execute(
        "SELECT tx_hash, maker FROM fills WHERE exchange='api' AND tx_hash != ''"
    ).fetchall()
    if not rows:
        log.info("没有可校验的 API 成交记录")
        return True
    picks = random.sample(rows, min(sample, len(rows)))

    w3 = Web3(Web3.HTTPProvider(cfg["rpc"]["url"], request_kwargs={"timeout": 30}))
    w3.middleware_onion.inject(_poa_middleware, layer=0)
    limiter = RateLimiter(cfg["rpc"]["min_interval_ms"])
    topics = {_topic_of(SIG_ORDER_FILLED), _topic_of(SIG_ORDER_FILLED_V2)}

    ok = bad = 0
    for r in picks:
        limiter.wait()
        try:
            receipt = w3.eth.get_transaction_receipt(r["tx_hash"])
        except Exception as e:  # noqa: BLE001
            log.warning("回执获取失败 %s: %.60s", r["tx_hash"][:18], e)
            bad += 1
            continue
        found = False
        for lg in receipt["logs"]:
            lg_topics = ["0x" + t.hex() if not t.hex().startswith("0x") else t.hex()
                         for t in lg["topics"]]
            if lg_topics and lg_topics[0] in topics and len(lg_topics) >= 3 \
                    and lg_topics[2][-40:].lower() == r["maker"][-40:].lower():
                found = True
                break
        if found:
            ok += 1
        else:
            bad += 1
            log.warning("校验失败：%s 的回执里没有 maker=%s 的 OrderFilled",
                        r["tx_hash"][:18], r["maker"][:10])
    log.info("抽样校验 %d 笔：通过 %d，失败 %d", len(picks), ok, bad)
    conn.close()
    return bad == 0
