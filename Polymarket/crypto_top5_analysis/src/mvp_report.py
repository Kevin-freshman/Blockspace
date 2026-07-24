#!/usr/bin/env python3
"""Deterministically render the evidence-bounded MVP Markdown reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SLUGS = {
    "0x8dxd": "0x8dxd",
    "Anon": "anon",
    "justdance": "justdance",
    "k9Q2mX4L8A7ZP3R": "k9q2mx4l8a7zp3r",
    "Bonereaper": "bonereaper",
}


def load_inputs(root: Path = PROJECT_ROOT) -> tuple[dict[str, Any], dict[str, Any]]:
    targets = yaml.safe_load((root / "config" / "targets.yaml").read_text())
    candidates = json.loads(
        (root / "reports" / "phase0b" / "identity_candidates.json").read_text()
    )
    return targets, candidates


def unavailable(reason: str) -> str:
    return f"`unavailable` — {reason}"


def account_report(target: dict[str, Any], candidate: dict[str, Any]) -> str:
    name = target["seed_username"]
    address = target["proxy_wallet"]
    observed = candidate["candidates"][0]
    behavior_reason = (
        "Phase 0C 尚无 DATA_SOURCES.md 批准的账户行为 endpoint 与字段契约，"
        "因此未请求 Trades、Activity、Positions 或其他账户历史。"
    )
    anon_note = ""
    if name == "Anon":
        anon_note = (
            "\n## Anon 身份证据边界\n\n"
            "- 排行榜 API 原始 `userName` 是空字符串，严格用户名匹配为 false。\n"
            "- 原始自动状态为 `zero_candidates_blocked`；诊断标记为 "
            "`unique_abbreviated_address_match` 和 "
            "`unique_address_candidate_username_semantics_unresolved`。\n"
            "- 用户通过 `official_profile_manual_review` 补充核验特定官方 Profile "
            "URL 显示名为 Anon。该证据不构成空用户名的通用映射规则。\n"
        )
    return f"""# {name} — MVP 基础分析

## 身份与范围

- Seed username：`{name}`
- Seed abbreviated address：`{target['seed_abbreviated_address']}`
- Approved User Profile Address：`{address}`
- Approval scope：`polymarket_user_profile_address`
- 地址不被认定为 EOA、资金来源、最终受益地址或现实身份。

## 已有官方观察

| 字段 | 原始观察值 | 口径 |
|---|---:|---|
| rank | {observed['observed_rank']} | `CRYPTO / ALL / PNL` 当前排行榜 |
| pnl | {observed['observed_pnl_raw']} | 单位及完整计算口径未核验 |
| vol | {observed['observed_vol_raw']} | 单位及完整计算口径未核验 |

来源请求：`{observed['source_request_id']}`
响应 SHA-256：`{observed['response_sha256']}`
{anon_note}
## 行为数据覆盖

{unavailable(behavior_reason)}

## 基础指标

| 指标 | 结果 |
|---|---|
| 覆盖起止时间 | {unavailable(behavior_reason)} |
| 记录数 | {unavailable(behavior_reason)} |
| 活跃天数与交易频率 | {unavailable(behavior_reason)} |
| 买入/卖出分布 | {unavailable(behavior_reason)} |
| 总成交量与典型交易规模 | {unavailable(behavior_reason)} |
| 市场集中度 | {unavailable(behavior_reason)} |
| 最大市场敞口 | {unavailable(behavior_reason)} |
| 官方账户行为 PnL | {unavailable(behavior_reason)} |
| 临近结算行为 | {unavailable(behavior_reason)} |
| 可重复交易模式 | {unavailable(behavior_reason)} |

## 结论边界

