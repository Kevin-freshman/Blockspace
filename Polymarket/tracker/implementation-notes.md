# Implementation Notes

实现过程中的关键决策与偏离计划的记录（Deviations）。

## 关键决策

- **只采集 maker 侧 OrderFilled**：CTF Exchange 上每个用户的订单成交都会以该用户为
  maker 发出一条 OrderFilled；taker 字段多为对手方/交易所。只用 maker 侧即可完整覆盖
  且避免同一笔成交按 maker/taker 重复计入现金流。taker 侧采集留了开关
  （`ingest.track_taker_side`），默认关闭。
- **fee 处理**：Polymarket 的 fee 从"收到的资产"里扣。卖出时收到 USDC−fee（计入现金流）；
  买入时 fee 扣在 token 上，不影响现金流，仅影响持仓数量（对 PnL 的影响通过持仓市值体现）。
- **默认 RPC 用公共节点**：保证开箱能跑；README 引导用户换 Alchemy。
- **sync_state 按 stream 全局记录**（不按地址）：地址批共享区块推进。新增地址需要
  `sync --reset` 重扫，靠 (tx_hash, log_index) 主键幂等去重。
- **回填起点**：按 `backfill_days` 用二分查找定位区块号，而不是从合约部署块（2022 年）
  全量扫，控制免费 RPC 档的用量。代价是"全期收益"变成"窗口内收益"，画像表已注明口径。
- **浮盈用 Data API /positions 的 curPrice**：链上没有现价，自建订单簿快照超出 MVP 范围。

## 实测发现（影响架构的事实）

- **Polymarket 已于 2026-04-28 迁移到 CLOB V2**：新交易所合约
  `0xE111...996B`（CTF）/ `0xe222...0F59`（NegRisk），`OrderFilled` 事件结构变了
  （显式 side 枚举 + 单一 tokenId + builder/metadata 字段），抵押品从 USDC.e 换成
  pUSD（`0xC011...2DFB`，同为 6 位小数、1 美元锚定）。采集层同时监听 V1+V2 四个
  合约、两种 topic，V2 事件在入库时归一化成 V1 的 asset_id 语义，收益引擎无需感知。
  验证方式：直接抓最近区块的 topic0 分布对照源码里的事件签名。
- **排行榜 API offset 实测能翻到至少 500 名**，四个榜单（OVERALL/CRYPTO ×
  MONTH/ALL）去重后约 1700 个地址。
- **公共 RPC 现状（2026-07 实测）**：`polygon-rpc.com` 已要求鉴权（401）；
  publicnode 的 getLogs 范围上限 ~250 块且超限返回 403（无错误详情）；
  drpc 超限返回 400，小分块可用。默认节点选了 drpc + 50 块下限，
  自适应分块把各种超限错误都当降块信号处理。要认真回填必须换 Alchemy。
- **Polygon 是 POA 链**：web3.py 读区块头需要注入 `ExtraDataToPOAMiddleware`。

## 2026-07-08 下午：链上方案改混合方案（重大变更）

**触发**：用户拿到 Alchemy key 后回填连续失败。真实原因（挖出 Alchemy 错误响应体）：
免费档 `eth_getLogs` 只允许 **10 块**范围——20000 块的分块降到 50 块下限也过不了，
纯链上回填在免费额度内不可行。

**决策**（用户确认）：主数据源改为官方 Data API `/activity`（免费、免鉴权、按地址直查），
链上只保留 `verify` 抽样校验（`getTransactionReceipt` 不限范围）和备用的 `sync-chain`。

**过程中发现并修复的问题**：
- Gamma API 对 V2 后的市场覆盖率极差（7002 个 token 只映射上 360 个），
  且 `condition_ids`/`clob_token_ids` 批量参数实测不可靠 → 元数据源换成
  CLOB API `GET /markets/{cid}`（逐个查，实测覆盖 750/755，且直接带 winner）；
  token→市场映射改为在采集时直接从 activity 记录写入（记录里自带 conditionId/title）。
- combo/parlay 合成市场（isCombo，conditionId 尾部全零）CLOB 查不到元数据，
  只有标题，胜率统计不含这类市场。
- **PnL 口径 bug**：旧实现只把"已映射到市场"的成交计入现金流，映射缺口会让买入
  被漏计、PnL 虚高（swisstony 一度显示 +1.17 亿）。修复：地址级现金流用全量成交，
  市场级拆解只做胜率和证据展示。
- Data API 的 REWARD（做市奖励）/ MAKER_REBATE（返佣）也计入现金流（extra_cash）。
- 多进程同时写 SQLite 会 database is locked：连接加 30s busy_timeout；
  运行 pnl/markets 前先停掉 `run` 循环进程。
- /activity 用时间窗口翻页（offset 深翻会截断），单地址设页数上限防超高频地址拖死。

## Deviations

- 计划里写"按 maker/taker topic 过滤候选地址"，实现改为默认只拉 maker 侧（原因见上），
  taker 侧保留开关。
- 未实现 NegRisk Adapter 的 convert 事件采集：该操作（NO 组合转换）现金流口径复杂，
  MVP 先跳过，重度负风险玩家的 PnL 会有偏差（README 已注明局限）。
- split/merge/redeem 只计入 parentCollectionId == 0 且抵押品为 USDC 的事件，
  嵌套 collection 在 Polymarket 上不用于常规交易。
