# Polymarket Crypto Top 5 全新独立研究项目计划

## 1. 分阶段实施计划

### 阶段 0：建立隔离边界

- 以 `crypto_top5_analysis` 为唯一工作根目录，新建且只使用 `data/db/top5.sqlite3`。
- 所有路径必须先经根目录约束校验；拒绝读取项目根目录外的数据文件。
- 生产采集记录 Git commit、项目目录内容哈希、采集器版本和配置哈希。
- 数据库启用 `PRAGMA foreign_keys=ON`、WAL 和迁移版本管理。
- 公网服务器的 document root 只能设置为 `public/`；禁止目录穿越、符号链接和运行时数据库访问。
- 静态检查保证代码、配置中不存在其他 SQLite 数据库路径，且没有指向相邻项目的导入或读取逻辑。

### 阶段 1：锁定五个目标地址

- 将用户种子快照单独写入不可覆盖的 `target_seed_snapshots`，序号保存为种子排名 1–5。
- `snapshot_as_of_utc=NULL`，另存实际录入时间 `recorded_at_utc`。
- 使用官方 `CRYPTO / ALL / PNL` 排行榜的无用户参数查询解析候选完整 `proxyWallet`；接口路径、参数及字段含义均须先查官方文档，当前标记为 `[待核验]`。
- 对五项逐一核对：用户名、42 字符完整地址、地址前缀、地址后缀、排名、PnL、成交量。
- 用户名和地址身份必须严格一致；动态排名、PnL、成交量的差异单独留痕，不覆盖种子快照，也不单独构成阻断。
- 必须得到恰好五个、规范化后互不相同的完整地址，才生成并锁定 `targets.yaml`。
- 若排行榜不提供完整地址、出现重名/多候选、前后缀不匹配或唯一地址不是恰好五个，立即停止并向用户请求完整地址或证据。
- `targets.yaml` 锁定前禁止任何带 `user`/`address` 参数的账户采集。

### 阶段 2：接口契约确认

- 只依据官方文档和最小验证请求确认 Polymarket 排行榜、Profile、Trades、Activity、Positions、Markets、Price History 的正式接口。
- Trades 固定要求 `takerOnly=false`、`start=1`；`start` 的准确语义、分页字段和 offset 上限标记为 `[待核验]`。
- 确认各接口的排序稳定性、时间边界包含规则、最大页长、最大 offset、限流和错误语义。
- 确认 Polygon explorer/RPC 提供商、链 ID、合约地址、ABI、最终确认规则；未获得官方证据的合约不得解码或归类。
- 将已确认的接口契约写成机器可验证配置和契约测试，不在业务代码中散落猜测。

### 阶段 3：原始数据采集

- 账户范围：五个目标的全部可获得历史，包括 Crypto 和非 Crypto。
- 市场范围：仅五人实际交易过的市场及其 outcome token。
- Polymarket 数据：Profile、Trades、Activity、当前持仓、已关闭持仓、市场元数据、价格历史。
- Polygon 数据：普通交易、internal transactions、ERC-20/ERC-1155 transfer、receipt、logs、block timestamp、经确认的 Polymarket 合约交互。
- 外部地址仅保存其在目标交易中直接出现的身份和边，不递归请求其账户历史。
- 每个 HTTP 客户端请求前验证目标地址、主机、参数和跳转目的地；任何不在 `targets.yaml` 的 `user`/`address` 立即在本地拒绝。

### 阶段 4：标准化与完整性证明

- 原始响应先以精确响应字节计算 SHA-256，再保存为不可覆盖的 gzip JSON。
- 数据库写入使用自然键、来源请求键和内容哈希去重；同一不可变交易出现内容冲突时不覆盖，转为数据质量错误。
- 为每个账户、接口和时间窗口生成覆盖报告，包括请求窗口树、终止依据、重复数、唯一数、最早/最晚时间、缺口和响应哈希 Merkle root。
- 生产覆盖状态只能是 `complete`、`partial` 或 `blocked`；不能把请求成功等同于历史完整。

### 阶段 5：指标与特征

