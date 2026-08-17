# Polymarket Crypto Filter

一个只读的 Polymarket 地址筛选网页：从官方 `CRYPTO PNL` 排行榜取得候选地址，找出指定时间内交易频率较高的地址，并提供可选收益筛选、排序、实时观察和数据导出。

> **GitHub 仓库不是在线网页。** 已部署的 Filter 通过 Tailscale Funnel 提供独立的公网
> HTTPS 地址；服务器内的应用本身仍只监听 `127.0.0.1:8788`。

## 最快打开网页

直接打开：

<https://vm-0-6-ubuntu.tail8e1e99.ts.net:8443>

这个地址由 Tailscale Funnel 提供 HTTPS，并转发到服务器内的
`http://127.0.0.1:8788`。访问者不需要安装 Tailscale 或建立 SSH 隧道。

### 服务检查

SSH 登录服务器后运行：

```bash
sudo systemctl status polymarket-filter.service
curl http://127.0.0.1:8788/api/health
```

看到 `active (running)`，并且健康接口返回 `"ok": true`，说明服务正常。如果服务没有运行：

```bash
sudo systemctl start polymarket-filter.service
```

### SSH 隧道备用访问

如果公网 Funnel 暂时不可用，可以在自己的电脑上打开一个终端，并保持该终端运行：

```bash
ssh -N -L 8788:127.0.0.1:8788 Blockspace
```

`Blockspace` 是 SSH 配置中的服务器别名。如果没有配置别名，改成实际登录地址：

```bash
ssh -N -L 8788:127.0.0.1:8788 ubuntu@你的服务器地址
```

然后打开 <http://127.0.0.1:8788>。

这里的 `127.0.0.1` 指你自己的电脑，SSH 会把请求安全转发到远程服务器。

## 网页怎么用

### 基础筛选

页面默认只有两个必要字段：

1. **分析时间范围**：可选最近 24 小时、7 天或 30 天。排行榜周期和交易回看窗口使用同一时间范围。
2. **最低交易频率**：单位是“成交记录/天”。例如选择 7 天并填写 `5`，表示只保留最近 7 天平均每天至少有 5 条成交记录的候选地址。

点击 **运行筛选** 后等待进度完成。系统需要读取最多 1,000 个地址的公开活动记录，
扫描时间会比旧版更长，状态变为“实时观察中”后即完成。

### 收益筛选与排序（可选）

展开该区域后可以设置：

- **最低总收益效率**：`同期排行榜 PNL ÷ 同期成交量`，输入单位为百分比。例如 `5` 表示至少 `5%`。
- **最低平均每条成交 PNL**：`同期排行榜 PNL ÷ 同期成交记录数`，单位为 USD。
- **结果排序**：按交易频率、总收益效率、平均每条成交 PNL 或区间 PNL 从高到低排序。

这些选项默认不限制结果。若只想筛选高频地址，不需要展开或填写。

### 查看与导出结果

- **排行榜候选**：本次从官方榜单实际读到的地址数。
- **通过筛选**：满足条件的完整结果数；其中排序靠前的 100 个地址进入详细分析、持仓
  补全和实时观察。
- **实时交易**：页面当前保留的成交活动数。
- 点击地址可打开对应的 Polymarket profile。
- 页面下方的实时活动会自动刷新。

导出入口：

- **地址 CSV**：通过筛选的地址和指标。
- **交易 CSV**：当前保留的实时成交活动。
- **完整 JSON**：配置、候选、结果、进度、错误和实时活动的完整快照。

## “候选地址 1,000”是怎么选的

```text
所选时间范围内的官方 CRYPTO PNL 排行榜
    → 按 PNL 分页取前 1,000 个地址
    → 分别读取这 1,000 个地址的成交活动
    → 计算交易频率
    → 按页面条件筛选和排序
```

Polymarket 官方排行榜只提供 PNL 或成交量排序，不提供全站按交易频率排序。接口单页
最多返回 50 条，并支持分页 offset；本项目把候选池从 200 扩大到 1,000，即最多发起
20 页榜单请求。1,000 仍是有边界的研究范围，不是全站完整地址集合。

因此，结果是“CRYPTO PNL 榜前 1,000 名中的高频地址”，不是“Polymarket 全站最高频
地址”，仍可能遗漏榜外高频地址。

## 扫尾盘策略研究

网页新增“扫尾盘策略研究教室”。这里的“扫尾盘”指：在市场临近计划结束、结果看似
高度确定时，买入高概率结果，尝试赚取成交价到最终兑付值之间的小价差。例如以 0.97
买入最终兑付 1 的合约，理论毛空间只有 0.03，而判断错误时损失可能接近 0.97。

研究流程为：

1. 1,000 个候选地址全部参加频率筛选。
2. 对通过筛选且排序靠前的最多 100 个地址，补齐其最近 12 个不同市场的计划结束时间。
   短周期 Crypto 市场优先根据 slug 中的 Unix 起点和 5m/15m 等周期推导精确结束时间；
   其他市场使用官方结束字段，页面会分别报告两种来源。
3. 统计计划结束前 60 分钟、6 小时和 24 小时的成交数。
4. 对 60 分钟窗口进一步统计成交占比、BUY/SELL、平均价格、价格 ≥0.90 的 BUY 数、
   涉及市场数与名义成交规模。
5. 页面把本次真实结果代入五步教学，先检查时间数据覆盖率，再识别行为模式和价格风险。

