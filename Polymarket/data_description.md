# Tracker 与 Filter：数据来源、覆盖范围和指标说明

> 最后核验：2026-09-03（UTC+8）。本文描述 `Polymarket/tracker` 与
> `Polymarket/Filter` 的实现口径和核验时的本地数据状态。运行时数据会变化；下文把
> **系统设计上限** 与 **某次快照的实际覆盖** 分开说明。

## 1. 先看结论

| 问题 | Tracker | Filter |
|---|---|---|
| 核心用途 | 批量重建候选账户历史、计算现金流 PnL、生成账户画像 | 从榜单候选中筛选高频/高效率地址，实时观察并做纵向 cohort 研究 |
| 主数据源 | Polymarket Data API | Polymarket Data API |
| 市场元数据 | Polymarket CLOB API | Polymarket CLOB API，仅用于补充计划结束时间等字段 |
| Polygon 链上数据 | 可选：交易回执抽样；另有 `eth_getLogs` 备用全链采集器 | 可选：`eth_chainId` 和有限 `eth_getTransactionReceipt` |
| 是否“纯链上” | 否 | 否 |
| 当前数据是否含链上核验 | 当前数据库没有成交，因此没有可用链上核验样本 | 主筛选快照中链上核验关闭，回执数为 0；纵向研究另设有限回执核验 |
| 当前可用程度 | 管线原型存在，但本地核心分析表为空 | 可运行；有筛选快照、实时观察和研究快照 |

最重要的理解是：**官方 API 返回的活动记录带有 `transactionHash`，不等于这些记录是由本项目直接从链上重建的。** 交易哈希可以用于回执核验，但主记录仍来自 Polymarket 托管 API。

---

## 2. Tracker

### 2.1 数据流

```text
Data API /v1/leaderboard ──> candidates + leaderboard_snapshots
Data API /activity ────────> fills + ctf_events + token_map
CLOB API /markets/{id} ────> markets
Data API /positions ───────> positions_cache

fills + ctf_events + markets + positions_cache
  └──> address_stats + address_market_pnl
       └──> profiles.md / profiles.csv

Polygon eth_getTransactionReceipt ──> API 成交抽样真实性核验
Polygon eth_getLogs ────────────────> 备用链上回填路径（非主数据源）
```

### 2.2 数据来源

#### A. Polymarket Data API 排行榜

- 端点：`GET https://data-api.polymarket.com/v1/leaderboard`
- 用途：建立候选地址池，保存地址、用户名、榜单类别、周期、排名、官方 PNL 和成交量。
- 地址字段：`proxyWallet`，即 Polymarket 用户的代理钱包地址。
- 分页：每页最多 50 条，以 `offset` 翻页；遇到空页或重复页停止。

#### B. Polymarket Data API 活动记录

- 端点：`GET https://data-api.polymarket.com/activity`
- 用途：按地址读取 TRADE、SPLIT、MERGE、REDEEM、REWARD、MAKER_REBATE 等公开活动。
- 主采集数据会写入 `fills` 或 `ctf_events`；活动记录自带的 `conditionId`、token 和标题用于建立市场映射。
- 每个地址默认最多读取 400 页，即名义上最多约 200,000 条活动。触及上限时，更早历史不在本地样本中。

#### C. Polymarket Data API 当前持仓

- 端点：`GET https://data-api.polymarket.com/positions`
- 用途：读取 `size`、`curPrice` 或 `currentValue`，计算当前持仓市值。
- 它是计算 `total_pnl = cash_net + position_value` 的组成部分，是时点快照，不是历史持仓轨迹。

#### D. Polymarket CLOB API 市场元数据

- 用途：补充市场问题、类别、计划结束时间、关闭/揭晓状态、胜出 outcome、token ID 和 neg-risk 标志。
- 市场级胜率、首次入场距离截止时间和临近截止买入比例都依赖这些元数据；映射缺失会降低这些指标的覆盖，但地址级全量现金流仍按已采集活动计算。

#### E. Polygon RPC

Tracker 有两条链上路径：

