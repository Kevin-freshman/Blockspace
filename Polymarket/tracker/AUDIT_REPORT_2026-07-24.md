# Polymarket Tracker 只读审计报告

审计日期：2026-07-24
项目：`/home/ubuntu/kevin/Blockspace/Polymarket/tracker`

## 一、结论摘要

- 本轮未修改、创建、删除或移动项目文件；未安装依赖，未启动任何采集、同步、PNL、网页服务或网络请求。
- Git 当前位于 `main`，本地跟踪引用未显示 ahead/behind；没有已跟踪文件修改或暂存修改。
- 正确数据库结构健康，`PRAGMA quick_check` 返回 `ok`。
- 数据库实际上只有候选地址和单日排行榜快照；成交、市场、持仓、PNL 等核心分析数据全部为空。
- 当前数据库不足以完成可靠的 Crypto Top 5 账户分析。
- `viz` 是未跟踪的静态快照，包含 1,392 个地址，但无法由当前数据库复现；页面声称使用的 `export_for_viz.py` 不存在。
- `config.yaml` 中存在疑似真实 RPC 凭据且文件被 Git 跟踪，属于最高优先级安全风险；本报告不复述其内容。
- `http.log` 显示静态目录曾受到公网扫描，而且日志文件自身曾被成功读取。
- 受当前沙箱 PID namespace 和权限限制，无法确认宿主机的 HTTP 服务及 Funnel 当前状态。

## 二、Git 状态与项目结构

### Git 状态

```text
分支：main
上游：origin/main
已跟踪修改：无
暂存修改：无
```

未跟踪文件：

```text
Polymarket/tracker/viz/dashboard.html
Polymarket/tracker/viz/data.js
Polymarket/tracker/viz/data.json
Polymarket/tracker/viz/http.log
tracker/data/smartmoney.db
```

最新本地提交：

```text
d9bcdd872848b64c3634b5960a185159660ce47c
2026-07-11T16:05:22-07:00
prompt plan added
```

### 主要结构

```text
tracker/
├── main.py
├── README.md
├── implementation-notes.md
├── requirements.txt
├── config.yaml
├── data/
│   └── smartmoney.db
├── smartmoney/
│   ├── config.py
│   ├── db.py
│   ├── seed.py
│   ├── ingest_api.py
│   ├── ingest.py
│   ├── markets.py
│   ├── pnl.py
│   ├── profile.py
│   ├── tracker.py
│   ├── verify.py
│   └── util.py
└── viz/
    ├── dashboard.html
    ├── data.js
    ├── data.json
    └── http.log
```

其他结构问题：

- 仓库跟踪了 12 个 `smartmoney/__pycache__/*.pyc` 文件。
- 项目范围内没有有效 `.gitignore`。
- 未找到 `export_for_viz.py`。
- 未发现已生成的 `profiles.md` 或 `profiles.csv`。
- `viz` 四个文件全部未跟踪。

## 三、数据库审计

### 文件与 Git 状态

正确数据库：

```text
路径：Polymarket/tracker/data/smartmoney.db
大小：749,568 字节
状态：已被 Git 跟踪
工作树差异：无
忽略状态：未忽略
```

错误数据库：

```text
路径：tracker/data/smartmoney.db
大小：0 字节
状态：未跟踪、未忽略
```

按照要求，仅检查了错误数据库的文件元数据和 Git 状态，没有打开或查询它。

### 连接方式与健康状态

正确数据库使用以下形式连接：

```text
file:///home/ubuntu/kevin/Blockspace/Polymarket/tracker/data/smartmoney.db?mode=ro&immutable=1
```

`immutable=1` 用于防止当前较旧的 SQLite CLI 尝试创建 journal/旁路文件，连接仍明确包含 `mode=ro`。

健康检查：

- `PRAGMA quick_check`：`ok`
- `PRAGMA foreign_key_check`：无错误输出
- 页大小：4,096 字节
- 页数：183
- 空闲页：1
- journal mode：`delete`
- 表：10 张
- 视图：0 个

### 表结构、行数和时间范围

