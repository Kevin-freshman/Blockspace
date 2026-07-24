# 项目进度

## Phase 0A：项目安全骨架与五账户种子配置

状态：完成（2026-07-24，Asia/Shanghai）

已完成：

- 完整审阅现有 `docs/PLAN.md` 与 `docs/DECISIONS.md`。
- 建立项目执行边界和 Git 忽略规则。
- 确认并使用唯一种子文件 `config/target_seeds.yaml`。
- 固化恰好五个用户提供的种子账户；仅保存缩写地址，全部标记为 `unresolved`。
- 建立或确认 `config/`、`data/`、`docs/`、`logs/`、`public/`、`reports/`、`src/`、`tests/`，并将 `data/`、`logs/` 权限设为 700。
- 登记计划使用但尚未请求、尚待核验的官方来源。
- 未调用外部 API，未安装依赖，未创建数据库，未开始全量采集。

本地验收结果：

- 种子账户数量：通过，恰好 5。
- 缩写地址唯一性：通过，5 个互不相同。
- 完整地址防伪：通过，种子文件没有 `0x` 加 40 位十六进制地址。
- public 私有文件隔离：通过，没有私有文件或符号链接进入 `public/`。
- Git 忽略规则：通过，覆盖 `data/`、`logs/`、`.env`、数据库、Python/测试缓存和虚拟环境；`public/` 未整体忽略。
- 目录权限：通过，`data/` 与 `logs/` 均为 700。

## 尚未解决

- 五个完整 `proxyWallet` 均未解析、未验证。
- Polymarket endpoint、字段、分页和 `start=1` 语义尚待官方契约核验。
- Polygon explorer/RPC 提供商、环境变量名、合约、ABI 和最终性规则尚未确认。
- `targets.yaml` 尚未生成；任何账户型采集仍被禁止。
- 当前目录不是 Git 仓库，无法记录 Git commit 或检查工作树差异。

## Phase 0B 前用户确认门禁

- 确认 Phase 0B 的具体范围，以及是否允许进行不带账户参数的官方身份解析请求。
- 确认五地址验证成功后是自动锁定 `targets.yaml`，还是逐地址人工批准。
- 若 Phase 0B 涉及链上契约，确认提供商组合及仅包含变量名的凭据约定；不得提供密钥值。

## Phase 0B：官方排行榜身份候选解析

状态：`blocked`（2026-07-24，网络 DNS 解析失败）

已完成：

- 用户已确认官方排行榜文档、唯一数据 endpoint、固定筛选参数、分页范围和人工审批门禁。
- 按用户授权修正 `docs/PLAN.md`：EIP-55 checksum 仅作附加审计信息，不再作为地址通过或拒绝的必要条件。
- 实现标准库受限客户端 `src/phase0b.py`，固定仅允许 `data-api.polymarket.com/v1/leaderboard`、固定参数和 `offset=0,50,...,1000`。
- 客户端拒绝额外参数、非授权 host/path、重定向后的非 200 状态、非 JSON、异常 content encoding 和响应契约异常。
- 实现原始 body SHA-256、确定性 gzip（`mtime=0`）、gzip SHA-256、排他创建、严格五种子匹配及人工审批报告生成逻辑。
- 没有读取 `.env`、凭据、旧 tracker、旧数据库或父级 Git；没有创建 `targets.yaml`。

网络执行结果：

- 准备请求的完整 URL：`https://data-api.polymarket.com/v1/leaderboard?category=CRYPTO&timePeriod=ALL&orderBy=PNL&limit=50&offset=0`
- 沙箱内和获准的网络重试均在 `data-api.polymarket.com` DNS 解析阶段失败。
- 未建立 HTTP 连接，故没有 HTTP 状态、响应 body、记录数或响应 SHA-256。
- 没有 raw 响应可保存，没有生成 `source_requests.jsonl`、候选 JSON 或人工审批报告。
- 未继续请求 `offset=50` 或更高 offset。

离线测试：

- 命令：`python3 -m unittest discover -s tests -v`
- 结果：8 项 Phase 0B 测试全部通过。
- 覆盖：固定 URL/参数、拒绝额外参数、响应契约、地址格式和严格匹配、checksum 非门槛、确定性 gzip、排他写入、五账户外用户隔离。

未解决：

- 需要恢复 `data-api.polymarket.com` 的 DNS/网络可达性后重新执行获准客户端。
- 五个账户仍没有实际 API 候选，无法提供有证据的逐账户审批表。
- `pnl` 和 `vol` 的单位及完整计算口径仍为 `unverified`。
- PNL 的实际排序方向只能在成功响应后记录观察结果，不能作为已确认契约。