1. `verify`：随机抽取 API 成交，用 `eth_getTransactionReceipt` 检查回执中是否存在相应 maker 的 `OrderFilled` 事件。
2. `sync-chain`：通过 `eth_getLogs` 读取 CTF Exchange、NegRisk 和 ConditionalTokens 相关事件，作为备用采集方式。

第二条路径在免费 RPC 的日志查询区块范围限制下成本很高，因此项目把 Data API 设为主数据源。链上回执核验只能证明“该交易和相关合约事件存在”，不能单独证明 API 已完整覆盖账户历史。

### 2.3 当前本地数据 coverage

2026-09-03 以 SQLite `mode=ro&immutable=1` 核验 `tracker/data/smartmoney.db`：

| 数据表/对象 | 当前行数 | 含义 |
|---|---:|---|
| `candidates` | 1,749 | 去重后的候选代理钱包 |
| `leaderboard_snapshots` | 2,033 | 2026-07-08 的排行榜记录 |
| `fills` | 0 | 没有已入库成交 |
| `ctf_events` | 0 | 没有 split/merge/redeem/reward/rebate 记录 |
| `markets` / `token_map` | 0 / 0 | 没有市场元数据或 token 映射 |
| `positions_cache` | 0 | 没有当前持仓快照 |
| `address_stats` / `address_market_pnl` | 0 / 0 | 没有可用账户级或市场级分析结果 |
| `block_times` / `sync_state` | 0 / 0 | 没有链上区块时间或链上同步游标 |

排行榜细分：

| Category | Period | 行数 | 唯一地址 | Rank | 日期 |
|---|---|---:|---:|---:|---|
| CRYPTO | ALL | 500 | 500 | 1–500 | 2026-07-08 |
| CRYPTO | MONTH | 519 | 519 | 1–500 | 2026-07-08 |
| OVERALL | ALL | 500 | 500 | 1–500 | 2026-07-08 |
| OVERALL | MONTH | 514 | 514 | 1–500 | 2026-07-08 |

数据库健康检查 `PRAGMA quick_check` 为 `ok`，但“数据库结构健康”不等于“研究数据完整”。当前 Tracker 只能可靠说明候选池和单日榜单快照，不能支持可靠的交易行为、持仓、胜率或 PnL 结论。旧 `viz` dashboard 是独立静态快照，无法由当前数据库复现，不应与上表混用。

### 2.4 Tracker 指标字典

#### 排行榜字段

| 指标 | 定义 | 注意 |
|---|---|---|
| `rank` | 地址在指定 category、period、orderBy 下的官方名次 | 相对排名，不等于绝对表现 |
| `official_pnl` | 最新 ALL 周期排行榜里的官方 PNL | 官方口径；本项目不重新定义它 |
| `vol` | 官方榜单成交量 | 不是本金，也不是净投入 |

#### 现金流和收益

| 指标 | 公式/含义 | 注意 |
|---|---|---|
| `buy_volume` | 买入 outcome token 支出的 USDC 总额 | 买入时 fee 从收到的 token 中扣，本口径不再从 USDC 支出重复扣费 |
| `sell_volume` | 卖出 outcome token 实收 USDC，代码中为 `taker_amount - fee` | 已扣卖出 fee |
| `cash_net` | 奖励/返佣 + merge/redeem 流入 − split 流出 + 卖出实收 − 买入支出 | 不含当前持仓价值 |
| `position_value` | 当前持仓的 `currentValue`，或 `size × curPrice` | 时点估值，价格和持仓会变 |
| `total_pnl` | `cash_net + position_value` | 只有活动窗口足够完整时才接近账户经济盈亏 |
| `realized_pnl` | 奖励/返佣 + 已揭晓市场的市场级现金流净额 | 不是严格税务或会计“已实现收益” |
| `pnl_diff` | `total_pnl - official_pnl` | 用来发现本地窗口、映射或官方口径差异，不代表谁一定错误 |

#### 行为和市场指标

