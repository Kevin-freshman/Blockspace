# 数据来源登记

本文件在 Phase 0A 仅登记计划核验和使用的官方来源。以下 URL 本阶段均未请求；具体 endpoint、参数、字段、分页上限和契约状态全部为 `[待核验]`。

| 计划用途 | 官方来源 | URL | 当前状态 |
|---|---|---|---|
| Polymarket 开发文档与接口契约 | Polymarket Documentation | https://docs.polymarket.com/ | `[待核验，未请求]` |
| 排行榜、账户公开数据与市场元数据 | Polymarket 官方服务（具体 endpoint 待确认） | https://polymarket.com/ | `[待核验，未请求]` |
| CLOB 交易与市场接口 | Polymarket CLOB 文档（具体 endpoint 待确认） | https://docs.polymarket.com/developers/CLOB/introduction | `[待核验，未请求]` |
| Polygon 链、RPC 与最终性规则 | Polygon 官方文档 | https://docs.polygon.technology/ | `[待核验，未请求]` |
| 链上浏览器证据与账户型接口 | PolygonScan | https://polygonscan.com/ | `[待核验，未请求]` |

约束：

- Phase 0A 不发起网络请求，不确认或猜测接口契约。
- 未来只能在用户批准的阶段使用官方来源和经确认的提供商。
- API/RPC 密钥只能来自环境变量，禁止写入配置、日志、数据库、文档或对话。
- 完整地址必须通过正式身份核验获得，不得从缩写地址拼接或猜测。