- 构建可审计的独立 PnL、账户级指标、市场级指标和交易上下文特征。
- 每个指标绑定公式版本、输入快照哈希、计算时间、代码 commit 和覆盖状态。
- 特征以 SQLite 保存索引和摘要，以 Parquet 保存模型输入；金额使用定点整数或 Decimal，禁止依赖二进制浮点作为审计结果。

### 阶段 6：异常核查信号

- 使用版本化、可配置、可解释的规则生成“需要进一步核查”信号。
- 不输出内幕交易、对敲、共同控制等确定性结论。
- 数据量不足、事件时间不可靠、Maker/Taker 不可辨认或链上覆盖不完整时，信号必须降级为 `unavailable` 或 `low_confidence`。

### 阶段 7：静态报告

- Python 离线生成总览页、五个账户详情页和 Glossary。
- 浏览器不连接数据库和外部 API，只读取 `public/data` 中经过字段白名单导出的汇总 JSON。
- 图表、表格、术语解释、来源和局限均通过统一元数据组件生成。

### 阶段 8：审计与发布门禁

- 执行数据库完整性、采集覆盖、公式、异常规则、可访问性和 public 泄露检查。
- 相同 raw 数据、配置和 commit 必须生成相同数据库派生结果及 public 文件哈希。
- 任一硬性验收失败时禁止发布。

## 2. 目录结构

```text
crypto_top5_analysis/
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── target_seeds.yaml
│   ├── targets.yaml
│   ├── sources.yaml
│   ├── contracts.yaml
│   ├── public_event_sources.yaml
│   └── anomaly_rules.yaml
├── src/top5/
│   ├── cli/
│   ├── collectors/
│   ├── storage/
│   ├── normalization/
│   ├── features/
│   ├── signals/
│   ├── reporting/
│   └── security/
├── data/
│   ├── raw/
│   │   └── <source>/<yyyy>/<mm>/<dd>/<source_request_id>.json.gz
│   ├── db/
│   │   └── top5.sqlite3
│   ├── derived/
│   │   ├── account_day_features/
│   │   ├── account_market_features/
│   │   └── trade_context_features/
│   └── checkpoints/
├── logs/
├── reports/
├── templates/
├── public/
│   ├── index.html
│   ├── accounts/<account-slug>/index.html
│   ├── glossary/index.html
│   ├── assets/
│   └── data/
├── docs/
└── tests/
    ├── unit/
    ├── integration/
    ├── contract/
    ├── security/
    └── site/
```

`data/`、`logs/`、`reports/` 永远不位于 `public/` 下，也不得通过符号链接暴露。

## 3. 数据库表结构

所有地址保存为规范化小写 42 字符地址，同时可保存 checksum 展示形式；价格使用 `price_ppm`，美元使用 `microusd`，链上 token 同时保存原始整数和 decimals。

### 审计与运行

| 表 | 核心字段及约束 |
|---|---|
| `schema_migrations` | `version PK`、`applied_at_utc`、`git_commit` |
| `collection_runs` | `run_id PK`、固定截止时间、collector version、Git commit、项目树哈希、配置哈希、状态 |
| `source_requests` | `source_request_id PK`、`run_id FK`、可空 `target_id FK`、来源、方法、脱敏完整 URL、请求时间、HTTP status、响应 SHA-256、记录数、窗口、分页参数、raw 路径、collector version、Git commit、确定性 request key |
| `pagination_windows` | 账户、接口、半开时间窗口、递归深度、父窗口、终止原因、请求数、唯一记录数、覆盖状态、Merkle root |
| `coverage_reports` | 账户/数据集、期望范围、实际范围、缺口、重复数、冲突数、完整性结论 |
| `data_quality_issues` | 严重度、规则、对象、证据请求、处置状态 |

带用户参数的 `source_requests` 必须有非空 `target_id`，且请求地址必须等于该 target 的白名单地址。

### 目标身份