### Phase 0B 客户端审计修复

状态：完成（2026-07-24，仅离线修改与测试，未联网）

- collector version 更新为 `phase0b-2`。
- 每个已收到的 HTTP 响应先保存原始 body 和双 SHA-256，再经过统一验证路径恰好写入一条 request log。
- 成功日志记录 `validation_status=passed`、空 validation error 和正确记录数；HTTP、header、JSON 或字段契约失败记录 `validation_status=failed` 和具体错误。
- DNS、TLS 或连接阶段未收到响应时，不创建 raw 或 request log。
- 候选直接携带来源请求证据；相同 lowercase 完整地址跨页重复时合并为一个候选，并保留全部 source observations。
- 只有不同 lowercase 完整地址才构成多个候选。
- 全部离线测试：`python3 -m unittest discover -s tests -v`，16 项通过。
- 语法检查：`python3 -m py_compile src/phase0b.py tests/test_phase0b.py`，通过。

### Phase 0B 正式采集与 Anon 人工补充证据

状态：五个唯一地址候选均已 `ready_for_human_approval`，等待逐账户人工决定（2026-07-24）

- 唯一正式排行榜采集已覆盖 `offset=0,50,...,1000`，停止原因为 `offset_limit_reached`。
- 21 个请求均通过 HTTP、JSON、raw gzip、响应 SHA-256、gzip SHA-256 和记录数离线完整性复核；每页 50 条，共 1,050 条当前排行榜记录。
- `0x8dxd`、`justdance`、`k9Q2mX4L8A7ZP3R`、`Bonereaper` 各有一个严格自动匹配候选。
- Anon 的原始自动状态保留为 `zero_candidates_blocked`：排行榜 API `userName=""`，严格用户名匹配未通过；离线诊断为 `unique_abbreviated_address_match` 和 `unique_address_candidate_username_semantics_unresolved`。
- 用户人工核验官方 Profile URL `https://polymarket.com/profile/0xf705fa045201391d9632b7f3cde06a5e24453ca7`，观察到显示名 `Anon` 且 URL 完整地址与唯一缩写地址候选一致；证据类型为 `official_profile_manual_review`，记录时间 `2026-07-24T06:48:00.792579Z`。
- 该人工证据不是已保存 raw API 响应，也不建立“空 API 用户名必然映射为 Anon”的通用规则。
- Anon 的综合候选状态更新为 `unique_candidate_pending_approval`；没有账户被自动批准。
- 未创建 `config/targets.yaml`，未进入 Phase 0C。

### Phase 0B 最终人工审批与锁定

状态：完成（2026-07-24）

- 五个账户均由用户逐账户明确 `APPROVE`，批准范围仅为 Polymarket User Profile Address。
- 已按用户明确授权的 `phase0b-approved-v1` schema 创建并锁定 `config/targets.yaml`。
- 五个 lowercase 完整地址格式有效且互不相同；每个地址均引用其已有的排行榜 source observation。
- Anon 的批准同时使用 `official_profile_manual_review`；其 API `userName=""`、严格用户名匹配失败、原始 `zero_candidates_blocked`、`unique_abbreviated_address_match` 与 `unique_address_candidate_username_semantics_unresolved` 均完整保留。
- 该批准不认定地址是 EOA、资金来源、最终受益地址、现实身份或共同控制实体，也不建立空 API 用户名到 Anon 的通用映射规则。
- Phase 0B 的 21 个 raw gzip 与 `source_requests.jsonl` 保持不可变。

## Phase 0C：快速通道 MVP

状态：`blocked / partial report delivered`（2026-07-24）

已完成：

- 在 Phase 0B 验收通过后审阅 `PLAN`、`DECISIONS` 与
  `DATA_SOURCES` 的 Phase 0C 强制项和来源门禁。
- 确认当前唯一已批准且已验证的数据 endpoint 是 Phase 0B 排行榜；
  账户行为 endpoint、字段、分页、限流与错误语义仍为待核验。
- 未猜测或请求 Trades、Activity、Positions、CLOB、Profile、Polygon RPC
  或第三方来源；因此没有伪造 Phase 0C raw、request log 或 normalized 数据。
- 实现确定性 Markdown 报告生成器，并生成五篇账户报告、横向总览和数据覆盖
  报告。排行榜值保留为原始观察，行为指标统一标为 `unavailable` 并说明原因。
