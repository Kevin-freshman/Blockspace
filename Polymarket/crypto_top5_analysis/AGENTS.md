# 项目执行边界

本项目的实施必须同时遵守：

- [`docs/PLAN.md`](docs/PLAN.md)：分阶段计划、数据模型与验收方案。
- [`docs/DECISIONS.md`](docs/DECISIONS.md)：不得静默改变的项目边界与设计决策。

若任务提示、PLAN 和 DECISIONS 之间存在冲突，必须停止并向用户报告，不得自行选择。

## 安全边界

- `crypto_top5_analysis` 是唯一允许读取和写入的项目目录。
- 禁止读取、连接、复制、导入、参考或修改任何旧 tracker 项目、旧 `smartmoney.db`、旧数据库、旧配置或凭据。
- 明确禁止访问：
  - `/home/ubuntu/kevin/Blockspace/Polymarket/tracker`
  - `/home/ubuntu/kevin/Blockspace/tracker`
  - `/home/ugs`
- 禁止读取任何旧 `config.yaml`、`auth.json`、`.env`、RPC key、API key 或其他凭据。
- 私有数据只能位于非公开目录；只有 `public/` 可作为静态发布根目录。禁止符号链接、路径穿越或复制将 `data/`、`logs/`、`reports/`、数据库、raw 数据、密钥或凭据暴露到 `public/`。

## 固定研究范围

- 只允许研究 `config/target_seeds.yaml` 中固定的五个账户，不得按排行榜变化替换或增加目标。
- 在五个完整地址经严格验证前，不得创建或锁定 `targets.yaml`，不得使用缩写地址执行账户采集，也不得猜测完整地址。
- 禁止递归采集外部地址的历史。

## 工作流

- 禁止自动执行 `git add`、`git commit` 或 `git push`。
- 每个阶段完成后必须运行与该阶段相称的本地测试，并将执行结果、当前状态和未解决事项更新到 `docs/PROGRESS.md`。
- 普通测试必须离线运行；任何真实接口契约测试都必须由用户明确授权。

