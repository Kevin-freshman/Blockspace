"""链上采集模块：eth_getLogs 分块拉取候选地址的成交与 CTF 事件。

事件流（stream）：
- fills_maker   : CTF Exchange + NegRisk CTF Exchange 的 OrderFilled，按 maker topic 过滤
- fills_taker   : 同上按 taker topic 过滤（可选，默认关；PnL 只用 maker 侧）
- splits        : ConditionalTokens.PositionSplit，按 stakeholder 过滤
- merges        : ConditionalTokens.PositionsMerge
- redemptions   : ConditionalTokens.PayoutRedemption，按 redeemer 过滤

设计要点：
- 分块查询，遇到"结果太多/范围太大"类错误自动把分块减半（最低 min_chunk_size）
- sync_state 表记录每个 stream 已同步到的区块，断点续传；回填与增量共用此逻辑
- 每条记录保留 tx_hash / block_number / timestamp / log_index，主键 (tx_hash, log_index) 去重
- 区块时间戳单独缓存在 block_times 表，避免重复请求
"""
import datetime
import time

from eth_abi import decode as abi_decode
from web3 import Web3

try:  # web3 v7
    from web3.middleware import ExtraDataToPOAMiddleware as _poa_middleware
except ImportError:  # web3 v6
    from web3.middleware import geth_poa_middleware as _poa_middleware

from .db import candidate_addresses, connect, get_sync_state, set_sync_state
from .util import RateLimiter, setup_logging

log = setup_logging()

# V1 交易所（2026-04-28 迁移前）
SIG_ORDER_FILLED = "OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)"
# V2 交易所（CLOB V2）：side 枚举 + 单一 tokenId + builder/metadata
SIG_ORDER_FILLED_V2 = "OrderFilled(bytes32,address,address,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32)"
SIG_SPLIT = "PositionSplit(address,address,bytes32,bytes32,uint256[],uint256)"
SIG_MERGE = "PositionsMerge(address,address,bytes32,bytes32,uint256[],uint256)"
SIG_REDEEM = "PayoutRedemption(address,address,bytes32,bytes32,uint256[],uint256)"

USDC_SCALE = 1e6


def _topic_of(sig: str) -> str:
    h = Web3.keccak(text=sig).hex()
    return h if h.startswith("0x") else "0x" + h


def _addr_topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


def _hex(v) -> str:
    if hasattr(v, "hex"):
        h = v.hex()
        return h if h.startswith("0x") else "0x" + h
    return str(v)