| 表 | 行数 | 字段结构 | 时间范围 |
|---|---:|---|---|
| `address_market_pnl` | 0 | `address TEXT NOT NULL PK1`, `condition_id TEXT NOT NULL PK2`, `cash_net REAL`, `buy_volume REAL`, `n_fills INTEGER`, `first_buy_ts INTEGER`, `resolved INTEGER`, `won INTEGER` | 无数据 |
| `address_stats` | 0 | `address TEXT PK`, `computed_at TEXT`, `n_fills`, `n_markets`, `n_resolved`, `wins INTEGER`, `win_rate`, `buy_volume`, `sell_volume`, `cash_net`, `position_value`, `total_pnl`, `realized_pnl`, `avg_entry_price`, `median_entry_lead_h`, `late_buy_share`, `official_pnl`, `pnl_diff REAL` | 无数据 |
| `block_times` | 0 | `block_number INTEGER PK`, `ts INTEGER NOT NULL` | 无数据 |
| `candidates` | 1,749 | `address TEXT PK`, `username TEXT`, `added_at TEXT NOT NULL` | `added_at` 最早=最晚=`2026-07-08` |
| `ctf_events` | 0 | `tx_hash TEXT NOT NULL PK1`, `log_index INTEGER NOT NULL PK2`, `block_number INTEGER NOT NULL`, `ts INTEGER`, `kind TEXT NOT NULL`, `address TEXT NOT NULL`, `condition_id TEXT NOT NULL`, `collateral TEXT`, `parent_zero INTEGER NOT NULL`, `amount REAL NOT NULL` | 无数据 |
| `fills` | 0 | `tx_hash TEXT NOT NULL PK1`, `log_index INTEGER NOT NULL PK2`, `block_number INTEGER NOT NULL`, `ts INTEGER`, `exchange TEXT NOT NULL`, `order_hash TEXT`, `maker TEXT NOT NULL`, `taker TEXT`, `maker_asset_id TEXT NOT NULL`, `taker_asset_id TEXT NOT NULL`, `maker_amount REAL NOT NULL`, `taker_amount REAL NOT NULL`, `fee REAL NOT NULL` | 无数据 |
| `leaderboard_snapshots` | 2,033 | `address TEXT PK1`, `category TEXT PK2`, `time_period TEXT PK3`, `rank INTEGER`, `pnl REAL`, `vol REAL`, `fetched_at TEXT PK4` | 最早=最晚=`2026-07-08` |
| `markets` | 0 | `condition_id TEXT PK`, `question`, `slug`, `category`, `end_date TEXT`, `end_ts INTEGER`, `closed INTEGER`, `resolved INTEGER`, `winner_outcome`, `outcome_prices`, `clob_token_ids TEXT`, `neg_risk INTEGER`, `updated_at TEXT` | 无数据 |
| `positions_cache` | 0 | `address TEXT NOT NULL PK1`, `token_id TEXT NOT NULL PK2`, `size`, `cur_price`, `value REAL`, `title`, `fetched_at TEXT` | 无数据 |
| `sync_state` | 0 | `stream TEXT PK`, `last_block INTEGER NOT NULL` | 无同步游标 |
| `token_map` | 0 | `token_id TEXT PK`, `condition_id TEXT NOT NULL`, `outcome TEXT`, `outcome_index INTEGER` | 无时间字段 |

索引包括：

- `idx_fills_maker`
- `idx_ctf_addr`
- 各表主键自动索引

### 排行榜覆盖

| 类别 | 周期 | 日期 | 行数 | 唯一地址 | Rank 范围 |
|---|---|---|---:|---:|---|
| CRYPTO | ALL | 2026-07-08 | 500 | 500 | 1–500 |
| CRYPTO | MONTH | 2026-07-08 | 519 | 519 | 1–500 |
| OVERALL | ALL | 2026-07-08 | 500 | 500 | 1–500 |
| OVERALL | MONTH | 2026-07-08 | 514 | 514 | 1–500 |

补充：

- 唯一候选地址：1,749
- 排行榜唯一地址：1,749
- candidates 与 snapshots 地址集合完全对应
- 缺失用户名：87
- MONTH 两组行数超过配置的名义 `max_per_board=500`；只读审计无法确认形成原因
- 审计日期距离唯一快照日期已有 16 天

### 数据覆盖判断

当前数据库仅覆盖“2026-07-08 的排行榜候选集合”，不覆盖：

- 账户历史交易
- split/merge/redeem/reward/rebate
- token 到市场映射
- 市场截止及揭晓信息
- 当前持仓和估值
- 地址级 PNL
- 地址市场级收益拆解
- 区块时间
- 链上或 API 同步游标

因此它不是一个已经完成同步的分析数据库。

## 四、现有采集和分析链路

