# Polymarket Crypto Filter

一个轻量、只读的 Polymarket 地址筛选器：从官方 `CRYPTO` 排行榜取得候选地址，
按交易次数、交易频率和交易距离计划结算时间进行筛选，再通过 Polygon 主网交易回执
验证筛选地址，持续轮询通过筛选的地址，展示实时交易与当前持仓摘要，并导出 CSV/JSON。

## 数据流

```text
CRYPTO 排行榜（分页，默认 200 / 最大 1000）
    │
    ├─ Data API /activity ── 交易次数、频率
    │
    ├─ CLOB /markets/{condition_id} ── 计划结算时间
    │
    └─ 前置 Filter ── 候选地址 ── Polygon eth_getTransactionReceipt
                                      │
                                      └─ 链上 Filter ── /activity 实时轮询
                                                        ├─ /positions 持仓摘要
                                                        └─ CSV / JSON 导出
```

数据来自公开、免鉴权的 Polymarket API 和 Polygon 官方文档列出的公开主网 RPC。
项目只调用 `eth_chainId` 与 `eth_getTransactionReceipt`，不连接钱包、不读取密钥，
也不包含下单、签名或任何写链功能。

## 筛选口径

- **交易次数**：回看窗口内 `/activity?type=TRADE` 返回的交易记录数。一次链上交易
  可能对应多条 fill，因此页面同时显示去重后的交易哈希数。
- **交易频率**：`窗口内交易记录数 / 回看窗口天数`。
- **交易距离结算时间**：`market.end_date_iso - trade.timestamp`。地址筛选使用可获得
  结算时间且交易发生在计划结算前的记录中位数；计划结算后的交易另行计数，不进入
  该中位数。
- **结算覆盖率**：成功取得计划结算时间的筛选内交易记录占比。为保持轻量，每个候选
  地址默认只补齐最近 12 个不同市场；页面明确显示覆盖率，不能把抽样中位数当成完整
  历史统计。
- **链上确认率**：对通过次数、频率和结算距离条件的地址，默认抽样最近 8 个不同交易
  哈希，通过 Polygon 主网 `eth_getTransactionReceipt` 确认交易存在且状态为成功。
- **官方合约命中率**：回执的目标地址或日志地址命中 Polymarket 官方公布的 CTF
  Exchange、Neg Risk Exchange、Adapter、Conditional Tokens 或 pUSD 合约的比例。
  默认只展示该指标，不以它淘汰地址；可在页面调整最低命中率。
- **排行榜 PNL/成交量**：直接展示官方排行榜值，不在本项目中重算。

高频地址默认最多读取 2 页、每页 500 条活动。如果达到上限，结果会标记 `capped`，
提示交易次数和频率是下限。

## 运行

服务器已有 Python 3.8 和 `requests` 时无需安装额外依赖：

```bash
cd /home/ubuntu/kevin/Blockspace/Polymarket/Filter
python3 app.py
```

默认仅监听 `127.0.0.1:8788`。在本机新开一个终端建立 SSH 隧道：

```powershell
ssh -L 8788:127.0.0.1:8788 Blockspace
```

然后访问 `http://127.0.0.1:8788`。

如需临时监听所有网卡，必须先确认服务器防火墙和访问控制：

```bash
python3 app.py --host 0.0.0.0 --port 8788
```

## 测试

普通测试完全离线：

```bash
python3 -m unittest discover -s tests -v
```

启动服务后可以做本机健康检查：

```bash
curl http://127.0.0.1:8788/api/health
```

网页点击“运行筛选”才会发起有边界的真实 API 请求。

## 目录

```text
Filter/
  app.py                 # 标准库 HTTP 服务
  service.py             # 扫描、实时轮询、缓存与导出
  polymarket_client.py   # 公开 API 客户端与重试
  polygon_client.py      # Polygon 主网只读 JSON-RPC 客户端
  filter_engine.py       # 纯筛选/统计逻辑
  config.json            # 默认参数
  static/                # 无框架网页
  tests/                 # 离线单元测试
  deploy/                # 可选 systemd unit
  data/                  # 运行时缓存/快照，已 gitignore
```

## 导出

- `/api/export/addresses.csv`：通过筛选的地址与指标
- `/api/export/trades.csv`：当前保留的实时交易记录
- `/api/export/snapshot.json`：筛选配置、地址、交易、进度与错误的完整快照

## 官方接口

- 排行榜：<https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings>
- 用户活动：<https://docs.polymarket.com/api-reference/core/get-user-activity>
- 当前持仓：<https://docs.polymarket.com/api-reference/core/get-current-positions-for-a-user>
- CLOB 市场：<https://docs.polymarket.com/api-reference/markets/get-market-by-id>
- 速率限制：<https://docs.polymarket.com/api-reference/rate-limits>
- Polymarket 合约地址：<https://docs.polymarket.com/resources/contracts>
- Polygon PoS RPC：<https://docs.polygon.technology/pos/reference/rpc-endpoints>
