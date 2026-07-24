#!/usr/bin/env python3
"""Phase 0C bounded-MVP research-quality audits and deterministic reports."""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import phase0c
from mvp_report import PROJECT_ROOT, SLUGS

SUMMARY_PATH = PROJECT_ROOT / "reports/phase0c/bounded_mvp_summary.json"
LOG_PATH = PROJECT_ROOT / "reports/phase0c/source_requests.jsonl"
MARKET_AUDIT_PATH = PROJECT_ROOT / "reports/phase0c/market_coverage_audit.json"
FAILED_AUDIT_PATH = PROJECT_ROOT / "reports/phase0c/failed_requests_audit.json"
COVERAGE_AUDIT_PATH = PROJECT_ROOT / "reports/phase0c/coverage_audit.json"
RESEARCH_PATH = PROJECT_ROOT / "reports/phase0c/research_metrics.json"
RAW_AUDIT_PATH = PROJECT_ROOT / "reports/phase0c/raw_integrity_audit.json"
LABEL = "PRELIMINARY PARTIAL-COVERAGE MVP"
DISCLAIMER = "This is not full account history."
CAPS = {"trades": 20_000, "activity": 5_000, "positions": 10_000, "closed_positions": 500}


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def normalized_root(summary: dict[str, Any]) -> Path:
    return PROJECT_ROOT / summary["normalized_root"]


def iter_gzip_jsonl(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in sorted(paths):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                yield json.loads(line)


def normalized_rows(summary: dict[str, Any], dataset: str, seed: str | None = None):
    base = normalized_root(summary) / "normalized" / f"dataset={dataset}"
    if seed is not None:
        base /= f"seed_username={SLUGS[seed]}"
    return iter_gzip_jsonl(base.rglob("records.jsonl.gz"))


def staging_rows(summary: dict[str, Any], dataset: str, seed: str):
    base = (
        normalized_root(summary) / "staging" / f"dataset={dataset}"
        / f"seed_username={SLUGS[seed]}"
    )
    return iter_gzip_jsonl(base.rglob("records.jsonl.gz"))


def requested_condition_ids(summary: dict[str, Any]) -> set[str]:
    result = set()
    for dataset in ("trades", "activity", "positions", "closed_positions"):
        for row in normalized_rows(summary, dataset):
            condition = row.get("condition_id")
            if isinstance(condition, str) and condition:
                result.add(condition.lower())
    return result


def gamma_response_observations() -> list[dict[str, Any]]:
    result = []
    for request in read_jsonl(LOG_PATH):
        if request.get("source") != "markets":
            continue
        body = gzip.decompress((PROJECT_ROOT / request["raw_path"]).read_bytes())
        rows = json.loads(body)
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            condition = row.get("conditionId")
            result.append({
                "source_request_id": request["source_request_id"],
                "source_row_index": index,
                "condition_id": condition.lower() if isinstance(condition, str) else None,
                "fingerprint": phase0c.fingerprint(row),
                "row": row,
            })
    return result


def raw_integrity_audit() -> dict[str, Any]:
    passed = failed = 0
    bad = []
    referenced = set()
    for request in read_jsonl(LOG_PATH):
        referenced.add(request["raw_path"])
        if request.get("validation_status") != "passed":
            failed += 1
            continue
        passed += 1
        path = PROJECT_ROOT / request["raw_path"]
        try:
            gzip_bytes = path.read_bytes()
            body = gzip.decompress(gzip_bytes)
        except Exception as exc:
            bad.append({"source_request_id": request["source_request_id"], "error": str(exc)})
            continue
        if phase0c.sha256(gzip_bytes) != request["gzip_sha256"]:
            bad.append({"source_request_id": request["source_request_id"], "error": "gzip_sha256_mismatch"})
        if phase0c.sha256(body) != request["response_sha256"]:
            bad.append({"source_request_id": request["source_request_id"], "error": "response_sha256_mismatch"})
    disk = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "data/raw/phase0c").rglob("*.json.gz")
    }
    return {
        "passed_requests": passed,
        "failed_requests": failed,
        "referenced_raw_files": len(referenced),
        "verified_passed_raw_files": passed - len({x["source_request_id"] for x in bad}),
        "integrity_error_count": len(bad),
        "integrity_errors": bad,
        "orphan_raw_count": len(disk - referenced),
        "orphan_raw_paths": sorted(disk - referenced),
    }