| 指标 | 定义 | 注意 |
|---|---|---|
| `n_fills` | 该地址作为 maker 的成交记录数 | 一笔链上交易可能包含多条 fill，不等于交易哈希数 |
| `n_markets` | 已映射到 `conditionId` 的不同市场数 | 映射缺失会低估 |
| `n_resolved` | 已映射且已揭晓的市场数 | 依赖市场元数据 coverage |
| `wins` | 已揭晓市场中，市场现金流净额大于 0 的市场数 | 以市场现金流为正定义“胜”，不是预测命中率 |
| `win_rate` | `wins / n_resolved` | 未揭晓市场不进入分母 |
| `avg_entry_price` | 买入 USDC 总成本 / 买入 token 数量 | 是数量加权平均成交价 |
| `median_entry_lead_h` | 每个市场首次买入距离计划截止时间的小时数的中位数 | 不是距离实际判定/兑付时间 |
| `late_buy_share` | 截止前 24 小时内买入 USDC / 全部买入 USDC | 只有结束时间已知且映射成功的买入能识别为“临近截止” |

### 2.5 Tracker 不能声称什么

- 当前不能声称已有完整交易历史、可靠账户 PnL 或有效胜率。
- `proxyWallet` 不等于现实身份、资金最终受益人或 EOA。
- CEX 入金/出金不等于已经买入或卖出。
- API 抽样回执通过不代表 API 没有漏数据。
- 当前 `CONVERSION` 未纳入现金流，重度 neg-risk 用户可能产生偏差。
- 受回填天数和单地址页数上限影响，超高频地址可能只有截断样本。

---

## 3. Filter

### 3.1 数据流

```text
CRYPTO PNL 排行榜（最多 Top 1,000）
  └──> 每个地址 /activity?type=TRADE
       └──> 交易频率、收益效率、每条活动估算 PNL
            └──> 排序后最多 100 个地址进入详细观察
                 ├──> CLOB market：结束时间
                 ├──> Data API positions：当前持仓
                 ├──> 实时 activity 轮询
                 └──> 可选 Polygon receipt 核验

独立研究线程：DAY PNL Top 1,000 + DAY VOL Top 1,000
  └──> 每 6 小时快照；相隔 24 小时比较
       └──> entered / retained / dropped cohort
            └──> 每日有限行为富集与 Finding / Insight
```

### 3.2 数据来源

#### A. Data API `/v1/leaderboard`

- 手动筛选默认使用 `category=CRYPTO`、`timePeriod=WEEK`、`orderBy=PNL`，最多 1,000 个地址。
- 纵向研究固定读取 DAY 周期的 PNL Top 1,000 和 VOL Top 1,000。
- `leaderboard_user` 可按单一地址复核其当前官方排名，包括 Top 1,000 之外的地址。

#### B. Data API `/activity`

- 只读取 `type=TRADE`。
- 手动筛选默认每个地址最多 2 页 × 500 条，即最多 1,000 条活动。
- 纵向研究富集同样最多 2 页 × 500 条。
- 实时观察每 15 秒轮询；单次每地址最多请求 100 条，页面状态最多保留 1,000 条实时活动。
- 一条 activity 是一条成交/fill 记录；一个 `transactionHash` 可以对应多条 activity。

#### C. Data API `/positions`

- 对进入详细观察的地址读取当前持仓，单次最多 500 条，按当前价值排序。
- 页面展示的持仓现金 PnL 来自 API 持仓字段聚合；它不是完整配对开平仓重建。

#### D. CLOB API `/markets/{condition_id}`

- 用于补充市场问题、slug、计划结束时间、closed/active 状态。
- 每个详细地址默认只补齐最近最多 12 个不同市场。
- 短周期 Crypto slug（如 5m/15m）优先由 slug 中的起始时间与周期推导计划结束时间；其他市场使用官方结束字段。

#### E. Polygon RPC

Filter 只允许两类只读调用：

- `eth_chainId`：确认连接的是 Polygon chain ID 137；
- `eth_getTransactionReceipt`：有限抽取交易哈希，检查交易成功状态、区块号、日志数量，以及 `to` 或日志地址是否命中配置的 Polymarket 合约集合。