- 创建 `docs/FUTURE_EXTENSIONS.md`，记录后移功能、数据依赖、难点、风险、
  acceptance criteria 与建议优先级。

未完成及阻断：

- Phase 0C 行为采集、contract smoke test、分页、normalized 数据、行为覆盖与
  行为分析均未完成；阻断原因为没有获批且已定义契约的账户行为 endpoint。
- normalized schema 尚未锁定。没有通过契约验证的源字段时，提前定义映射会
  构成字段或经济含义猜测。
- 首版报告是证据边界明确的部分交付，不得表述为 Phase 0C 完成。

## Phase 0C 恢复、扩展性修复与有界真实 MVP

状态（2026-07-24）：

- Phase 0B: `complete`
- Phase 0C full history: `in_progress`
- Phase 0C bounded MVP: `complete`

恢复审计：

- 恢复时未发现仍运行的旧 phase0c/Python 采集进程。
- Phase 0B 的 `source_requests.jsonl` 为 21 行、21/21 passed；21 个 raw gzip
  共 76,805 bytes。逐条 raw 存在性、解压响应 SHA-256 与 gzip SHA-256
  全部通过，offset 仍为 `0..1000`，证据未修改。
- 恢复时 Phase 0C request log 为 411 行（408 passed、3 failed HTTP 500）；
  日志引用的 411 个 raw 均存在，所有 passed raw 双 SHA-256 正确。
- 另发现 1 个 OOM 时已排他落盘、但进程退出前未写 request log 的 orphan raw。
  文件保留且未纳入 normalized，未删除、覆盖或补造来源元数据。
- 恢复时已有行为证据只覆盖 `0x8dxd`：Trades 405 个成功请求，Activity、
  Positions、Closed Positions 各 1 个 smoke；其他四账户无行为数据。

扩展性与正确性修复：

- request log 每次启动只扫描一次并建立 passed `request_key` 索引；精确成功
  请求复用前再次校验 raw 双 SHA-256，不覆盖 raw。
- 每次运行固定 `run_id`、`snapshot_cutoff_utc` 与
  `snapshot_cutoff_epoch`；最终 MVP 的共同 cutoff 为
  `2026-07-24T09:10:10Z`（epoch `1784884210`）。
- Trades/Activity 显式使用共同 `end` cutoff；按 dataset→五账户 breadth-first。
- 单页验证后立即写 gzip JSONL staging；normalized 按 run、dataset、
  seed_username、UTC year/month 分区，不在 list 中持有账户全历史。
- JSON 小数使用 `Decimal` 并序列化为十进制字符串，未经过 float 往返。
- 重叠窗口按 fingerprint multiset 一对一匹配；同一响应内完全相同记录保留
  其 multiplicity。完整 child window 替代饱和 parent 进入 normalized。
- staging 保留全部来源观察；normalized 行保留 `source_request_id`、
  `source_row_index`、`fingerprint` 与来源观察引用。
- 时间二分按最新 child 优先；HTTP 500 窗口保留失败证据并继续二分。
- Markets 对目标数据发现的 condition IDs 进行批量重复参数查询，并同时覆盖
  `closed=false` 与 `closed=true`。

最终有界 MVP：

- Run ID：`phase0c-mvp-20260724T091010Z-9a644498`
- Trades（0x8dxd / Anon / justdance / k9Q… / Bonereaper）：
  `20,000 / 3,177 / 11,207 / 20,000 / 20,000`
- Activity：`13 / 3,444 / 5,000 / 5,000 / 5,000`
- Current Positions：`3 / 116 / 1,325 / 58 / 3,066`，均由短页终止。
- Closed Positions：`500 / 500 / 500 / 500 / 496` normalized；五账户均按
  最新最多 500 个来源记录触发 capped sample，状态为 partial。
- 最近 30 天无 Trades/Activity 的账户按规则扩展到 180 天；所有账户仍使用
  同一 cutoff。
- Markets：发现 6,617 个 condition IDs，批量双 closed 状态返回 2,823 个
  normalized 市场观察；3,834 个 ID 无返回，Markets coverage=`partial`。
- 报告：`reports/mvp/` 下五账户报告、横向报告和 coverage 报告均以
  `PRELIMINARY PARTIAL-COVERAGE MVP` / `This is not full account history.`
  开头；指标位于 `reports/phase0c/mvp_metrics.json`。

验收：

