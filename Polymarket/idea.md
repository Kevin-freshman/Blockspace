key  words:

poly market

对发掘市场热点有价值

聪明钱模式 这种钱存在哪种Defi

分析范围：

公开的中心化撮合成交 Polymarket

每一个赌局 买yes or no token

链上链下都有

可以内幕交易

step：读文档 prediction

做聚类 模式识别

eg. 套利

eg. 一直赢的赌狗 - 可能是内幕

可以自动化：

1.收益分析

前一百名 前一千名收益最高的链上地址 记录他们的交易

2.让大模型做聚类 分析简单文本

3.挖funding

赌各种东西

crypto模块 - 可套利

先交付MVP  


# 推荐 Prompt 模板

Prompt 不是一句"帮我写代码"，而是一份小型需求文档。好的 prompt 包含：背景、输入、输出、data schema、技术约束、错误处理要求和禁止事项。

#### 需求拆分 Prompt

描述任务目标和 IO，要求先拆模块、定 schema、列接口和测试点，**不要直接写完整代码**。

#### 代码实现 Prompt

提供明确 schema，指定技术栈（如 web3.py + eth_getLogs），要求分块查询、处理 rate limit、读取 config.yaml，不硬编码配置。

#### 调试 Prompt

提供错误日志和当前代码，要求只分析错误原因并给出**最小修改 patch**，不要重写整个项目。

#### Code Review Prompt

重点检查：是否会漏日志/重复计算、decimals 是否正确、是否有重试机制、是否保留 tx_hash/block/timestamp/log_index、是否可复现。

#### 报告生成 Prompt

要求每个结论标注 evidence level（on-chain fact / reasonable inference / unverified hypothesis），禁止写投资建议或把 CEX 入金说成已经卖出。