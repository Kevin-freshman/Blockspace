# Polymarket Crypto Top 5 Analysis

这是一个与旧 tracker 和旧数据库完全隔离的研究项目。研究对象永久固定为用户提供的五个账户种子；当前仅完成 Phase 0A 的安全骨架和未解析种子配置，尚未进行地址解析或数据采集。

## 当前状态

- 唯一种子文件：`config/target_seeds.yaml`
- 五个地址均仅保存用户提供的缩写，状态为 `unresolved`
- `snapshot_time` 为 `unknown`，且 `snapshot_as_of_utc` 为 `null`
- 没有创建 `targets.yaml`、数据库或采集器
- 没有调用任何外部 API

## 项目边界

实施以 [`docs/PLAN.md`](docs/PLAN.md) 和 [`docs/DECISIONS.md`](docs/DECISIONS.md) 为准。私有运行数据位于 `data/`、`logs/` 和 `reports/`；只有 `public/` 可作为未来静态站点根目录，且不得包含数据库、raw 数据、日志、凭据或指向私有目录的符号链接。

阶段记录与下一步门禁见 [`docs/PROGRESS.md`](docs/PROGRESS.md)，计划来源见 [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)。