主要链路：

```text
Data API leaderboard
  → candidates + leaderboard_snapshots

Data API /activity
  → fills + ctf_events
  → token_map + markets 的初始标题

CLOB /markets/{condition_id}
  → 市场截止、分类、揭晓状态、winner

Data API /positions
  → positions_cache

fills + ctf_events + markets + positions_cache
  → address_stats + address_market_pnl
  → profiles.md / profiles.csv
```

备用链路：

```text
Polygon RPC eth_getLogs
  → V1/V2 CTF Exchange、NegRisk、ConditionalTokens 事件
  → fills / ctf_events / block_times / sync_state
```

入口命令：

```text
python main.py seed
python main.py sync
python main.py verify
python main.py sync-chain [--reset]
python main.py probe
python main.py markets
python main.py pnl [--no-positions]
python main.py profile
python main.py run
python main.py status
```

本轮均未执行。

特别注意：即使 `status` 表面上是查询命令，`db.connect()` 仍会：

- 创建数据库父目录
- 执行 `PRAGMA journal_mode=WAL`
- 执行完整 `CREATE TABLE/INDEX IF NOT EXISTS`

因此它不是严格只读命令，本轮没有运行。

依赖只有：

```text
web3>=6.0.0
requests>=2.31.0
PyYAML>=6.0
```

没有锁定具体版本或提供 lock 文件。

## 五、HTML 数据流

入口文件是 `viz/dashboard.html`。

数据加载顺序：

1. 从 jsDelivr CDN 加载 Chart.js 4.4.3。
2. 加载本地 `data.js`。
3. 如果 `window.DASHBOARD_DATA` 已存在，直接使用。
4. 否则通过 `fetch("data.json")` 加载同目录 JSON。

前端组成：

- CSS：全部内嵌在 `dashboard.html`
- 页面逻辑：全部内嵌 JavaScript
- 图表库：硬编码外部 CDN URL
- 数据：`data.js` 和 `data.json`
- 没有后端 API、WebSocket 或数据库直连
- HTML 中未发现硬编码钱包地址或内嵌样例数组
- `data.js` 与 `data.json` 语义内容相同

静态数据快照：

```text
generated_at：2026-07-12T07:41:00+00:00
地址数：1,392
盈利地址：1,162
分类数：143
top markets：500
```

该快照与当前数据库不一致：

- 网页：1,392 个已计算地址
- 当前数据库：`address_stats=0`
- 页面快照生成于 7 月 12 日
- 当前数据库修改时间为 7 月 14 日
- 生成器 `export_for_viz.py` 不存在

因此无法确认这些网页数据来自哪个数据库版本，也无法从当前项目复现。

## 六、HTTP Server 与 Funnel

### HTTP Server

在当前可见进程命名空间中：

- 未发现 `python -m http.server`
- 未发现常见端口上的 Python 监听

但当前审计运行在隔离 PID namespace 中，无法看到宿主机全部进程，所以不能据此断言宿主机没有运行 HTTP 服务。

`http.log` 覆盖：

```text
2026-07-14 17:35:26 ～ 2026-07-21 22:22:53
```

日志显示：

- `/dashboard.html`、`/data.js`、`data.json` 曾成功访问
- 大量针对 `.env`、`.git`、AWS credentials、PHP/WordPress 文件的自动化扫描
- `/http.log` 自身曾返回 `200` 三次

这说明静态目录至少曾暴露给非可信扫描流量，但无法仅凭日志确定是 Funnel、端口映射还是其他代理造成。

### Tailscale Funnel

执行 `tailscale funnel status` 结果：

```text
无法连接 /var/run/tailscale/tailscaled.sock：operation not permitted
```

socket 文件存在，但沙箱禁止访问；systemd 状态同样不可查询。因此 Funnel 当前状态为：**无法确认**。

## 七、Git 与安全风险

按优先级排序：

1. **严重：疑似真实 RPC 凭据已存在于被跟踪的 `config.yaml`。**
   即使随后删除，Git 历史仍可能保留，应立即轮换凭据并审查历史。

2. **严重：`http.log` 曾被 HTTP 服务直接公开。**
   日志不应放在公开静态目录内。

3. **高：正确数据库被 Git 跟踪。**
   数据刷新会形成大体积二进制差异，并可能把地址分析数据永久写入历史。

4. **高：错误数据库未跟踪且未忽略。**
   容易被程序因相对路径错误再次使用或误提交。