| 表 | 核心字段及约束 |
|---|---|
| `target_seed_snapshots` | 固定排名、种子用户名、地址前后缀、官方 PnL、成交量、种子比率、`snapshot_as_of_utc NULL`、录入时间、来源为用户 |
| `target_verifications` | 候选完整地址、官方观察用户名/排名/PnL/成交量、逐字段比较结果、来源请求、验证状态 |
| `targets` | `target_id` 限定 1–5、`seed_id UNIQUE FK`、完整 `proxy_wallet UNIQUE`、用户名、验证记录、锁定时间 |
| `official_account_snapshots` | `target_id FK`、观察时间、官方排名/PnL/成交量、筛选条件、来源请求 |
| `profiles` | `target_id FK`、观察时间、公开资料字段、来源请求 |

数据库触发器拒绝第六个 target；发布测试同时要求数量恰好为五。

### 交易、持仓和市场

| 表 | 核心字段及约束 |
|---|---|
| `trades` | `target_id FK`、稳定交易 ID/规范哈希、时间、market/outcome/token、BUY/SELL、price、size、notional、tx hash、直接对手方、Maker/Taker 可空、来源请求 |
| `activities` | `target_id FK`、类型、时间、市场、金额/token、tx hash、来源请求 |
| `position_snapshots` | `target_id FK`、run/as-of、token、数量、成本、估值、来源请求 |
| `closed_positions` | `target_id FK`、market/outcome、开平时间、数量、官方/独立 PnL 字段、来源请求 |
| `markets` | market/condition ID、问题、标签、开始/结束/结算时间、结果、Crypto 分类版本、首次发现 target |
| `market_snapshots` | 市场可变字段的逐次观察版本 |
| `target_markets` | `target_id FK + market_id FK`，证明市场确由目标交易发现 |
| `outcome_tokens` | market、token ID、outcome、decimals |
| `price_history` | target-market/token、UTC 时间、价格、来源请求，唯一键去重 |

市场采集器只有在 `target_markets` 已有目标交易证据后才允许请求该市场。

### Polygon 数据

| 表 | 核心字段及约束 |
|---|---|
| `onchain_transactions` | `target_id FK`、chain ID、tx hash、block、from/to、value、nonce、input selector、来源请求 |
| `internal_transactions` | `target_id FK`、tx hash、trace ID、from/to/value、状态 |
| `transaction_receipts` | `target_id FK`、tx hash、status、gas、contract address、block hash |
| `contract_logs` | `target_id FK`、tx hash、log index、contract、topics、data、解码状态 |
| `token_transfers` | `target_id FK`、tx hash、log/trace ID、ERC-20/1155、token/token ID、from/to、原始数量 |
| `contract_interactions` | `target_id FK`、tx hash、经证实的合约角色、函数/事件、Split/Merge/Redeem 分类、证据 |
| `observed_addresses` | 直接出现的外部地址及首次/末次观察；不触发历史采集 |
| `address_edges` | 目标到外部地址或五目标之间的直接资金/交互边、金额、tx 证据 |

### 公式、特征和异常

| 表 | 核心字段及约束 |
|---|---|
| `formula_registry` | 公式 ID、版本、中文定义、参数、单位、代码 commit |
| `feature_input_snapshots` | 输入请求/记录哈希集合、cutoff、覆盖状态 |
| `features` | target、可选 market/day/trade、指标名、值、单位、公式版本、输入快照、计算时间 |
| `account_day_features` | UTC 日期、交易数、金额、活跃、Crypto 占比、PnL、暴发比例、链上操作等 |
| `account_market_features` | 市场交易数/金额、入场、持仓、PnL、集中度贡献、结算距离等 |
| `trade_context_features` | 每笔交易的相对规模、价格变化窗口、结算距离、事件距离、同步簇等 |
| `public_events` | 来源白名单、标题、首次公开 UTC、URL、内容哈希、可信度 |
| `market_event_links` | 市场与公开事件的人工/规则映射及理由 |
| `anomaly_signals` | 规则版本、阈值、状态、优先级、替代解释、局限、生成时间 |
| `signal_evidence` | 信号、交易/链上记录、PolygonScan URL、公开消息 URL 或明确“不适用”原因 |

