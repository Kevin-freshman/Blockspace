# Polymarket Crypto Filter

一个轻量、只读的 Polymarket 地址筛选器：从官方 `CRYPTO` PNL 排行榜取得固定候选池，
按交易频率和可选收益效率筛选、排序，持续轮询通过筛选的地址，并导出 CSV/JSON。

## 数据流

```text
同周期 CRYPTO PNL 排行榜前 200 名（每页 50 条）
    │
    ├─ Data API /activity ── 交易频率
    ├─ 榜单 PNL / 成交量 ── 收益效率
    └─ Filter / sort ── /activity 实时轮询
                       ├─ /positions 持仓摘要
                       └─ CSV / JSON 导出
```

数据来自公开、免鉴权的 Polymarket API。项目不连接钱包、不读取密钥，也不包含下单、
签名或任何写链功能。代码仍支持只读 Polygon 回执验证，但默认关闭且不作为筛选条件。

## 筛选口径

- **候选池**：所选周期内官方 `CRYPTO` PNL 榜前 200 名。它不是全站按交易频率选出的
  前 200 名；官方榜单不支持按频率排序，频率只在这 200 个候选内计算和排序。200 是
  控制请求量与扫描耗时的默认上限，不是统计抽样结论。
- **交易记录数**：回看窗口内 `/activity?type=TRADE` 返回的交易记录数。一次链上交易
  可能对应多条 fill，因此页面同时显示去重后的交易哈希数。
- **交易频率**：`窗口内交易记录数 / 回看窗口天数`。
- **总收益效率**：`官方榜单区间 PNL / 同期成交量`。它是横向比较指标，不是基于投入
  本金的严格 ROI。可作为可选最低阈值，也可用于结果排序。
- **平均每条成交 PNL（估算）**：`官方榜单区间 PNL / 同期成交记录数`。它可选筛选、
  排序，但不是配对后的单笔收益；活动达到读取上限时不计算。
- **单笔真实收益率**：暂不展示。公开活动行不足以可靠配对开仓、部分平仓、剩余成本
  和最终结算；在缺少完整成本账本时给出单笔 ROI 会产生误导。

高频地址默认最多读取 2 页、每页 500 条活动。如果达到上限，结果会标记 `capped`，
提示交易记录数和频率是下限。

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