现有证据只支持身份候选审批和一次当前排行榜观察，不支持交易策略、市场敞口、
同步行为、资金关系或异常交易结论。任何 insider trading、wash trading、
collusion 等判断均不可用，且未来也只能作为 exploratory heuristic 或
anomaly signal，并要求额外证据。
"""


def overview_report(
    targets: dict[str, Any], candidates: dict[str, Any]
) -> str:
    by_name = {item["seed_username"]: item for item in candidates["targets"]}
    rows = []
    for target in targets["targets"]:
        candidate = by_name[target["seed_username"]]["candidates"][0]
        rows.append(
            f"| {target['seed_username']} | `{target['proxy_wallet']}` | "
            f"{candidate['observed_rank']} | {candidate['observed_pnl_raw']} | "
            f"{candidate['observed_vol_raw']} | `unavailable` |"
        )
    return """# 五账户 MVP 横向比较

## 可用证据

Phase 0B 已锁定五个 Polymarket User Profile Address，并保存一次
`CRYPTO / ALL / PNL` 排行榜观察。Phase 0C 行为采集未开始，因为
`DATA_SOURCES.md` 尚无获批的账户行为 endpoint、分页契约和字段定义。

| 账户 | User Profile Address | 当前 rank | pnl 原始值 | vol 原始值 | 行为覆盖 |
|---|---|---:|---:|---:|---|
""" + "\n".join(rows) + """

## 横向指标

| 比较项 | 结果 |
|---|---|
| 五账户行为数据覆盖 | `unavailable` — 无获批行为 endpoint |
| 活跃度 | `unavailable` — 无交易时间序列 |
| 交易规模 | `unavailable` — 无逐笔 size/price |
| 市场集中度 | `unavailable` — 无 market-level 成交 |
| 共同参与市场 | `unavailable` — 无账户市场历史 |
| 时间接近的同向或反向行为 | `unavailable` — 无可比 UTC 交易时间 |
| 探索性异常特征 | `unavailable` — 不以排行榜数值替代行为证据 |

## 解释边界

排行榜 rank、pnl 和 vol 只作为原始当前观察值展示；其单位和完整计算口径仍未
核验。不得从这些值推断违法行为、共同控制、现实身份或资金关系。
"""


def coverage_report(targets: dict[str, Any]) -> str:
    rows = "\n".join(
        f"| {target['seed_username']} | 身份/排行榜：partial | 行为：blocked | "
        "无获批账户行为 endpoint |"
        for target in targets["targets"]
    )
    return f"""# MVP 数据覆盖

## Phase 0B

- 排行榜请求：21 页，offset `0..1000`，每页 50 条。
- 终止原因：`offset_limit_reached`。
- 证据完整性：21 个 raw gzip、响应 SHA-256、gzip SHA-256 与 request log
  已通过离线复核。
- 该结果仅覆盖接口允许的本次当前范围，不代表历史排行榜完整覆盖。

## Phase 0C

状态：`blocked`

阻断原因：`DATA_SOURCES.md` 没有批准任何账户行为 endpoint；Trades、
Activity、Positions、市场元数据和价格历史的正式路径、参数、分页、字段、
限流与错误语义仍未核验。为避免猜测接口，本轮没有发送 Phase 0C HTTP 请求，
没有创建虚假的 raw、request log 或 normalized 行为记录。

| 账户 | 数据集状态 | 行为覆盖状态 | 原因 |
|---|---|---|---|
{rows}

## Normalized 数据

`not_created` — 没有通过契约验证的源记录，因此不存在可合法标准化的行为数据。
去重与引用完整性检查为 `not_applicable`，不能误报为 complete。
"""


def render_reports(root: Path = PROJECT_ROOT) -> dict[str, str]:
    targets, candidates = load_inputs(root)
    by_name = {item["seed_username"]: item for item in candidates["targets"]}
    reports = {
        "reports/mvp/overview.md": overview_report(targets, candidates),
        "reports/mvp/data_coverage.md": coverage_report(targets),
    }
    for target in targets["targets"]:
        name = target["seed_username"]
        reports[f"reports/mvp/accounts/{SLUGS[name]}.md"] = account_report(
            target, by_name[name]
        )
    return reports


def write_reports(root: Path = PROJECT_ROOT) -> list[str]:
    reports = render_reports(root)
    for relative, content in reports.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return sorted(reports)


if __name__ == "__main__":
    for output in write_reports():
        print(output)