## 4. 目标地址验证流程

1. 固化五条种子记录，保留用户给出的整数 USD 数值和比率，不允许更新语句覆盖。
2. 查询官方筛选结果时固定记录 `CRYPTO / ALL / PNL`、请求时间和原始响应。
3. 从同一官方响应提取用户名、排名、PnL、成交量和完整 `proxyWallet`；不拼接或猜测缩写地址。
4. `proxyWallet` 的必要格式验证为 `^0x[a-fA-F0-9]{40}$`。必须保留 API 返回的原始地址字符串，并使用 lowercase 地址进行唯一性比较以及与种子地址前后缀的大小写不敏感匹配。EIP-55 checksum 只能作为附加审计信息，不是候选地址通过、拒绝或身份锁定的必要条件；严禁使用 Python `hashlib.sha3_256` 代替 Ethereum Keccak-256。若环境中没有已经存在且经过验证的 Ethereum Keccak 实现，不得为此安装新依赖，记录 `checksum_status = "not_checked"`，且不得因此拒绝符合十六进制格式的官方 `proxyWallet`。即使执行了 checksum 检查，也不得仅凭 checksum 结果确认账户身份；身份候选仍须满足官方排行榜来源、`userName` 严格匹配、地址前后缀匹配和全局地址唯一等条件。
5. 用户名严格匹配，完整地址必须匹配给定前缀和后缀。
6. 排名、PnL、成交量逐项记录“种子值、当前官方值、绝对差、相对差”；差异不覆盖种子。
7. 对规范化地址执行唯一性检查，结果必须为五。
8. 只有全部身份检查通过，才原子生成并锁定 `targets.yaml`；文件记录验证响应哈希和配置版本。
9. 后续 HTTP 客户端从该文件加载五地址集合，并在序列化 URL 前验证 `user`/`address`。
10. 任意失败均停止账户采集并输出候选、冲突字段和所需用户证据。

## 5. API 分页和断点续传方案

- 每个逻辑请求计算确定性 `request_key = SHA256(source + endpoint + target + normalized_params + window + page)`。
- 发送前插入 `source_requests` 的 pending 记录；响应成功后先计算哈希、检查密钥泄露，再以排他创建方式保存 gzip。
- URL 保留完整非敏感参数；`apikey` 一律记录为 `REDACTED`，认证 header 完全不落盘。
- 重试采用有限指数退避和 jitter；429、5xx、超时与永久 4xx 分开记录。
- 同一 request key 重跑时：
  - 已有成功且哈希一致：幂等跳过数据库写入。
  - 响应发生变化：保存新请求版本；不可变交易冲突进入质量错误，快照型数据新增观察版本。
- 正常分页直到得到官方定义的终止证据：空页、短页、无 next cursor 等；具体依据在接口契约确认后锁定。
- offset 即将超过上限或无法证明终止时，对 UTC 时间窗口递归二分。
- 时间窗口统一使用半开区间 `[start, end)`；若接口只支持包含边界，则相邻窗口保留最小重叠并通过稳定 ID/规范哈希去重。
- 若最小时间粒度内仍超过 offset 上限，且接口没有稳定 cursor/次级排序键，则标记 `blocked` 并停止，不宣称完整。
- Checkpoint 保存已完成窗口和终止证据；恢复时只处理 pending/failed/partial 窗口。
- 完整性证明包含：覆盖窗口树、无缺口检查、页级记录数、唯一数、重复数、首末时间、终止依据、所有响应哈希的 Merkle root。

## 6. 链上数据方案