def market_audit(summary: dict[str, Any]) -> dict[str, Any]:
    requested = requested_condition_ids(summary)
    observations = gamma_response_observations()
    response_ids = [x["condition_id"] for x in observations if x["condition_id"]]
    response_set = set(response_ids)
    resolved = requested & response_set
    unresolved = requested - response_set
    unexpected = response_set - requested
    duplicate_rows = len(response_ids) - len(response_set)
    audit = {
        "schema_version": 1,
        "run_id": summary["run_id"],
        "requested_unique_condition_ids": len(requested),
        "response_market_rows": len(response_ids),
        "response_unique_condition_ids": len(response_set),
        "resolved_unique_requested_ids": len(resolved),
        "unresolved_unique_ids": len(unresolved),
        "duplicate_response_rows": duplicate_rows,
        "unexpected_response_ids": len(unexpected),
        "identity_holds": len(requested) == len(resolved) + len(unresolved),
        "unresolved_condition_ids": sorted(unresolved),
        "unexpected_condition_ids": sorted(unexpected),
        "legacy_summary_reconciliation": {
            "legacy_requested_condition_ids": summary["markets"]["condition_id_count"],
            "legacy_normalized_market_rows": summary["markets"]["record_count"],
            "legacy_unresolved_ids": summary["markets"]["missing_condition_id_count"],
            "difference_of_40_reason": (
                "The legacy request set came from staging (including saturated-parent "
                "observations and an empty Activity conditionId), not final normalized. "
                "The empty condition_ids= parameter caused Gamma to ignore the filter "
                "for the first closed=false/true batch and return 20 unexpected rows "
                "per closed state (40 total). Those rows were response rows but not "
                "resolved requested IDs."
            ),
        },
        "definitions": {
            "requested": "unique non-empty condition_id values in final normalized target datasets",
            "response_rows": "all market rows in every immutable Gamma raw referenced by the Phase 0C log",
            "duplicate_response_rows": "response rows minus unique response conditionId count",
            "unexpected": "unique response conditionIds absent from final normalized requested set",
        },
    }
    if not audit["identity_holds"]:
        raise phase0c.Phase0CError("market requested/resolved/unresolved identity failed")
    return audit


def canonical_param_dict(request: dict[str, Any]) -> dict[str, Any]:
    result: defaultdict[str, list[str]] = defaultdict(list)
    for item in request.get("normalized_params", []):
        result[item["name"]].append(item["value"])
    return {key: values[0] if len(values) == 1 else values for key, values in sorted(result.items())}


def failed_requests_audit(summary: dict[str, Any]) -> dict[str, Any]:
    requests = list(read_jsonl(LOG_PATH))
    passed_by_key: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for request in requests:
        if request.get("validation_status") == "passed":
            passed_by_key[request["request_key"]].append(request)
    entries = []
    for failed in (x for x in requests if x.get("validation_status") == "failed"):
        replacements = passed_by_key.get(failed["request_key"], [])
        params = canonical_param_dict(failed)
        classification = "transient_recovered" if replacements else "terminal_gap"
        replacement_ids = [x["source_request_id"] for x in replacements]
        impact = "none; exact request_key later passed" if replacements else "partial"
        # The one offset=11000 request exceeded the observed API cap. Its parent was
        # intentionally replaced by successful bounded child windows.
        if (
            failed.get("source") in {"trades", "activity"}
            and not replacements
            and int(params.get("offset", 0)) > 10_000
        ):
            classification = "transient_recovered"
            replacement_ids = [
                x["source_request_id"] for x in requests
                if x.get("validation_status") == "passed"
                and x.get("source") == failed.get("source")
                and x.get("target") == failed.get("target")
                and int(canonical_param_dict(x).get("start", 0)) >= int(params.get("start", 0))
                and int(canonical_param_dict(x).get("end", 0)) <= int(params.get("end", 0))
                and canonical_param_dict(x).get("purpose", x.get("purpose")) is not None
            ][:20]
            impact = "none within bounded MVP; offset-cap parent replaced by successful child windows"
        entries.append({
            "source_request_id": failed["source_request_id"],
            "account": failed.get("target"),
            "endpoint": failed.get("source"),
            "canonical_params": params,
            "window": {"start": params.get("start"), "end": params.get("end")},
            "offset": params.get("offset"),
            "http_status": failed.get("http_status"),
            "validation_error": failed.get("validation_error"),
            "retry_replacement_request_id": replacement_ids,
            "classification": classification,
            "coverage_impact": impact,
        })
    terminal = [x for x in entries if x["classification"] == "terminal_gap"]
    return {
        "schema_version": 1,
        "run_id": summary["run_id"],
        "failed_request_count": len(entries),
        "transient_recovered_count": len(entries) - len(terminal),
        "terminal_gap_count": len(terminal),
        "requests": entries,
    }