它**不使用** `eth_getLogs` 重建账户历史，不解码 maker/taker，不追踪对手方，也不把回执命中解释成对敲或协调。当前主筛选配置 `chain.enabled=false`，所以当前主快照的链上回执数为 0。纵向研究另设 `receipt_verification_enabled=true`，每天富集时每个地址最多抽 2 个近期交易哈希；这是证据抽样，不是全量链上 coverage。

### 3.3 系统设计 coverage

| 范围 | 默认上限/窗口 | 实际含义 |
|---|---:|---|
| 手动候选池 | CRYPTO PNL Top 1,000 | 不是 Polymarket 全站所有地址 |
| 手动活动窗口 | 7 天 | 可在页面选择 24 小时、7 天或 30 天 |
| 每地址活动 | 1,000 条 | 2 页 × 500；触顶后 `activity_truncated=true` |
| 详细分析地址 | Top 100 | 通过筛选后按所选指标排序的前 100 个 |
| 结束时间补齐 | 每地址最多 12 个市场 | 不是该地址全部历史市场 |
| 实时跟踪地址 | 最多 100 个 | 实时状态最多保留 1,000 条活动 |
| 研究榜单 | DAY PNL 1,000 + VOL 1,000 | 每 6 小时保存一次，保留 90 天 |
| 榜单迁移比较 | 相隔 24 小时 | DAY PNL 是滚动窗口值；两次值的差不是新增 24h PNL |
| 每类研究 cohort | 最多 30 个地址 | dropped、retained control、entered、volume losers、matched winners |
| Insight 最低门槛 | ≥7 个完整日、每组 ≥30 个唯一地址、方向一致度 ≥75% | 还要求每日中位差 bootstrap 95% 区间不跨 0 |

### 3.4 当前 Filter 快照 coverage

以下数字来自 2026-09-03 核验时的本地运行快照，仅描述该次扫描，不是永久常量：

| 项目 | 数值 |
|---|---:|
| 扫描完成时间 | 2026-09-03 05:27:24 UTC |
| 实时活动更新时间 | 2026-09-03 09:37:23 UTC |
| 排行榜返回的有效候选地址 | 924 |
| 通过筛选 | 740 |
| 扫描到的活动记录 | 399,260 |
| 去重交易哈希数（逐地址汇总） | 390,421 |
| 触及 1,000 条活动上限的地址 | 279 |
| 当前内存/快照保留的实时活动 | 1,000 |
| 扫描错误记录 | 8 |
| 主筛选链上回执 / 已确认回执 | 0 / 0 |

该次尾盘分析只覆盖排序靠前的 100 个地址，而且 100 个全部触及活动读取上限：

| 尾盘 coverage | 数值 |
|---|---:|
| 参与尾盘分析地址 | 100 |
| 总活动记录 | 100,000 |
| 能取得计划结束时间的记录 | 87,136 |
| 结束时间 coverage | 87.14% |
| slug 推导结束时间 | 81,920 |
| 官方市场结束字段 | 5,216 |
| 结束前 60 分钟记录 | 81,660 |
| 占结束时间已知记录 | 93.72% |
| 有 60 分钟尾盘活动的地址 | 93 |
| 其中价格 ≥0.90 的 BUY | 10,218 |
| 60 分钟样本名义规模 | 2,496,551.34 USDC |

这些数字高度受“按频率排序、每地址最多 1,000 条、只取 Top 100”影响。比如 93.72% 不是全站交易的尾盘比例，而是这个**高频、截断、排序后样本**中结束时间已知记录的比例，不能直接外推。

核验时有 5 个研究榜单快照，覆盖 2026-09-02 06:00 UTC 至 2026-09-03 06:00 UTC；每个快照均含 PNL 1,000 和 VOL 1,000。尚未累积到 7 个完整日，因此不能形成符合门槛的 Insight。

### 3.5 Filter 指标字典

#### 筛选指标