class ChainIngester:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.conn = connect(cfg)
        rpc = cfg["rpc"]
        self.w3 = Web3(Web3.HTTPProvider(rpc["url"], request_kwargs={"timeout": 60}))
        # Polygon 是 POA 链，区块头 extraData 超长，需要该中间件才能读区块
        self.w3.middleware_onion.inject(_poa_middleware, layer=0)
        self.limiter = RateLimiter(rpc["min_interval_ms"])
        self.max_retries = rpc["max_retries"]
        self.confirmations = rpc["confirmations"]
        icfg = cfg["ingest"]
        self.chunk_size = icfg["chunk_size"]
        self.min_chunk = icfg["min_chunk_size"]
        self.addr_batch = icfg["address_batch_size"]
        self.track_taker = icfg["track_taker_side"]
        c = cfg["contracts"]
        self.exchanges = {
            Web3.to_checksum_address(c["ctf_exchange"]): "ctf",
            Web3.to_checksum_address(c["neg_risk_ctf_exchange"]): "negrisk",
            Web3.to_checksum_address(c["ctf_exchange_v2"]): "ctf_v2",
            Web3.to_checksum_address(c["neg_risk_ctf_exchange_v2"]): "negrisk_v2",
        }
        self.conditional_tokens = Web3.to_checksum_address(c["conditional_tokens"])
        # 现金流认可的抵押品：USDC.e（V1）与 pUSD（V2），都是 6 位小数、1:1 锚定美元
        self.collaterals = {c["usdc"].lower(), c["pusd"].lower()}
        self.topic_order_filled = _topic_of(SIG_ORDER_FILLED)
        self.topic_order_filled_v2 = _topic_of(SIG_ORDER_FILLED_V2)
        self.topic_split = _topic_of(SIG_SPLIT)
        self.topic_merge = _topic_of(SIG_MERGE)
        self.topic_redeem = _topic_of(SIG_REDEEM)

    # ---------- 基础 RPC ----------

    def _rpc(self, fn, retries: int = None):
        """限速 + 指数退避重试。"""
        backoff = 1.0
        last = None
        for _ in range(retries or self.max_retries):
            self.limiter.wait()
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 网络/节点错误种类繁杂
                last = e
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError(f"RPC 重试耗尽: {last}")

    def latest_safe_block(self) -> int:
        return self._rpc(lambda: self.w3.eth.block_number) - self.confirmations

    def block_ts(self, block_number: int) -> int:
        row = self.conn.execute(
            "SELECT ts FROM block_times WHERE block_number=?", (block_number,)).fetchone()
        if row:
            return row["ts"]
        ts = self._rpc(lambda: self.w3.eth.get_block(block_number)["timestamp"])
        self.conn.execute(
            "INSERT OR IGNORE INTO block_times(block_number, ts) VALUES(?,?)", (block_number, ts))
        return ts

    def find_block_by_time(self, target_ts: int) -> int:
        """二分查找时间戳对应的区块号（回填起点用）。"""
        lo, hi = 1, self._rpc(lambda: self.w3.eth.block_number)
        while lo < hi:
            mid = (lo + hi) // 2
            ts = self._rpc(lambda: self.w3.eth.get_block(mid)["timestamp"])
            if ts < target_ts:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _get_logs_adaptive(self, base_filter: dict, from_block: int, to_block: int):
        """自适应分块的 eth_getLogs。生成 (logs, chunk_end)。

        分块大小在实例上记忆（self.chunk_size）：一旦某个节点探明了可用范围，
        后续所有查询直接用这个大小，不再反复试错。
        """
        cur = from_block
        while cur <= to_block:
            span = min(self.chunk_size, to_block - cur + 1)
            end = cur + span - 1
            params = dict(base_filter, fromBlock=cur, toBlock=end)
            try:
                # 降块试探期间少重试（2 次），降块本身就是重试机制
                logs = self._rpc(lambda p=params: self.w3.eth.get_logs(p), retries=2)
            except RuntimeError as e:
                # 各家节点的超限错误五花八门（403/400/-32000...），
                # 只要还能降分块就先降（按实际请求跨度减半），降到底仍失败才报错
                if span > self.min_chunk:
                    self.chunk_size = max(self.min_chunk, span // 2)
                    log.info("getLogs 失败（%.60s），分块降到 %d", e, self.chunk_size)
                    continue
                raise
            yield logs, end
            cur = end + 1

    # ---------- 各 stream 的解析入库 ----------

    def _store_fill(self, lg):
        topics = lg["topics"]
        maker = "0x" + _hex(topics[2])[-40:]
        taker = "0x" + _hex(topics[3])[-40:]
        data = bytes(lg["data"])
        exchange = self.exchanges.get(Web3.to_checksum_address(lg["address"]), "?")
        if _hex(topics[0]) == self.topic_order_filled_v2:
            # V2: side 显式给出，归一化成 V1 的 asset_id 语义（'0' = 现金）
            side, token_id, maker_amt, taker_amt, fee, _builder, _meta = abi_decode(
                ["uint8", "uint256", "uint256", "uint256", "uint256", "bytes32", "bytes32"],
                data)
            if side == 0:  # BUY: 付现金收 token
                maker_asset, taker_asset = 0, token_id
            else:          # SELL: 付 token 收现金
                maker_asset, taker_asset = token_id, 0
        else:
            maker_asset, taker_asset, maker_amt, taker_amt, fee = abi_decode(
                ["uint256"] * 5, data)
        ts = self.block_ts(lg["blockNumber"])
        self.conn.execute(
            "INSERT OR IGNORE INTO fills(tx_hash, log_index, block_number, ts, exchange,"
            " order_hash, maker, taker, maker_asset_id, taker_asset_id, maker_amount,"
            " taker_amount, fee) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_hex(lg["transactionHash"]), lg["logIndex"], lg["blockNumber"], ts, exchange,
             _hex(topics[1]), maker, taker, str(maker_asset), str(taker_asset),
             maker_amt / USDC_SCALE, taker_amt / USDC_SCALE, fee / USDC_SCALE),
        )

    def _store_ctf(self, lg, kind: str):
        topics = lg["topics"]
        addr = "0x" + _hex(topics[1])[-40:]
        data = bytes(lg["data"])
        if kind in ("split", "merge"):
            # data: collateralToken(address), partition(uint256[]), amount
            collateral, _partition, amount = abi_decode(
                ["address", "uint256[]", "uint256"], data)
            parent = _hex(topics[2])
            condition_id = _hex(topics[3])
        else:  # redeem — indexed: redeemer, collateralToken, parentCollectionId
            condition_bytes, _index_sets, amount = abi_decode(
                ["bytes32", "uint256[]", "uint256"], data)
            collateral = "0x" + _hex(topics[2])[-40:]
            parent = _hex(topics[3])
            condition_id = "0x" + condition_bytes.hex()
        parent_zero = int(int(parent, 16) == 0)
        if collateral and collateral.lower() not in self.collaterals:
            # 只认 USDC.e / pUSD 抵押品（都是 1 美元锚定、6 位小数）
            return
        ts = self.block_ts(lg["blockNumber"])
        self.conn.execute(
            "INSERT OR IGNORE INTO ctf_events(tx_hash, log_index, block_number, ts, kind,"
            " address, condition_id, collateral, parent_zero, amount)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_hex(lg["transactionHash"]), lg["logIndex"], lg["blockNumber"], ts, kind,
             addr.lower(), condition_id.lower(), (collateral or "").lower(), parent_zero,
             amount / USDC_SCALE),
        )

    # ---------- 同步主流程 ----------

    def _streams(self, addresses: list) -> list:
        """返回 (stream 名, filter 模板列表)。filter 里的地址批在这里展开。"""
        addr_topics = [_addr_topic(a) for a in addresses]
        batches = [addr_topics[i:i + self.addr_batch]
                   for i in range(0, len(addr_topics), self.addr_batch)]
        exchange_addrs = list(self.exchanges.keys())
        streams = []

        def add(name, contract, topics_fn, handler):
            filters = [{"address": contract, "topics": topics_fn(b)} for b in batches]
            streams.append((name, filters, handler))

        # V1/V2 的 OrderFilled topic 不同，一次查询同时匹配两代交易所
        both_fills = [self.topic_order_filled, self.topic_order_filled_v2]
        add("fills_maker", exchange_addrs,
            lambda b: [both_fills, None, b],
            self._store_fill)
        if self.track_taker:
            add("fills_taker", exchange_addrs,
                lambda b: [both_fills, None, None, b],
                self._store_fill)
        add("splits", self.conditional_tokens,
            lambda b: [self.topic_split, b], lambda lg: self._store_ctf(lg, "split"))
        add("merges", self.conditional_tokens,
            lambda b: [self.topic_merge, b], lambda lg: self._store_ctf(lg, "merge"))
        add("redemptions", self.conditional_tokens,
            lambda b: [self.topic_redeem, b], lambda lg: self._store_ctf(lg, "redeem"))
        return streams

    def default_start_block(self) -> int:
        days = self.cfg["ingest"]["backfill_days"]
        target = int(time.time()) - days * 86400
        log.info("二分查找 %d 天前的区块号...", days)
        blk = self.find_block_by_time(target)
        log.info("回填起点区块: %d", blk)
        return blk

    def sync(self, reset: bool = False) -> dict:
        """把所有 stream 同步到最新安全区块。reset=True 时从回填起点重扫（幂等去重）。"""
        addresses = candidate_addresses(self.conn)
        if not addresses:
            raise RuntimeError("candidates 表为空，先运行 seed")
        log.info("追踪地址数: %d", len(addresses))

        target = self.latest_safe_block()
        start_default = None
        counts = {}
        for name, filters, handler in self._streams(addresses):
            last = None if reset else get_sync_state(self.conn, name)
            if last is None:
                if start_default is None:
                    start_default = self.default_start_block()
                start = start_default
            else:
                start = last + 1
            if start > target:
                counts[name] = 0
                continue
            n = 0
            log.info("[%s] 同步区块 %d -> %d（地址批 %d 个）",
                     name, start, target, len(filters))
            # 多个地址批共享区块窗口推进：一个窗口内跑完所有地址批再落 sync_state，
            # 保证断点续传时不会漏掉某个地址批
            cur = start
            last_report = start
            while cur <= target:
                end = min(cur + self.chunk_size - 1, target)
                for f in filters:
                    for logs, _ in self._get_logs_adaptive(f, cur, end):
                        for lg in logs:
                            handler(lg)
                            n += 1
                self.conn.commit()
                set_sync_state(self.conn, name, end)
                if end - last_report >= 100000 or end == target:
                    log.info("[%s] 已到区块 %d / %d，累计 %d 条", name, end, target, n)
                    last_report = end
                cur = end + 1
            counts[name] = n
            log.info("[%s] 完成，新增 %d 条", name, n)
        return counts


def run_sync(cfg: dict, reset: bool = False) -> dict:
    ing = ChainIngester(cfg)
    try:
        return ing.sync(reset=reset)
    finally:
        ing.conn.close()


def run_probe(cfg: dict):
    """连通性自检：RPC 可用性 + 找一个最近区块的事件验证 topic 正确。"""
    ing = ChainIngester(cfg)
    latest = ing._rpc(lambda: ing.w3.eth.block_number)
    log.info("RPC OK，最新区块 %d", latest)
    n = 0
    base = {"address": list(ing.exchanges.keys()),
            "topics": [[ing.topic_order_filled, ing.topic_order_filled_v2]]}
    for logs, _ in ing._get_logs_adaptive(base, latest - 100, latest):
        n += len(logs)
    log.info("最近 100 块内 OrderFilled 事件: %d 条（>0 说明 topic/合约地址正确）", n)
    ing.conn.close()
    return n
