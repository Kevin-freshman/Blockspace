# 数据来源登记

本文件登记计划核验和使用的官方来源。Phase 0B 排行榜契约已由用户依据官方文档明确确认；首次连接尝试曾在 DNS 解析阶段失败，之后唯一一次正式采集成功完成并保存 21 页不可变响应证据。

| 计划用途 | 官方来源 | URL | 当前状态 |
|---|---|---|---|
| Polymarket 排行榜接口契约 | Polymarket Documentation | https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings | `[Phase 0B 契约已由用户确认；本轮未抓取文档 HTML]` |
| Phase 0B 排行榜身份解析 | Polymarket Data API | https://data-api.polymarket.com/v1/leaderboard | `[已完成；21 页响应证据已保存并验收]` |
| Phase 0C Trades | Polymarket Data API | https://data-api.polymarket.com/trades | `[bounded MVP 已完成；真实响应契约已验证，full history 仍 in_progress]` |
| Phase 0C Activity | Polymarket Data API | https://data-api.polymarket.com/activity | `[bounded MVP 已完成；真实响应契约已验证，full history 仍 in_progress]` |
| Phase 0C Current Positions | Polymarket Data API | https://data-api.polymarket.com/positions | `[bounded MVP 已完成]` |
| Phase 0C Closed Positions | Polymarket Data API | https://data-api.polymarket.com/closed-positions | `[bounded latest-500 sample 已完成；不得视为 full history]` |
| Phase 0C Markets | Polymarket Gamma API | https://gamma-api.polymarket.com/markets | `[bounded MVP 已查询 closed=false/true；部分 condition ID 无返回，coverage=partial]` |
| CLOB 交易与市场接口 | Polymarket CLOB 文档（具体 endpoint 待确认） | https://docs.polymarket.com/developers/CLOB/introduction | `[待核验，未请求]` |
| Polygon 链、RPC 与最终性规则 | Polygon 官方文档 | https://docs.polygon.technology/ | `[待核验，未请求]` |
| 链上浏览器证据与账户型接口 | PolygonScan | https://polygonscan.com/ | `[待核验，未请求]` |

约束：

- Phase 0A 不发起网络请求，不确认或猜测接口契约。
- 未来只能在用户批准的阶段使用官方来源和经确认的提供商。
- API/RPC 密钥只能来自环境变量，禁止写入配置、日志、数据库、文档或对话。
- 完整地址必须通过正式身份核验获得，不得从缩写地址拼接或猜测。
- Phase 0B 固定使用 `category=CRYPTO`、`timePeriod=ALL`、`orderBy=PNL`、`limit=50`，并从 `offset=0` 按 50 递增；官方文档未明确承诺 PNL 排序方向。
- `rank`、`proxyWallet`、`userName`、`vol`、`pnl` 是已确认的响应字段；`pnl` 和 `vol` 的单位及完整计算口径仍为 `unverified`。
- Phase 0C bounded MVP 已由用户于 2026-07-24 明确授权继续真实采集；上述
  Data API/Gamma endpoint 的响应、字段和实际 offset 行为均保留 raw 与 request
  log。该授权和观察不等于 full-history 完整性证明，也不授权 Polygon/RPC。