5. **高：`viz` 全部未跟踪。**
   当前页面、数据和日志没有版本或来源保证。

6. **中：12 个 `__pycache__/*.pyc` 被跟踪。**

7. **中：项目没有有效 `.gitignore`。**

8. **中：外部 CDN 为硬编码依赖。**
   页面打开时会产生网络请求；CDN 不可用时图表无法正常工作。

## 八、阻塞完整 Crypto Top 5 分析的问题

主要阻塞项：

- `fills=0`、`ctf_events=0`：没有账户交易和现金流。
- `markets=0`、`token_map=0`：无法做市场级归因。
- `positions_cache=0`：无法计算当前持仓价值。
- `address_stats=0`、`address_market_pnl=0`：没有可分析画像。
- 只有单日、已过期的排行榜快照。
- “Crypto Top 5”尚需明确采用 `CRYPTO/ALL` 还是 `CRYPTO/MONTH`。
- `_official_pnl()` 只过滤 `time_period='ALL'`，没有过滤 `category='CRYPTO'`；地址同时出现在 OVERALL 和 CRYPTO 时，可能选中错误榜单 PNL。
- 静态网页快照与当前数据库不一致且无法复现。
- 前端导出脚本缺失。
- Data API 的高频地址受每地址页数上限影响，历史可能被截断。
- 默认回填窗口为 180 天，不等于官方 ALL 全期。
- `CONVERSION` 明确未纳入现金流。
- combo/parlay 市场可能缺少完整元数据。
- 当前无法确认网页暴露和 Funnel 是否已经关闭。

## 九、下一阶段建议

1. 立即轮换疑似泄露的 RPC 凭据，并将真实配置与可提交模板分离。
2. 增加 `.gitignore`，覆盖数据库、日志、静态导出、`.env`、凭据文件和 `__pycache__`；是否保留现有数据库历史需单独决定。
3. 明确 Crypto Top 5 的榜单周期，并修正官方 PNL 查询，使其同时过滤 `category='CRYPTO'`。
4. 在获得明确网络和写入授权后，按 `seed → sync → markets → pnl` 顺序重建可验证数据库。
5. 每阶段检查行数、时间边界、失败地址、分页上限和市场映射率，再进行 Top 5 分析。
6. 补充并版本化前端导出程序，写入源数据库哈希、导出时间、榜单周期和覆盖窗口。
7. 不要从包含数据库或日志的目录直接启动 `http.server`；单独建立最小公开目录。
8. 将 Chart.js 本地化或明确接受 CDN 网络依赖。
9. Funnel 状态需要在宿主机有权限的终端中另行确认。

## 十、本轮执行过的命令清单

只读命令类别如下：

- `pwd`
- `git rev-parse --show-toplevel`
- `git branch --show-current`
- `git status --short --branch`
- `git status --porcelain=v1 --untracked-files=all`
- `git ls-files`
- `git check-ignore -v --no-index`
- `git diff --stat`
- `git diff --cached --stat`
- `git diff --quiet`
- `git log -1 --format=...`
- `rg --files`
- `rg -n` 静态搜索入口、URL、数据加载和服务地址
- `stat`
- `file`
- `namei -l`
- `ls -la`
- `wc -l`
- `sed -n`
- `awk` 汇总配置键名和 HTTP 请求路径
- `sha256sum`
- `test -e export_for_viz.py`
- `sqlite3 -version`
- `sqlite3 -help`
- `sqlite3 -readonly file:///...smartmoney.db?mode=ro&immutable=1`
- SQLite 查询：`quick_check`、`foreign_key_check`、表/视图、`table_info`、schema、索引、行数、时间边界、区块范围、排行榜覆盖、PRAGMA 页信息
- `sqlite3 file:jsonaudit?mode=memory&cache=shared`，只读解析静态 JSON 摘要
- `ps -eo ...`
- `ss -ltnp`
- `timeout 10s tailscale funnel status`
- `stat /var/run/tailscale/tailscaled.sock`
- `systemctl is-active tailscaled`
- `systemctl is-enabled tailscaled`

两次辅助解析尝试因工具不存在而失败：

```text
node: command not found
jq: command not found
```

最初未加 `immutable=1` 的三次 SQLite URI 连接返回“unable to open database file”，随后改用仍含 `mode=ro` 的 `immutable=1` URI 成功完成审计。最终 Git 状态与审计开始时一致。