- 固定 Polygon chain ID 137；生产截止点记录 block number、block hash 和 UTC 时间。
- 目标地址历史通过 explorer 的账户型接口获取普通交易、internal、ERC-20 和 ERC-1155 transfers；仅允许五个白名单地址作为账户参数。
- RPC 仅用于补充目标交易的 receipt、logs 和 block timestamp，按官方确认的 Polymarket 合约及目标 indexed topic 查询，以及验证 explorer 结果。
- block-range 查询遇到条数上限、超时或供应商截断时递归二分；最小区块仍溢出则停止并报告。
- 交易以 `chain_id + tx_hash` 去重；logs 以 `tx_hash + log_index`；internal 以 `tx_hash + trace_id`；ERC-1155 同时包含 token ID。
- Split、Merge、Redeem 只能根据官方合约地址、ABI 和可验证事件/调用数据分类；合约注册表来源标记 `[待核验]`。
- 外部资金来源、对手方和路由地址只保存直接边、金额和对应目标交易，不再以这些地址调用账户历史 API。
- 区块最终性优先使用 RPC 的 `finalized` 能力；不支持时，确认深度必须依据提供商或 Polygon 官方说明确定，当前标记 `[待核验]`。
- 每条链上证据生成交易链接和相关地址链接；PolygonScan 基础域名及链接格式在实施前做契约检查。
- explorer 与 RPC 结果不一致时保留双方证据，标记冲突，不静默择一。

## 7. 指标公式

统一使用 UTC。交易金额 `v_i = shares_i × price_i`；市场份额默认按独立重算成交金额计算。样本不足或必要字段缺失时返回 `unavailable`，不以零代替。

| 指标 | 公式与定义 |
|---|---|
| 成交笔数 | 去重后交易记录数 `N` |
| 活跃天数 | 至少一笔交易的不同 UTC 日期数 |
| 中位交易间隔 | 时间排序后 `median(t_i - t_(i-1))` |
| 日均交易频率 | `N / 历史覆盖的日历天数`；另列活跃日频率 `N / 活跃天数` |
| 爆发式交易比例 | 属于“相邻间隔均不超过 5 分钟且至少连续 3 笔”的交易数除以 `N` |
| 市场数 | 不同 market/condition ID 数量 |
| Crypto 占比 | 主指标 `Crypto 成交金额 / 全部成交金额`；同时报告笔数占比。分类规则和版本必须公开 |
| HHI 市场集中度 | `Σ(市场成交金额 / 总成交金额)²`，范围 0–1；成交金额为零时 unavailable |
| 官方 PnL | 种子官方 PnL 与每次采集的当前官方 PnL 分开保存和展示 |
| 独立已实现 PnL | FIFO lot：卖出/赎回所得减对应买入成本、可确认费用；Split/Merge 按等价抵押品守恒分配成本 |
| 独立总经济 PnL | `已实现 PnL + Σ(未平仓数量 × 截止价格 − 未平仓成本)`；无可靠截止价格的仓位不得按零估值 |
| PnL 差异 | 同一截止点、可比范围下 `独立 PnL − 官方 PnL`，同时报告绝对值和比例；官方口径未知时明确说明 |
| PnL/成交量 | `PnL / 成交金额`。它不是 ROI，因为成交量是周转额，不是投入资本或平均占用资金 |
| 盈利市场胜率 | 已结算且可完整重算的市场中，`市场净 PnL > 0` 的市场数除以合格市场数；未结算和持平排除 |
| 平均持仓时间 | FIFO 配对后按已处置份额加权的 `exit_time − entry_time` |
| 利润集中度 | 对正市场利润计算 `Σ(positive_market_pnl / total_positive_pnl)²`，并辅以 Top-1/Top-3 利润占比 |
| 最大回撤 | 日度盯市累计 PnL 曲线的 `max(previous_peak − later_value)`，主指标为 USD；只有资金流完整时才计算百分比回撤 |
| 入场价格 | BUY 的份额加权平均价格；同时保留首笔和中位入场价格 |
| 距离结算时间 | `official_resolution_time − entry_time`，以小时表示；官方结算时间缺失则 unavailable |
| Split/Merge/Redeem | 分别统计次数、涉及抵押品金额、活跃日、涉及市场数，以及相对交易笔数/金额的比例 |
| Maker/Taker | 只接受接口明确字段或已验证撮合链上证据；不得从 BUY/SELL 推断，证据不足显示 unavailable |
| 同步交易 | 两账户在同一 market、outcome、方向下 5 分钟内发生交易的次数、金额和显著簇；另算账户对 Jaccard |
| 资金关联 | 五目标之间直接转账、共同直接资金来源、重复直接对手方；仅基于目标交易已观察到的边 |