| 指标 | 公式/含义 | 如何解读 |
|---|---|---|
| `trade_count` | 回看窗口内 activity TRADE 行数 | fill 数，不是独立链上交易数 |
| `transaction_count` | 去重后的 `transactionHash` 数 | 更接近链上交易次数，但一个交易可含多条 fill |
| `trades_per_day` | `trade_count / 回看天数` | 页面“交易频率”；24h、7d、30d 的分母不同 |
| `leaderboard_pnl` | 官方相同榜单周期的 PNL | 官方窗口指标，不是 Filter 自算 PnL |
| `leaderboard_volume` | 官方相同榜单周期成交量 | 不是投入本金 |
| `return_efficiency` | `leaderboard_pnl / leaderboard_volume` | “总收益效率”，不是严格 ROI；页面以百分比显示 |
| `estimated_pnl_per_activity` | `leaderboard_pnl / trade_count` | 仅在活动未截断时给出；不是一笔真实配对交易的利润 |
| `activity_truncated` / `capped` | 活动读取达到 1,000 条上限 | `trade_count` 和频率是下限；每条活动 PNL 不再可靠，显示未知 |

#### 尾盘行为指标

| 指标 | 定义 | 注意 |
|---|---|---|
| `minutes_to_settlement` | 计划结束时间 − 成交时间，单位分钟 | “settlement”字段名实际表示计划结束时间，不一定是实际判定/兑付时间 |
| `settlement_coverage` | 结束时间已知的活动 / 全部活动 | 低 coverage 时尾盘比例不可外推 |
| `median_hours_to_settlement` | 所有非负结束距离的中位数，单位小时 | 负值（计划结束后记录）不进入中位数 |
| `tail_60m_trade_count` | 计划结束前 0–60 分钟活动数 | 同理还有 6h、24h |
| `tail_60m_share` | 60 分钟活动 / 结束时间已知且非负的活动 | 不是全部活动的比例 |
| `tail_60m_buy_count` / `sell_count` | 60 分钟窗口内 BUY / SELL 行数 | 行数，不是地址数或市场数 |
| `tail_60m_high_confidence_count` | 60 分钟窗口内价格 ≥0.90 的 BUY 数 | “high confidence”只是价格阈值标签，不证明真实胜率高 |
| `tail_60m_avg_price` | 60 分钟活动的平均成交价 | 未按规模加权 |
| `tail_60m_usdc_volume` | 60 分钟活动的 `usdcSize` 合计 | 名义成交规模，未扣费用、滑点，也不是 PnL |
| `after_scheduled_end_count` | 计划结束时间之后出现的活动数 | 可能来自实际结束延迟、元数据误差或市场仍可交易 |

#### 当前持仓和链上核验指标

| 指标 | 定义 | 注意 |
|---|---|---|
| `open_position_cash_pnl` | Data API 当前持仓中的 cash PnL 聚合 | 是当前持仓快照，不是完整历史总 PnL |
| `chain_receipt_count` | 成功取得的抽样交易回执数 | 抽样分母，不代表所有活动 |
| `chain_confirmed_count` | 回执 `status == 1` 的数量 | 只证明链上交易成功 |
| `chain_verification_rate` | `confirmed / receipt_count` | 回执缺失会影响分母/coverage |
| `polymarket_contract_rate` | 命中已知 Polymarket 合约的回执 / 已确认回执 | 合约集合可能不完整；命中不证明具体策略 |
| `chain_log_count` | 抽样回执中的日志总数 | 未解码时不能直接解释为成交数 |
| `chain_latest_block` | 抽样回执的最高区块号 | 只表示抽样数据的新鲜度上界之一 |

#### 纵向排行榜与策略指纹

