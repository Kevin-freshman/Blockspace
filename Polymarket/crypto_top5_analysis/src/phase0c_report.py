#!/usr/bin/env python3
"""Build auditable metrics and Markdown reports from one bounded normalized run."""

from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from mvp_report import PROJECT_ROOT, SLUGS

LABEL = "PRELIMINARY PARTIAL-COVERAGE MVP"
DISCLAIMER = "This is not full account history."


def decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal(0)


def iter_partition(root: Path, dataset: str, seed: str):
    slug = SLUGS[seed]
    base = root / f"dataset={dataset}" / f"seed_username={slug}"
    for path in sorted(base.rglob("records.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                yield json.loads(line)


def trade_metrics(root: Path, seed: str) -> dict[str, Any]:
    count = 0
    notional = Decimal(0)
    sides: Counter[str] = Counter()
    days: set[str] = set()
    markets: defaultdict[str, Decimal] = defaultdict(Decimal)
    earliest = latest = None
    for item in iter_partition(root, "trades", seed):
        row = item["raw_record"]
        timestamp = item.get("timestamp_epoch_seconds")
        count += 1
        value = decimal_value(row.get("size")) * decimal_value(row.get("price"))
        notional += value
        sides[str(row.get("side", "UNKNOWN"))] += 1
        condition = item.get("condition_id")
        if condition:
            markets[condition] += value
        if isinstance(timestamp, int):
            day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
            days.add(day)
            earliest = timestamp if earliest is None else min(earliest, timestamp)
            latest = timestamp if latest is None else max(latest, timestamp)
    hhi = None
    if notional:
        hhi = sum((value / notional) ** 2 for value in markets.values())
    return {
        "trade_count": count,
        "notional_usd_decimal": str(notional),
        "buy_count": sides["BUY"],
        "sell_count": sides["SELL"],
        "active_days": len(days),
        "market_count": len(markets),
        "market_hhi_decimal": str(hhi) if hhi is not None else None,
        "earliest_timestamp_epoch": earliest,
        "latest_timestamp_epoch": latest,
    }


def build_metrics(summary: dict[str, Any], root: Path) -> dict[str, Any]:
    normalized = PROJECT_ROOT / summary["normalized_root"] / "normalized"
    accounts = {}
    for target in summary["targets"]:
        seed = target["seed_username"]
        metrics = trade_metrics(normalized, seed)
        metrics["coverage"] = target["datasets"]
        accounts[seed] = metrics
    return {
        "run_id": summary["run_id"],
        "snapshot_cutoff_utc": summary["snapshot_cutoff_utc"],
        "coverage_label": LABEL,
        "full_history": False,
        "accounts": accounts,
        "markets": summary["markets"],
    }


def fmt_epoch(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def account_markdown(seed: str, address: str, metrics: dict[str, Any]) -> str:
    coverage = metrics["coverage"]
    return f"""# {LABEL}
{DISCLAIMER}

# {seed} — 有界真实数据 MVP

- User Profile Address: `{address}`
- Snapshot cutoff (UTC): `{fmt_epoch(coverage['trades']['snapshot_cutoff_epoch'])}`
- Trades coverage: `{coverage['trades']['coverage_status']}`; window={coverage['trades']['window_days']} days; cap_reached={str(coverage['trades']['record_cap_reached']).lower()}
- Activity coverage: `{coverage['activity']['coverage_status']}`; window={coverage['activity']['window_days']} days; cap_reached={str(coverage['activity']['record_cap_reached']).lower()}
- Current Positions: `{coverage['positions']['coverage_status']}`
- Closed Positions: `{coverage['closed_positions']['coverage_status']}`; latest capped sample only

## 可审计基础指标

| 指标 | 结果 |
|---|---:|
| Trades | {metrics['trade_count']} |
| Active UTC days | {metrics['active_days']} |
| BUY / SELL | {metrics['buy_count']} / {metrics['sell_count']} |
| Observed notional (Decimal USD) | {metrics['notional_usd_decimal']} |
| Distinct condition IDs | {metrics['market_count']} |
| Market HHI | {metrics['market_hhi_decimal'] or 'unavailable'} |
| Earliest / latest trade UTC | {fmt_epoch(metrics['earliest_timestamp_epoch'])} / {fmt_epoch(metrics['latest_timestamp_epoch'])} |
| Activity records | {coverage['activity']['record_count']} |
| Current position records | {coverage['positions']['record_count']} |
| Closed position records | {coverage['closed_positions']['record_count']} |

## 解释边界

这些数值只描述本次有界样本，不是账户全历史。达到 cap 的数据集是
`partial`；Closed Positions 是最新最多 500 条。Observed notional 是
`size × price` 的 Decimal 汇总，不是投入资本或 ROI。未执行链上归因，也不据此
作内幕交易、对敲、共同控制或现实身份判断。
"""


def write_outputs(summary_path: Path) -> None:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = build_metrics(summary, PROJECT_ROOT)
    metrics_path = PROJECT_ROOT / "reports/phase0c/mvp_metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    by_seed = {item["seed_username"]: item for item in summary["targets"]}
    accounts_dir = PROJECT_ROOT / "reports/mvp/accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True)
    for seed, account in metrics["accounts"].items():
        target = by_seed[seed]
        (accounts_dir / f"{SLUGS[seed]}.md").write_text(
            account_markdown(seed, target["proxy_wallet"], account),
            encoding="utf-8",
        )
    rows = []
    for seed, account in metrics["accounts"].items():
        trades = account["coverage"]["trades"]
        rows.append(
            f"| {seed} | {account['trade_count']} | {account['active_days']} | "
            f"{account['notional_usd_decimal']} | {account['market_count']} | "
            f"{account['market_hhi_decimal'] or 'unavailable'} | "
            f"{trades['coverage_status']} |"
        )
    overview = f"""# {LABEL}
{DISCLAIMER}

# 五账户有界真实数据横向报告

- Run ID: `{metrics['run_id']}`
- Shared snapshot cutoff: `{metrics['snapshot_cutoff_utc']}`
- Markets: requested condition IDs={metrics['markets']['condition_id_count']},
  returned={metrics['markets']['record_count']},
  missing={metrics['markets']['missing_condition_id_count']},
  coverage=`{metrics['markets']['coverage_status']}`;
  both `closed=false` and `closed=true` were queried in batches.

| Account | Trades | Active UTC days | Observed notional | Markets | HHI | Coverage |
|---|---:|---:|---:|---:|---:|---|
""" + "\n".join(rows) + """

## 边界

所有比较均为同一 cutoff 下的 bounded MVP。`partial` 与 cap 必须按账户解释；
这不是 full history，不将 capped sample 外推为账户长期行为。
"""
    (PROJECT_ROOT / "reports/mvp/overview.md").write_text(overview, encoding="utf-8")
    coverage_lines = []
    for target in summary["targets"]:
        parts = ", ".join(
            f"{name}={state['coverage_status']}"
            f"(n={state['record_count']},cap={str(state['record_cap_reached']).lower()})"
            for name, state in target["datasets"].items()
        )
        coverage_lines.append(f"- {target['seed_username']}: {parts}")
    coverage = f"""# {LABEL}
{DISCLAIMER}

# MVP 数据覆盖

- Run ID: `{summary['run_id']}`
- Shared snapshot cutoff: `{summary['snapshot_cutoff_utc']}`
- Phase 0B: complete (21 immutable raw gzip; 21 passed request logs).
- Phase 0C full history: in_progress.
- Phase 0C bounded MVP: complete with explicitly partial/capped datasets.

""" + "\n".join(coverage_lines) + f"""

- Markets: `{summary['markets']['coverage_status']}`; requested
  {summary['markets']['condition_id_count']}, returned
  {summary['markets']['record_count']}, missing
  {summary['markets']['missing_condition_id_count']}.
"""
    (PROJECT_ROOT / "reports/mvp/data_coverage.md").write_text(
        coverage, encoding="utf-8"
    )


if __name__ == "__main__":
    write_outputs(PROJECT_ROOT / "reports/phase0c/bounded_mvp_summary.json")
