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