- 最终离线测试：`python3 -m unittest discover -s tests -v`，29 项通过。
- 语法检查：`python3 -m py_compile src/phase0c.py src/phase0c_report.py
  tests/test_phase0c.py`，通过。
- 最终 Phase 0C request log 为 2,905 行（2,889 passed、16 failed）；
  2,905 个日志引用 raw 全部存在，所有 passed raw 的解压响应 SHA-256 与
  gzip SHA-256 均通过。磁盘仍只有恢复时已记录的 1 个 orphan raw。
- 最终 normalized 逐分区行数与 bounded summary 全部一致，所有行均含
  `source_request_id`、`source_row_index` 与 `fingerprint`。
- `public/` 私有类型与符号链接检查通过；没有 raw、数据库、日志或 normalized
  数据进入公开根目录。
- 未执行 `git add`、`git commit` 或 `git push`。

未解决事项：

- Phase 0C full history 保持 `in_progress`，本轮按要求停止，不继续无上限
  backfill。
- Gamma Markets 对 3,834 个已观察 condition IDs 在 closed 双状态批量查询中
  无返回；已明确标记 partial，未伪造元数据。
- Polygon/RPC、价格历史、独立全历史 PnL 与链上归因不属于本次 bounded MVP。

## Phase 0C bounded MVP 研究质量 QA

状态：完成（2026-07-24）；未启动或继续 full-history backfill。

Market metadata 计数修复：

- 以最终 target normalized 的非空 `condition_id` 重建 requested 集合，而非
  staging parent 集合：`requested_unique_condition_ids=6,480`。
- 查明旧 `6,617 / 2,823 / 3,834` 相差 40 的根因：旧请求集合来自 staging，
  且包含 Activity 的空 `conditionId`。首个 Gamma 双 closed 批次因此带有
  `condition_ids=`；Gamma 忽略该空过滤并分别返回 20 条非请求市场，共 40 条
  unexpected response rows。
- 仅对初审仍 unresolved 的 3,656 个 ID 进行 25-ID 批次、`closed=false/true`
  有界补采；没有请求账户历史，没有覆盖任何 raw。
- 补采后，全部 Gamma raw 审计结果为：`response_market_rows=9,277`、
  `response_unique_condition_ids=6,947`、`resolved_unique_requested_ids=5,752`、
  `unresolved_unique_ids=728`、`duplicate_response_rows=2,330`、
  `unexpected_response_ids=1,195`。
- 恒等式通过：`6,480 = 5,752 + 728`。
- 机器可读结果：`reports/phase0c/market_coverage_audit.json`。

失败请求与覆盖语义：

- 16 个 failed request 已逐条记录 canonical params、window、offset、状态、
  error、replacement IDs、classification 和 coverage impact。
- 15 条存在相同 request_key 的成功替代；1 条 offset=11,000 的 400 由成功
  child windows 替代。分类为 `transient_recovered=16`、
  `terminal_gap=0`。
- 机器可读结果：`reports/phase0c/failed_requests_audit.json`。
- 每账户/endpoint 的配置窗口、观察时间、source/normalized 行数、cap、
  stop reason、saturation 和 window completeness 已写入
  `reports/phase0c/coverage_audit.json`。
- “complete”固定表述为
  `complete within the configured bounded window`；五账户整体均为 partial
  bounded evidence，不代表账户完整历史。
- Bonereaper Closed Positions 可复现为：500 个 source rows 达到来源 cap；
  4 个跨页完全相同观察经 multiset 一对一去重，得到 496 normalized rows。
  因此 `cap_reached=true` 与 normalized=496 并不矛盾。

研究报告 QA：

- 七份报告已确定性重生成，包含 BUY/SELL、频率、间隔、size/notional 分布、
  `Σ(size×price)` 单位边界、HHI 公式、前十大 conditionId、current/bounded
  closed positions、crypto/non-crypto/unknown、共同 conditionId、5/30/60
  分钟同向/反向行为、burst、reversal/churn 和替代解释。
- Overview 的 Coverage 不再把 Trades endpoint 状态当作账户整体状态；明确
  五账户均为 account-level partial bounded evidence。
- Snapshot endpoints 不接受统一 `end` 参数；报告显示所有观察时间（包括
  cutoff 后时间）并明确其限制，未静默截断。
- 报告明确声明这些 exploratory signals 不能证明 insider trading、
  collusion、wash trading、意图、共同控制或现实身份。

最终验收：