这些统计只能发现“尾盘行为”，不能证明策略盈利。推导或官方计划结束时间都不一定
等于实际判定或兑付时间；真实回测还需要最终结果、费用、滑点、未成交订单和跨时间
样本外验证。

参考资料：

- [Polymarket 排行榜 API](https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings)
- [Polymarket 用户活动 API](https://docs.polymarket.com/api-reference/core/get-user-activity)
- [Page & Clemen (2013), prediction-market calibration and time to expiration](https://doi.org/10.1111/j.1468-0297.2012.02561.x)
- [Wolfers & Zitzewitz (2004), Prediction Markets](https://www.aeaweb.org/articles?id=10.1257/0895330041371321)
- [Angelini & De Angelis (2026), real-time information processing，预印本](https://arxiv.org/abs/2606.07811)
- [Xi et al. (2026), prediction-market volatility near resolution，预印本](https://arxiv.org/abs/2607.08199)

## 指标口径

- **成交记录数**：回看窗口内 `/activity?type=TRADE` 返回的记录数。一次链上交易可能对应多条 fill，所以页面也显示去重后的交易哈希数。
- **交易频率**：`窗口内成交记录数 ÷ 窗口天数`。
- **总收益效率**：`官方排行榜同期 PNL ÷ 同期成交量`。适合横向比较，但不是基于投入本金计算的严格 ROI。
- **平均每条成交 PNL（估算）**：`官方排行榜同期 PNL ÷ 同期成交记录数`。它不是完整配对开仓和平仓后得到的单笔真实收益。
- **单笔真实收益率**：暂不提供。公开活动数据无法可靠处理历史持仓成本、部分平仓、剩余仓位和最终结算，强行计算会产生误导。

每个高频地址最多读取 2 页、每页 500 条活动。若达到上限，页面会标记 `capped`：成交记录数和频率只是下限，平均每条成交 PNL 会显示为未知。

## 常见问题

### 浏览器显示“无法访问此网站”或 `ERR_CONNECTION_REFUSED`

依次检查：

1. 服务器上的服务是否为 `active (running)`。
2. 服务器上 `curl http://127.0.0.1:8788/api/health` 是否成功。
3. `tailscale funnel status` 是否仍将公网 `:8443` 转发到 `127.0.0.1:8788`。
4. 浏览器访问的是否是
   `https://vm-0-6-ubuntu.tail8e1e99.ts.net:8443`，而不是 GitHub 文件地址。
5. 若使用备用 SSH 方式，确认隧道终端仍在运行，再访问
   `http://127.0.0.1:8788`。

### 本地端口 8788 已被占用

换一个本地端口，远端端口仍保持 8788：

```bash
ssh -N -L 18788:127.0.0.1:8788 Blockspace
```

然后打开 <http://127.0.0.1:18788>。

### 点击运行筛选后等待较久

系统需要读取最多 1,000 个地址的公开活动记录，并为详细分析样本补齐市场元数据。
部分 API 请求失败时，页面底部会显示诊断信息；单个地址失败不会中断整个扫描。

### 没有地址通过筛选

先降低“最低交易频率”，并确认可选收益阈值为空。`capped` 地址的频率是下限，但仍可通过频率筛选；缺少可靠分母的地址不会通过“平均每条成交 PNL”筛选。

## 服务器安装与维护

首次安装 systemd 服务：

```bash
cd /home/ubuntu/kevin/Blockspace/Polymarket/Filter
sudo install -o root -g root -m 0644 \
  deploy/polymarket-filter.service \
  /etc/systemd/system/polymarket-filter.service
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-filter.service
```

常用命令：

```bash
sudo systemctl status polymarket-filter.service
sudo systemctl restart polymarket-filter.service
sudo journalctl -u polymarket-filter.service -n 100 --no-pager
```

应用默认只监听 `127.0.0.1:8788`，这是有意的安全设置。公网访问由 Tailscale Funnel
终止 HTTPS 并转发；不要把 Python 服务改成直接监听公网网卡。

## 本地开发与测试

服务器已有 Python 3.8 和 `requests` 时无需安装其他依赖：

```bash
cd /home/ubuntu/kevin/Blockspace/Polymarket/Filter
python3 app.py
```

离线测试：

```bash
python3 -m unittest discover -s tests -v
```

网页只在点击“运行筛选”时发起有边界的真实 Polymarket API 请求。项目不连接钱包、不读取密钥，也不包含下单、签名或任何写链功能。

## 项目结构

```text
Filter/
  app.py                 # 标准库 HTTP 服务
  service.py             # 扫描、实时轮询、缓存与导出
  polymarket_client.py   # Polymarket 公开 API 客户端
  polygon_client.py      # 可选的 Polygon 只读回执客户端
  filter_engine.py       # 纯筛选与统计逻辑
  config.json            # 默认参数
  static/                # 无框架网页
  tests/                 # 离线单元测试
  deploy/                # systemd unit
  data/                  # 运行时缓存和快照，不提交、不对外提供
```

## 数据来源

- [Polymarket 排行榜](https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings)
- [用户活动](https://docs.polymarket.com/api-reference/core/get-user-activity)
- [当前持仓](https://docs.polymarket.com/api-reference/core/get-current-positions-for-a-user)
- [速率限制](https://docs.polymarket.com/api-reference/rate-limits)
