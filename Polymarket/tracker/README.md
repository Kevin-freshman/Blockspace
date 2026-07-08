# Polymarket 聪明钱追踪器（MVP）

Polymarket 聪明钱追踪与分析工具：用官方排行榜圈定候选地址，用 Data API 重建这些
地址的交易史（链上抽样校验），用现金流法计算收益，产出供人工审阅的账户画像表。

## 数据流（混合方案）

```
排行榜 API（候选地址）──┐
Data API /activity ─────┼──> SQLite ──> 收益引擎（现金流法）──> 画像表 profiles.md
CLOB API（市场元数据）──┘       ↑                                └─> 人工审阅发现模式
Polygon RPC（抽样校验）─────────┘ verify
```

为什么是混合方案：Alchemy 免费档把 `eth_getLogs` 限到 10 块/次，公共节点更差，
纯链上回填在免费额度内不可行。官方 Data API 免费、免鉴权、按地址直查，每条记录
仍带 `transactionHash`，用 `verify` 命令随机抽样对照链上回执核验真实性。
纯链上采集保留为 `sync-chain` 命令（需要付费档 RPC）。

## 安装

```powershell
cd F:\Blockspace\Polymarket\tracker
pip install -r requirements.txt
```

## 配置（重要）

编辑 `config.yaml`：

1. `ingest.backfill_days`：历史回填天数。天数越大首次回填越慢。
2. `data_api.max_activity_pages_per_address`：单地址翻页上限（默认 400 页 = 20 万条），
   防止超高频做市地址拖死回填；超限地址会在日志里标出并丢弃更早历史。
3. **RPC（可选）**：只有 `verify` 抽样校验和备用的 `sync-chain` 需要。
   Alchemy 免费档够 `verify` 用；`sync-chain` 需要付费档（免费档 getLogs 限 10 块）。
4. `seed.max_per_board`：每个榜单最多取多少名（默认 500）。

## 使用

```powershell
python main.py seed      # 1. 拉排行榜候选地址
python main.py sync      # 2. Data API 回填/增量同步（首次数小时，中断重跑续传）
python main.py verify    # 3.（可选）链上抽样校验 API 数据真实性
python main.py markets   # 4. 补齐市场元数据（截止时间/揭晓结果/胜出方/类别）
python main.py pnl       # 5. 现金流法算收益（同时从 Data API 拉当前持仓）
python main.py profile   # 6. 生成画像表 output\profiles.md
python main.py status    # 查看数据库统计
python main.py run       # 持续追踪循环（把 2/4/5/6 定时自动跑）
```

产出：`output/profiles.md`（人工审阅用画像表 + 前 N 名明细）、`output/profiles.csv`。

拿到 `profiles.md` 后，按 `../updated_prompt.md` 第 3 条「账户画像与分析 Prompt」
把表贴给 LLM 做开放式归纳，再自己逐行审阅发现模式。

## 口径与已知局限（重要）

- **PnL = 现金流净额 + 当前持仓市值**。现金流 = 卖出 + merge + 赔付 + 奖励/返佣
  − 买入 − split，来自 Data API `/activity` 全量账户活动。
- **地址级现金流用全量成交计算**（与市场映射无关）；市场级拆解（胜率、单市场盈亏）
  只覆盖已映射到市场的部分。combo/parlay 合成市场在 CLOB 上查不到元数据，
  但标题在采集时已入库，其成交同样计入地址级现金流。
- **数据窗口 = backfill_days**，窗口外的历史交易不计入，因此与官方"全期" PnL 有偏差；
  画像表里把偏差过大的地址标了出来，官方数字仅作交叉参考。
- 超高频地址受 `max_activity_pages_per_address` 限制，更早历史被丢弃（日志有标注）。
- 胜率只统计已揭晓市场（该市场现金流净额 > 0 记一胜）。
- CONVERSION（负风险转换）暂未纳入现金流，重度负风险玩家数字会有偏差。
- 排行榜返回的是 proxy wallet；资金来源/CEX 流向追踪不在 MVP 范围内。
- 新增候选地址后重跑 `sync` 即可（按地址记录同步位点，幂等去重）。
- Data API 属于 Polymarket 官方托管服务，字段口径可能变化；定期跑 `verify`
  抽样对照链上回执。

## 项目结构

```
tracker/
  main.py                 # CLI 入口
  config.yaml             # 全部配置
  smartmoney/
    config.py             # 配置加载
    db.py                 # SQLite schema 与存储
    util.py               # HTTP 重试 / 速率控制
    seed.py               # 排行榜候选地址
    ingest_api.py         # Data API 采集（主数据源）
    ingest.py             # 链上事件采集（备用，sync-chain）
    verify.py             # 链上抽样校验
    markets.py            # CLOB 市场元数据
    pnl.py                # 现金流法收益引擎
    profile.py            # 画像表生成
    tracker.py            # 定时循环
  data/smartmoney.db      # SQLite（自动创建）
  output/profiles.md      # 画像表（自动生成）
```