三类特征：

- `account_day_features`：每日交易数、成交额、买卖额、Crypto 占比、市场数、已实现/未实现 PnL、暴发比例、持仓暴露、Split/Merge/Redeem、链上成本。
- `account_market_features`：市场交易数、成交额、方向、首末交易、入场/退出价格、持仓时间、市场 PnL、是否结算、结算距离、集中度贡献。
- `trade_context_features`：交易相对账户历史的金额分位数、前后价格变化、结算距离、公开事件距离、同步簇、Maker/Taker 证据状态。

### 异常规则默认阈值

所有阈值写入配置并在报告中显示，实施前由用户确认：

- 公开消息前集中建仓：消息前 24 小时净新增头寸同时达到 `≥10,000 USD`、账户该市场历史 24 小时净新增的 P99、且占过去 30 日成交额 `≥2%`。
- 价格剧变前大额交易：交易金额同时达到 `≥10,000 USD` 和账户过去 90 日 P99，随后 6 小时内价格沿有利方向变化 `≥15` 个百分点。
- 临近结算异常胜率：结算前 24 小时交易；至少 20 个合格市场，相对账户非临近结算基线提升至少 20 个百分点，精确检验 `p≤0.01`。
- 短时间双边反复成交：同账户、同市场/outcome 在 10 分钟内至少 4 次买卖方向交替，毛成交额 `≥10,000 USD`，净额/毛额 `≤20%`。
- 五账户同步交易：至少 3 个目标在 5 分钟内交易同一 market/outcome/方向，每人 `≥2,500 USD`，合计 `≥25,000 USD`。
- 五账户资金互转：任何直接转账均记录；稳定币/原生资产折算金额 `≥1,000 USD` 或 30 日内至少 3 次时提升优先级。
- 重复直接对手方：同一外部地址直接连接至少两个目标，累计至少 3 次且可折算金额 `≥10,000 USD`。
- 循环路径：仅在已观察直接边中寻找长度 2–4、24 小时内闭合的路径；明确提示外部历史未递归采集，可能存在漏检。

## 8. HTML 页面与图表结构

### 总览页

- 研究对象与固定种子快照表。
- 当前官方值、独立 PnL 和差异对照表。
- 五账户数据覆盖时间轴。
- 每日交易笔数和成交额小多图。
- Crypto 占比与市场 HHI 对比图。
- 累计 PnL 曲线和独立的最大回撤图，禁止双 Y 轴。
- 同步交易时间线及账户对矩阵。
- 五目标直接资金关系图，并提供等价可排序表格。
- 异常核查队列：规则、优先级、证据完整度、替代解释和局限。
- 方法、来源和完整性摘要。

### 五个账户详情页

- 身份、完整地址验证状态、种子值与当前官方值。
- 数据覆盖、分页证明和来源请求摘要。
- 每日交易频率及爆发交易比例。
- Crypto/非 Crypto 对照及市场集中度。
- 入场价格与距离结算时间散点图。
- 持仓时间分布。
- 已实现/未实现 PnL 和单独回撤图。
- 市场利润分布和利润集中度。
- Split/Merge/Redeem 时间线。
- Maker/Taker 证据状态。
- 同步交易、资金关联和异常证据表。

### Glossary 与无障碍要求

- 每个术语首次出现时使用统一 `<dfn>` 组件，支持鼠标悬停、Tab 聚焦、Enter/Space 和手机点击。
- Glossary 覆盖 PnL、ROI、成交量、HHI、回撤、Maker/Taker、proxyWallet、Split/Merge/Redeem、ERC-20/1155、receipt、internal transaction 等。
- 买卖除颜色外同时使用文字、形状、线型或纹理区分。
- 每张图强制携带：回答的问题；X/Y 轴含义、单位、公式和 UTC 时区；图例；样本量；覆盖时间；来源链接；1–2 句读图指导；数据局限。
- 所有表格列头提供中文解释、单位、排序和来源；排序按钮支持键盘和屏幕阅读器。
- public 页面默认截断地址显示；PolygonScan 链接包含完整地址或 tx hash。
- 不使用外部 CDN、在线字体或运行时 API。