| 指标 | 定义 | 注意 |
|---|---|---|
| `entered` / `retained` / `dropped` | 相隔 24h 的两个完整 DAY PNL 快照中，进入/留在/掉出 Top K | “掉榜”不是账户消失，也不必然是亏损 |
| `retention_rate` | 留榜地址 / 上一期 Top K 地址 | 描述榜单稳定性 |
| `trades_per_hour` | cohort 富集窗口内活动数 / 窗口小时数 | 活动截断会低估 |
| `records_per_transaction` | activity 行数 / 去重交易哈希数 | 衡量一次链上交易包含多少 fill 记录 |
| `median_trade_usdc` / `p90_trade_usdc` | 单条活动 `usdcSize` 的中位数 / 90 分位数 | 不是完整订单大小；大单可被多条 fill 拆分 |
| `market_count` | 不同 `conditionId` 数量 | 受活动页数上限影响 |
| `market_concentration` | 按 `usdcSize` 权重计算的 HHI：`Σ(市场规模/总规模)²` | 越接近 1 越集中；缺失规模时实现以每条记录权重 1 代替 |
| `buy_share` | BUY activity / 全部 activity | 是记录占比，不是资金占比 |
| `average_price` | 所有有效成交价的简单平均 | 未按规模加权 |
| `high_price_buy_share` | 价格 ≥0.90 的 BUY / 全部 BUY | 价格高不等于低风险 |
| `tail_60m_share` | 有结束时间的活动中，结束前 60 分钟占比 | 目前策略指纹仅从可由短周期 slug 推导的结束时间计算 |

#### Finding 与 Insight

系统比较“高成交量亏损者 vs 成交量匹配赢家”以及“掉榜者 vs 留榜对照”的每日中位数差异。状态含义：

- `COLLECTING`：尚未有可用每日富集；
- `FINDING`：已有描述性差异，但样本或稳定性不足；
- `INSIGHT`：至少 7 个完整日、每组至少 30 个唯一地址、方向一致度至少 75%，且每日中位差的确定性 bootstrap 95% 区间不跨 0；
- `反直觉信号`：稳定方向与预设假设相反；
- `证据不一致`：不同日期方向不稳定。

即使达到 `INSIGHT`，它仍是公开行为的统计关联，不是因果证明。

### 3.6 Filter 不能声称什么

- 筛选结果不是 Polymarket 全站最高频账户，而是指定 CRYPTO PNL 榜候选池中的结果。
- DAY PNL 两个快照之差不是两次快照之间“新增 PnL”。
- 活动行数不等于独立订单数，也不等于交易哈希数。
- 价格 ≥0.90 的 BUY 不等于稳赢或低风险；错误时可能损失接近全部本金。
- 计划结束时间不一定等于实际判定、关闭或兑付时间。
- 尾盘行为不等于尾盘策略盈利；当前统计没有完整纳入费用、滑点、未成交订单和最终输赢。
- 回执命中不能证明 maker/taker 身份、对敲、串谋、内幕交易、机器人参数或现实身份。

---

## 4. 两个项目应该怎样一起理解

Tracker 与 Filter 不是两个相同用途的页面：

- **Tracker** 试图建立更深的账户经济账本：历史活动、市场映射、当前持仓、现金流 PnL 和账户画像。它的指标更接近研究“赚了多少钱、在哪些市场赚”，但对历史完整性和市场映射要求更高；当前本地数据尚未闭环。
- **Filter** 先解决候选发现和持续观察：在一个有边界的官方榜单候选池中找出高频或高效率地址，再对前 100 个做有限的市场、持仓、实时和链上回执补充。它更适合“找谁值得继续研究”，不适合直接作为完整 PnL 账本。

推荐工作流是：

```text
Filter 找候选、观察榜单迁移和行为指纹
  → 选择明确且有研究价值的 cohort
  → 进入可审计的深度账户历史管线
  → 计算覆盖率充分、可复现的现金流和行为指标
  → 事实 / 推断 / 假设分层报告
```

## 5. 复现与更新本文数字

- Tracker 的表行数应以只读 SQLite 连接核验，避免运行会初始化或修改数据库的命令。
- Filter 当前扫描数字来自 `Filter/data/snapshot.json`，研究快照来自 `Filter/data/research/`；它们是运行时数据，按项目规则不提交、不对外静态提供。
- 更新本文时必须同时记录快照时间、筛选配置、候选池、活动页数上限、截断地址数和错误数，不能只复制页面上的总数。
- 所有比例都应明确分子、分母和样本选择规则；所有 PnL 都应注明是官方窗口值、现金流重建值还是当前持仓快照值。