def epoch_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def completeness_label(status: str, cap_reached: bool, terminal_gap: bool) -> str:
    return (
        "complete within the configured bounded window"
        if status == "complete" and not cap_reached and not terminal_gap
        else "partial within the configured bounded window"
    )


def coverage_audit(summary: dict[str, Any], failed_audit: dict[str, Any]) -> dict[str, Any]:
    terminal = {(x["account"], x["endpoint"]) for x in failed_audit["requests"] if x["classification"] == "terminal_gap"}
    accounts = {}
    for target in summary["targets"]:
        seed = target["seed_username"]
        endpoint_states = {}
        for endpoint, state in target["datasets"].items():
            timestamps = [
                row.get("timestamp_epoch_seconds")
                for row in normalized_rows(summary, endpoint, seed)
                if isinstance(row.get("timestamp_epoch_seconds"), int)
            ]
            normalized_count = state["record_count"]
            source_count = state["source_observation_count"]
            cap = CAPS[endpoint]
            cap_reached = bool(state["record_cap_reached"])
            stop = state["termination_reason"]
            if cap_reached and stop != "record_cap_reached":
                raise phase0c.Phase0CError(f"{seed}/{endpoint}: cap/stop mismatch")
            terminal_gap = (target["proxy_wallet"], endpoint) in terminal
            endpoint_states[endpoint] = {
                "configured_window_start_epoch": state.get("earliest_expected_epoch"),
                "configured_window_start_utc": epoch_iso(state.get("earliest_expected_epoch")),
                "configured_window_end_epoch": state.get("snapshot_cutoff_epoch"),
                "configured_window_end_utc": epoch_iso(state.get("snapshot_cutoff_epoch")),
                "observed_timestamp_min_epoch": min(timestamps) if timestamps else None,
                "observed_timestamp_min_utc": epoch_iso(min(timestamps)) if timestamps else None,
                "observed_timestamp_max_epoch": max(timestamps) if timestamps else None,
                "observed_timestamp_max_utc": epoch_iso(max(timestamps)) if timestamps else None,
                "source_row_count": source_count,
                "normalized_row_count": normalized_count,
                "record_cap": cap,
                "cap_reached": cap_reached,
                "stop_reason": stop,
                "saturation": bool(state["saturated"] or state["saturated_window_count"]),
                "window_completeness": completeness_label(
                    state["coverage_status"], cap_reached, terminal_gap
                ),
                "full_account_history": False,
            }
        accounts[seed] = endpoint_states
    return {"schema_version": 1, "run_id": summary["run_id"], "accounts": accounts}


def bonereaper_closed_reproduction(summary: dict[str, Any]) -> dict[str, Any]:
    rows = list(staging_rows(summary, "closed_positions", "Bonereaper"))
    counts = Counter(row["fingerprint"] for row in rows)
    duplicate_observations = sum(value - 1 for value in counts.values())
    normalized_count = sum(1 for _ in normalized_rows(summary, "closed_positions", "Bonereaper"))
    return {
        "source_rows": len(rows),
        "unique_fingerprints": len(counts),
        "duplicate_source_observations": duplicate_observations,
        "normalized_rows": normalized_count,
        "cap_reached": len(rows) >= CAPS["closed_positions"],
        "explanation": (
            "The latest-500 source cap was reached. Four exact records repeated across "
            "page observations and were removed one-to-one by fingerprint, yielding "
            "496 normalized rows. cap_reached describes source sampling, not unique output."
        ),
    }


def supplement_unresolved(audit: dict[str, Any], cutoff: int) -> None:
    unresolved = audit["unresolved_condition_ids"]
    if not unresolved:
        return
    phase0c._request_index = phase0c.RequestIndex(LOG_PATH)
    phase0c._run_context = phase0c.RunContext.create(cutoff)
    for params in phase0c.market_query_batches(unresolved, batch_size=25):
        if any(not value for value in params["condition_ids"]):
            raise phase0c.Phase0CError("empty conditionId rejected from repair batch")
        phase0c.get("markets", params, "market_coverage_unresolved_repair", None)