## 9. 测试与验收方案

### 单元与属性测试

- 地址格式、checksum、前后缀、大小写规范化和五地址唯一性。
- 安全 HTTP 客户端拒绝未白名单 user/address、未知主机、敏感 URL 日志和不安全跳转。
- 分页二分、边界重叠、offset 上限、恢复、去重和最小窗口溢出。
- Raw 文件排他创建、响应哈希、URL 脱敏和幂等写入。
- FIFO PnL、未实现估值、Split/Merge/Redeem、零成交量、缺失价格和最大回撤。
- 三类 feature 及全部异常规则的黄金样例和反例。
- ERC-20/ERC-1155、receipt、log 和直接地址边的固定 fixture 解码。

### 数据库测试

- `SELECT COUNT(*) FROM targets = 5`。
- `PRAGMA foreign_key_check` 为空。
- 所有用户级表不存在空 target 或孤儿记录。
- 所有市场均能追溯至至少一个目标交易。
- 不可变交易自然键无内容冲突。
- 每个派生数字都有公式版本、输入快照和计算时间。

### 集成与契约测试

- 使用本地 fixture 模拟 Polymarket、explorer 和 RPC，不在普通测试中访问真实 API。
- 对正式接口另设显式运行的只读契约测试，验证字段、分页和时间边界；失败时禁止生产采集。
- 每账户、每数据集必须有覆盖报告和可验证终止原因。
- Polygon 证据必须有格式正确的交易或地址链接。
- Maker/Taker 缺证据时必须显示 unavailable。

### 网页与安全测试

- 每张图检查问题、轴、单位、时区、公式、图例、样本量、覆盖期、来源、指导和局限。
- 禁止双 Y 轴，禁止仅以颜色编码买卖。
- Glossary 的鼠标、键盘和手机交互测试。
- 表格列解释、单位、排序和来源测试。
- `public/` 内容白名单审计：禁止 SQLite、gzip raw、Parquet、日志、环境文件、密钥、源映射泄密和符号链接。
- 静态扫描确保只存在 `data/db/top5.sqlite3` 这一数据库目标，不存在跨项目路径。
- 异常文案测试禁止使用无证据的定罪式表达。

### 最终发布门禁

十项用户验收标准全部映射为自动测试；任一失败，构建产物标记为不可发布。发布包只包含通过审计的 `public/`。

## 10. 实施前需用户确认的事项

已确认的设计决定：

- 种子序号即种子排名 1–5。
- 种子快照原始时间未知，保存为 NULL，不伪造时间。
- 用户名和地址身份严格核验；动态数值差异审计留痕但不覆盖、不单独阻断。
- 独立 PnL 采用已实现与含未实现的双口径。
- 公开消息采用来源白名单加人工 URL。
- 网页采用纯静态站点。

实施前仍需确认：

1. 批准上述七类异常规则的默认窗口、金额、样本量和显著性阈值；否则需提供替代阈值。
2. 提供或批准首批公开消息来源白名单。未获批准前只建立事件接口，不生成“消息前建仓”信号。
3. 确认链上提供商组合及环境变量名；建议仅约定 `POLYGON_RPC_URL`、`POLYGONSCAN_API_KEY` 或供应商等价变量，密钥值不得发到对话、配置或日志。
4. 在接口契约阶段确认实际官方 Polymarket endpoint、字段定义、offset 上限、`start=1` 语义、Polygon 合约地址/ABI 和最终性规则；任何未确认项保持 `[待核验]`，不得猜测实施。
5. 确认公开页面采用“地址文字截断、证据链接包含完整链上标识”的展示方式。
6. 确认目标解析成功时可在满足严格身份规则和五地址唯一性后自动锁定 `targets.yaml`；若希望每个完整地址仍逐个人工批准，应在首次采集前改为人工门禁。