- Phase 0C request log：3,183 passed、16 failed、3,199 个引用 raw。
- 全部 3,183 个 passed raw 的响应 SHA-256 与 gzip SHA-256 复核通过，
  integrity errors=0。
- 原 OOM orphan raw 仍为 1 个，保留且不进入 normalized。
- 最终离线测试：
  `python3 -m unittest discover -s tests -v`，35 项全部通过。
- 语法检查：`python3 -m py_compile src/phase0c_qa.py
  tests/test_phase0c_qa.py`，通过。
- 未执行 `git add`、`git commit` 或 `git push`；Phase 0B 未修改。

## 2026-07-24：Phase 0C bounded MVP 本地 HTML v1

状态：**complete（本地静态 HTML；未部署）**

实现：

- 新增确定性构建器 `src/build_site.py`，只读取现有
  `research_metrics.json`、四份 Phase 0C audit JSON，并生成一个公开白名单
  数据文件 `public/data/site-data.json`。
- 生成 Overview、Cross-account、Data Coverage、Glossary 和五个账户详情页；
  所有页面固定显示 `PRELIMINARY PARTIAL-COVERAGE MVP` 与
  `This is not full account history.`。
- 页面包含 BUY/SELL、size/notional/interval 分布、HHI 与 Top 10 markets、
  current/bounded closed positions、burst、reversal/churn、5/30/60 分钟
  pair matches、crypto/non-crypto/unknown 和 endpoint 级覆盖。
- Data Coverage 页面包含 16 个 recovered failures、raw integrity、orphan raw、
  caps/saturation/stop reason、market metadata 恒等式以及旧 40 条差异根因。
- 图表附带研究问题、轴、单位、公式、UTC、样本、覆盖、来源、阅读方式和限制；
  unknown metadata 保持独立，未归入 non-crypto。
- 采用无外部 CDN、字体或运行时接口的响应式静态 HTML/CSS/JS；`public/`
  中没有符号链接、raw、日志、数据库、gzip、Parquet 或私有审计明细。

验证：

- `python3 -m unittest discover -s tests -v`：44 项全部通过。
- 网站测试逐字段核对研究 JSON、conditionId 恒等式、16 个 failure 汇总、
  raw integrity、Bonereaper 500→496 解释、unknown 分类、图表审计字段和
  public 文件白名单。
- 连续两次构建后 `public/` 全部文件 SHA-256 映射一致，确定性通过。
- 沙箱禁止监听本地端口，因此未在执行环境内启动 HTTP server；已提供标准
  `python3 -m http.server` 本地预览方式。
- 没有重新采集数据或启动 full-history backfill；未删除或覆盖 raw；未修改
  Phase 0B；未执行 `git add`、`git commit` 或 `git push`；未公网部署。

## 2026-07-24：Phase 0C bounded MVP 网站发布

状态：**complete（systemd + Tailscale Funnel）**

发布结果：

- 重新完整读取 `AGENTS.md`，确认其未暂存 diff 只是在文件末尾新增一次性
  bounded release exception；该文件保持未暂存且不进入发布 commit。
- 只读核实 PID 3534308 是用户 `ugs` 的
  `python3 dashboard/server.py --host 0.0.0.0 --port 8765`，与本项目无关；
  在新 unit 安装、启用并准备完成后，仅终止该已核实 PID。
- 安装并启用唯一获准的
  `/etc/systemd/system/crypto-top5-site.service`；服务以用户 `ubuntu` 运行，
  document root 固定为项目 `public/`，且只监听 `127.0.0.1:8765`。
- Tailscale Funnel 根路由固定映射到 `http://127.0.0.1:8765`；公网地址为
  `https://vm-0-6-ubuntu.tail8e1e99.ts.net/`，HTTPS 请求验证通过。
- 未修改 cloud security-group 或 SSH 规则，未启动 full-history backfill，
  未发起新数据采集，未删除或覆盖 raw evidence。

发布验收：

- `python3 tests/test_phase0a.py`：通过。旧断言已限定到 Phase 0A 边界文档，
  不再把 Phase 0B 已批准并记录于进度文档的官方 Profile URL 误报为失败。
- `python3 -m unittest discover -s tests -q`：44 项全部通过。
- public 泄露检查：无符号链接、数据库、gzip、Parquet、日志、环境文件、
  auth 文件或疑似凭据。
- systemd：unit 为 enabled/active；监听地址为 `127.0.0.1:8765`。
- Tailscale：Funnel 为 on，根路由映射到 `http://127.0.0.1:8765`。