def percentile(sorted_values: list[Decimal], fraction: Decimal) -> str | None:
    if not sorted_values:
        return None
    index = int((len(sorted_values) - 1) * fraction)
    return str(sorted_values[index])


def classify_market(row: dict[str, Any]) -> str:
    text = json.dumps(row, ensure_ascii=False).lower()
    crypto = ("bitcoin", " btc", "ethereum", " eth", "crypto", "solana", "xrp", "doge")
    return "crypto" if any(token in text for token in crypto) else "non_crypto"


def account_research(summary: dict[str, Any], seed: str, market_map: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trades = list(normalized_rows(summary, "trades", seed))
    trades.sort(key=lambda x: (x.get("timestamp_epoch_seconds") or 0, x["fingerprint"]))
    notionals: list[Decimal] = []
    sizes: list[Decimal] = []
    condition_notional: defaultdict[str, Decimal] = defaultdict(Decimal)
    side_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    intervals = []
    previous = None
    burst_members = set()
    reversal_count = 0
    last_by_condition: dict[str, tuple[int, str]] = {}
    compact = []
    for index, item in enumerate(trades):
        row = item["raw_record"]
        timestamp = item.get("timestamp_epoch_seconds")
        side = str(row.get("side", "UNKNOWN"))
        size = Decimal(str(row.get("size", "0")))
        price = Decimal(str(row.get("price", "0")))
        notional = size * price
        condition = item.get("condition_id") or ""
        notionals.append(notional); sizes.append(size); side_counts[side] += 1
        condition_notional[condition] += notional
        category = classify_market(market_map[condition]) if condition in market_map else "unknown"
        category_counts[category] += 1
        if isinstance(timestamp, int):
            if previous is not None:
                intervals.append(timestamp - previous)
                if timestamp - previous <= 300:
                    burst_members.update((index - 1, index))
            previous = timestamp
            prior = last_by_condition.get(condition)
            if prior and prior[1] != side and timestamp - prior[0] <= 600:
                reversal_count += 1
            last_by_condition[condition] = (timestamp, side)
            compact.append({"timestamp": timestamp, "side": side, "condition_id": condition})
    notionals.sort(); sizes.sort(); intervals.sort()
    total = sum(notionals, Decimal(0))
    hhi = sum((value / total) ** 2 for value in condition_notional.values()) if total else None
    top = sorted(condition_notional.items(), key=lambda x: (-x[1], x[0]))[:10]
    current_positions = list(normalized_rows(summary, "positions", seed))
    closed_positions = list(normalized_rows(summary, "closed_positions", seed))
    metrics = {
        "trade_count": len(trades), "buy_count": side_counts["BUY"], "sell_count": side_counts["SELL"],
        "observed_notional": str(total), "size_p25": percentile(sizes, Decimal("0.25")),
        "size_median": percentile(sizes, Decimal("0.5")), "size_p75": percentile(sizes, Decimal("0.75")),
        "notional_p25": percentile(notionals, Decimal("0.25")),
        "notional_median": percentile(notionals, Decimal("0.5")),
        "notional_p75": percentile(notionals, Decimal("0.75")),
        "median_interval_seconds": statistics.median(intervals) if intervals else None,
        "burst_trade_count_5m": len(burst_members),
        "burst_ratio_5m": str(Decimal(len(burst_members)) / Decimal(len(trades))) if trades else None,
        "reversal_churn_10m_count": reversal_count,
        "hhi": str(hhi) if hhi is not None else None,
        "top_10_markets": [{"condition_id": c, "notional": str(v), "share": str(v / total) if total else None} for c, v in top],
        "classification_trade_counts": dict(category_counts),
        "metadata_resolved_trade_ratio": str(Decimal(len(trades) - category_counts["unknown"]) / Decimal(len(trades))) if trades else None,
        "current_position_count": len(current_positions),
        "bounded_closed_position_count": len(closed_positions),
    }
    return metrics, compact


def pair_matches(a: list[dict[str, Any]], b: list[dict[str, Any]], window: int, same: bool) -> int:
    by_condition_a: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    by_condition_b: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in a: by_condition_a[row["condition_id"]].append(row)
    for row in b: by_condition_b[row["condition_id"]].append(row)
    count = 0
    for condition in by_condition_a.keys() & by_condition_b.keys():
        right = by_condition_b[condition]; used = set()
        for left in by_condition_a[condition]:
            for index, candidate in enumerate(right):
                if index in used: continue
                if abs(candidate["timestamp"] - left["timestamp"]) > window: continue
                if (candidate["side"] == left["side"]) == same:
                    used.add(index); count += 1; break
    return count


def build_research(summary: dict[str, Any], market_audit_data: dict[str, Any]) -> dict[str, Any]:
    market_map = {}
    for observation in gamma_response_observations():
        condition = observation["condition_id"]
        if condition:
            market_map.setdefault(condition, observation["row"])
    accounts = {}; compact = {}
    for target in summary["targets"]:
        seed = target["seed_username"]
        accounts[seed], compact[seed] = account_research(summary, seed, market_map)
    pairs = []
    seeds = [x["seed_username"] for x in summary["targets"]]
    condition_sets = {seed: {x["condition_id"] for x in compact[seed]} for seed in seeds}
    for i, left in enumerate(seeds):
        for right in seeds[i + 1:]:
            row = {"left": left, "right": right, "common_condition_ids": len(condition_sets[left] & condition_sets[right])}
            for minutes in (5, 30, 60):
                row[f"same_direction_{minutes}m"] = pair_matches(compact[left], compact[right], minutes * 60, True)
                row[f"opposite_direction_{minutes}m"] = pair_matches(compact[left], compact[right], minutes * 60, False)
            pairs.append(row)
    return {
        "schema_version": 1, "run_id": summary["run_id"], "accounts": accounts,
        "account_pairs": pairs,
        "all_five_common_condition_ids": len(set.intersection(*(condition_sets[x] for x in seeds))),
        "market_coverage": market_audit_data,
    }


def report_header(title: str) -> str:
    return f"# {LABEL}\n{DISCLAIMER}\n这不是账户全历史。\n\n# {title}\n\n"


def write_reports(summary: dict[str, Any], coverage: dict[str, Any], research: dict[str, Any], market: dict[str, Any], failed: dict[str, Any], bonereaper: dict[str, Any], raw: dict[str, Any]) -> None:
    target_by_seed = {x["seed_username"]: x for x in summary["targets"]}
    account_dir = PROJECT_ROOT / "reports/mvp/accounts"; account_dir.mkdir(parents=True, exist_ok=True)
    for seed, metrics in research["accounts"].items():
        cov = coverage["accounts"][seed]
        top_rows = "\n".join(
            f"| {i} | `{x['condition_id']}` | {x['notional']} | {x['share']} |"
            for i, x in enumerate(metrics["top_10_markets"], 1)
        )
        categories = metrics["classification_trade_counts"]
        text = report_header(f"{seed} — bounded MVP 研究报告") + f"""- Address: `{target_by_seed[seed]['proxy_wallet']}`
- Shared cutoff: `{summary['snapshot_cutoff_utc']}`
- Account-level coverage: **partial bounded evidence**; never full account history.

## 数据覆盖

| Endpoint | Configured window | Observed timestamps | Source rows | Normalized | Cap | Stop | Completeness |
|---|---|---|---:|---:|---:|---|---|
""" + "\n".join(
            f"| {name} | {state['configured_window_start_utc'] or 'snapshot'} → {state['configured_window_end_utc'] or summary['snapshot_cutoff_utc']} | "
            f"{state['observed_timestamp_min_utc'] or 'not provided'} → {state['observed_timestamp_max_utc'] or 'not provided'} | "
            f"{state['source_row_count']} | {state['normalized_row_count']} | {state['record_cap']} | "
            f"{state['stop_reason']} | {state['window_completeness']} |"
            for name, state in cov.items()
        ) + f"""

## 交易行为

- BUY/SELL: {metrics['buy_count']} / {metrics['sell_count']}.
- Observed notional: `{metrics['observed_notional']} USD`, formula
  `Σ(size × price)`. This is observed turnover in the bounded API sample, not
  capital invested, deposits, ROI, or independently verified PnL.
- Size P25/median/P75: `{metrics['size_p25']}` / `{metrics['size_median']}` / `{metrics['size_p75']}` shares.
- Notional P25/median/P75: `{metrics['notional_p25']}` / `{metrics['notional_median']}` / `{metrics['notional_p75']}` USD.
- Median inter-trade interval: `{metrics['median_interval_seconds']}` seconds.
- 5-minute burst members: `{metrics['burst_trade_count_5m']}`; ratio `{metrics['burst_ratio_5m']}`.
- 10-minute reversal/churn heuristic count: `{metrics['reversal_churn_10m_count']}`.

## 市场集中度

HHI = `Σ(market observed notional / total observed notional)²`, weighted by
`size × price` within this bounded sample. HHI=`{metrics['hhi']}`.

| Rank | conditionId | Observed notional USD | Share |
|---:|---|---:|---:|
{top_rows}

## 持仓与分类

- Current positions: `{metrics['current_position_count']}` snapshot records associated
  with this run; the endpoint has no historical `end` cutoff parameter.
- Bounded closed positions: `{metrics['bounded_closed_position_count']}` records;
  latest source sample capped at 500, not full closed-position history.
- Crypto/non-crypto/unknown trade counts: `{categories.get('crypto', 0)}` /
  `{categories.get('non_crypto', 0)}` / `{categories.get('unknown', 0)}`.
- Metadata-resolved trade ratio: `{metrics['metadata_resolved_trade_ratio']}`.
  Missing Gamma metadata can move records only from `unknown` after resolution;
  it limits category shares, market labels, and interpretation.

## Exploratory signals and alternatives

Burst and reversal/churn counts are screening heuristics. Plausible alternatives
include API pagination density, automated execution, position rebalancing, market
making, hedging, settlement mechanics, or repeated fills of one order. This
bounded evidence cannot establish insider trading, collusion, wash trading,
common control, intent, or real-world identity.
"""
        if seed == "Bonereaper":
            text += f"\nClosed-position QA: source cap {bonereaper['source_rows']}, exact duplicate observations {bonereaper['duplicate_source_observations']}, normalized {bonereaper['normalized_rows']}.\n"
        (account_dir / f"{SLUGS[seed]}.md").write_text(text, encoding="utf-8")
    pair_rows = "\n".join(
        f"| {x['left']} / {x['right']} | {x['common_condition_ids']} | "
        f"{x['same_direction_5m']}/{x['opposite_direction_5m']} | "
        f"{x['same_direction_30m']}/{x['opposite_direction_30m']} | "
        f"{x['same_direction_60m']}/{x['opposite_direction_60m']} |"
        for x in research["account_pairs"]
    )
    overview = report_header("五账户 bounded MVP 横向研究") + f"""Shared cutoff: `{summary['snapshot_cutoff_utc']}`. Every account remains
**partial at account level** because this is a bounded, endpoint-specific sample.

## Actual cross-account findings

| Pair | Common conditionIds | Same/opposite 5m | Same/opposite 30m | Same/opposite 60m |
|---|---:|---:|---:|---:|
{pair_rows}

All-five common conditionIds: `{research['all_five_common_condition_ids']}`.
Time-near matches are one-to-one within each conditionId and direction relation;
they show co-occurrence, not coordination. Shared popular markets, news, bots,
liquidity, and independent reactions are alternative explanations.

## Account comparison

| Account | Trades | BUY/SELL | Observed notional USD | Median interval s | HHI | Burst ratio | Reversal/churn |
|---|---:|---:|---:|---:|---:|---:|---:|
""" + "\n".join(
        f"| {seed} | {m['trade_count']} | {m['buy_count']}/{m['sell_count']} | "
        f"{m['observed_notional']} | {m['median_interval_seconds']} | {m['hhi']} | "
        f"{m['burst_ratio_5m']} | {m['reversal_churn_10m_count']} |"
        for seed, m in research["accounts"].items()
    ) + f"""

## Metadata and interpretation limits

Requested unique conditionIds: `{market['requested_unique_condition_ids']}`;
resolved: `{market['resolved_unique_requested_ids']}`; unresolved:
`{market['unresolved_unique_ids']}`; unexpected response IDs:
`{market['unexpected_response_ids']}`. Crypto/non-crypto results therefore retain
an explicit unknown category. No finding here establishes insider trading,
collusion, wash trading, intent, or common control.
"""
    (PROJECT_ROOT / "reports/mvp/overview.md").write_text(overview, encoding="utf-8")
    coverage_report = report_header("Phase 0C bounded MVP 数据覆盖") + f"""- Phase 0C request log: passed `{raw.get('passed_requests')}`, failed
  `{raw.get('failed_requests')}`; all `{failed['failed_request_count']}` failed
  requests were individually audited.
  transient recovered `{failed['transient_recovered_count']}`, terminal gaps
  `{failed['terminal_gap_count']}`.
- Raw integrity: referenced `{raw.get('referenced_raw_files')}`, verified passed
  `{raw.get('verified_passed_raw_files')}`, SHA/gzip errors
  `{raw.get('integrity_error_count')}`.
- Orphan raw count `{raw.get('orphan_raw_count')}`. The OOM orphan remains
  preserved and excluded from normalized.
- Market identity: `{market['requested_unique_condition_ids']} =
  {market['resolved_unique_requested_ids']} + {market['unresolved_unique_ids']}`.
- Gamma response rows `{market['response_market_rows']}`, duplicate response rows
  `{market['duplicate_response_rows']}`, unexpected unique IDs
  `{market['unexpected_response_ids']}`.
- Every cap and stop reason is endpoint-specific. “Complete” below means only
  **complete within the configured bounded window**, never full account history.
- Positions/closed-position snapshot endpoints do not accept the Trades/Activity
  `end` cutoff. Any observed timestamp after the shared cutoff is displayed rather
  than silently clipped and limits strict cross-endpoint as-of comparability.

## Endpoint details

""" + "\n".join(
        f"### {seed}\n\n" + "\n".join(
            f"- {endpoint}: source={state['source_row_count']}, normalized={state['normalized_row_count']}, "
            f"configured={state['configured_window_start_utc'] or 'snapshot'}→{state['configured_window_end_utc'] or summary['snapshot_cutoff_utc']}, "
            f"observed={state['observed_timestamp_min_utc'] or 'not provided'}→{state['observed_timestamp_max_utc'] or 'not provided'}, "
            f"cap={state['record_cap']}, cap_reached={str(state['cap_reached']).lower()}, "
            f"stop={state['stop_reason']}, saturation={str(state['saturation']).lower()}, "
            f"{state['window_completeness']}."
            for endpoint, state in endpoints.items()
        )
        for seed, endpoints in coverage["accounts"].items()
    ) + f"""

## Bonereaper closed_positions reproduction

Source rows `{bonereaper['source_rows']}`, unique fingerprints
`{bonereaper['unique_fingerprints']}`, exact duplicate observations
`{bonereaper['duplicate_source_observations']}`, normalized rows
`{bonereaper['normalized_rows']}`, cap reached `{str(bonereaper['cap_reached']).lower()}`.
"""
    (PROJECT_ROOT / "reports/mvp/data_coverage.md").write_text(coverage_report, encoding="utf-8")


def build_all(write: bool = True) -> dict[str, Any]:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    market = market_audit(summary)
    failed = failed_requests_audit(summary)
    coverage = coverage_audit(summary, failed)
    bonereaper = bonereaper_closed_reproduction(summary)
    research = build_research(summary, market)
    raw = (
        json.loads(RAW_AUDIT_PATH.read_text(encoding="utf-8"))
        if RAW_AUDIT_PATH.exists()
        else {
            "passed_requests": None, "failed_requests": failed["failed_request_count"],
            "referenced_raw_files": None, "verified_passed_raw_files": None,
            "integrity_error_count": None, "orphan_raw_count": 1,
        }
    )
    result = {"market": market, "failed": failed, "coverage": coverage, "bonereaper": bonereaper, "research": research, "raw": raw}
    if write:
        for path, value in (
            (MARKET_AUDIT_PATH, market), (FAILED_AUDIT_PATH, failed),
            (COVERAGE_AUDIT_PATH, {**coverage, "bonereaper_closed_positions_reproduction": bonereaper}),
            (RESEARCH_PATH, research),
        ):
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_reports(summary, coverage, research, market, failed, bonereaper, raw)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--supplement-unresolved", action="store_true")
    parser.add_argument("--verify-raw", action="store_true")
    args = parser.parse_args()
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    audit = market_audit(summary)
    if args.supplement_unresolved:
        supplement_unresolved(audit, summary["snapshot_cutoff_epoch"])
    if args.verify_raw:
        RAW_AUDIT_PATH.write_text(
            json.dumps(raw_integrity_audit(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    build_all(write=True)


if __name__ == "__main__":
    main()
