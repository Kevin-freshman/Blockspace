"""Pure longitudinal leaderboard research logic.

The module deliberately separates observations, findings, and insights.  It
never treats a change in a rolling leaderboard value as incremental PNL and it
never infers private strategy settings from public fills.
"""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from filter_engine import infer_interval_end_timestamp, parse_timestamp, utc_iso


SCHEMA_VERSION = 1
ALLOWED_TOP_K = (100, 500, 1000)

FINGERPRINT_FIELDS = (
    ("trades_per_hour", "成交频率 / 小时"),
    ("records_per_transaction", "每笔链上交易的成交记录"),
    ("median_trade_usdc", "成交规模中位数"),
    ("market_count", "涉及市场数"),
    ("market_concentration", "市场集中度 HHI"),
    ("buy_share", "BUY 占比"),
    ("average_price", "平均成交价"),
    ("high_price_buy_share", "高价 BUY 占比"),
    ("tail_60m_share", "结束前 60 分钟占比"),
)


def research_slot_epoch(timestamp: float, cadence_hours: int) -> int:
    cadence = max(1, int(cadence_hours)) * 3600
    value = int(timestamp)
    return value - value % cadence


def research_slot_id(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def build_research_snapshot(
    slot_epoch: int,
    captured_epoch: int,
    period: str,
    pnl_rows: Iterable[Dict[str, Any]],
    volume_rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "category": "CRYPTO",
        "time_period": str(period).upper(),
        "slot_epoch": int(slot_epoch),
        "slot_at": utc_iso(slot_epoch),
        "captured_at": utc_iso(captured_epoch),
        "complete": True,
        "boards": {
            "PNL": normalize_leaderboard_rows(pnl_rows),
            "VOL": normalize_leaderboard_rows(volume_rows),
        },
        "rank_checks": {},
        "enrichment": None,
        "analysis": None,
        "errors": [],
    }


def normalize_leaderboard_rows(
    rows: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    normalized = []
    seen = set()
    for raw in rows:
        address = str(raw.get("proxyWallet") or raw.get("address") or "").lower()
        if len(address) != 42 or not address.startswith("0x") or address in seen:
            continue
        seen.add(address)
        normalized.append(
            {
                "address": address,
                "user_name": raw.get("userName") or raw.get("user_name") or "",
                "rank": _integer(raw.get("rank")),
                "pnl": _number(raw.get("pnl")),
                "volume": _number(raw.get("vol", raw.get("volume"))),
                "verified": bool(
                    raw.get("verifiedBadge", raw.get("verified", False))
                ),
            }
        )
    normalized.sort(key=lambda item: item.get("rank") or 10 ** 12)
    return normalized


def analyze_leaderboard_transition(
    previous: Dict[str, Any],
    current: Dict[str, Any],
    top_k: int,
) -> Dict[str, Any]:
    if top_k not in ALLOWED_TOP_K:
        raise ValueError("top_k must be 100, 500, or 1000")
    previous_pnl = _board_map(previous, "PNL")
    current_pnl = _board_map(current, "PNL")
    current_volume = _board_map(current, "VOL")
    rank_checks = current.get("rank_checks") or {}
    previous_top = {
        address
        for address, row in previous_pnl.items()
        if row.get("rank") is not None and int(row["rank"]) <= top_k
    }
    current_top = {
        address
        for address, row in current_pnl.items()
        if row.get("rank") is not None and int(row["rank"]) <= top_k
    }
    entered = sorted(current_top - previous_top, key=lambda value: current_pnl[value]["rank"])
    retained = sorted(current_top & previous_top, key=lambda value: current_pnl[value]["rank"])
    dropped = sorted(previous_top - current_top, key=lambda value: previous_pnl[value]["rank"])

    def transition_row(address: str, state: str) -> Dict[str, Any]:
        before = previous_pnl.get(address) or {}
        after = current_pnl.get(address) or rank_checks.get(address) or current_volume.get(address) or {}
        exact_rank = current_pnl.get(address) or rank_checks.get(address) or {}
        current_rank = exact_rank.get("rank")
        current_pnl_value = after.get("pnl")
        reason = None
        if state == "dropped":
            if current_pnl_value is None:
                reason = "current_metric_unknown"
            elif float(current_pnl_value) <= 0:
                reason = "current_pnl_non_positive"
            elif (
                before.get("pnl") is not None
                and float(current_pnl_value) < float(before["pnl"])
            ):
                reason = "official_window_pnl_lower"
            else:
                reason = "relative_rank_competition"
        return {
            "state": state,
            "address": address,
            "user_name": after.get("user_name") or before.get("user_name") or "",
            "previous_rank": before.get("rank"),
            "current_rank": current_rank,
            "current_rank_exact": address in current_pnl or address in rank_checks,
            "previous_pnl": before.get("pnl"),
            "current_pnl": current_pnl_value,
            "previous_volume": before.get("volume"),
            "current_volume": after.get("volume"),
            "visible_reason": reason,
        }

    rows = (
        [transition_row(address, "dropped") for address in dropped]
        + [transition_row(address, "entered") for address in entered]
        + [transition_row(address, "retained") for address in retained]
    )
    reason_counts: Dict[str, int] = {}
    for row in rows:
        reason = row.get("visible_reason")
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "top_k": top_k,
        "previous_slot_at": previous.get("slot_at"),
        "current_slot_at": current.get("slot_at"),
        "entered_count": len(entered),
        "retained_count": len(retained),
        "dropped_count": len(dropped),
        "retention_rate": round(len(retained) / len(previous_top), 4)
        if previous_top
        else None,
        "reason_counts": reason_counts,
        "rows": rows,
    }


def choose_research_cohorts(
    previous: Dict[str, Any],
    current: Dict[str, Any],
    top_k: int,
    cohort_limit: int,
) -> Dict[str, List[str]]:
    transition = analyze_leaderboard_transition(previous, current, top_k)
    dropped_addresses = [
        row["address"] for row in transition["rows"] if row["state"] == "dropped"
    ]
    entered_addresses = [
        row["address"] for row in transition["rows"] if row["state"] == "entered"
    ]
    retained_addresses = [
        row["address"] for row in transition["rows"] if row["state"] == "retained"
    ]
    previous_pnl = _board_map(previous, "PNL")
    retained_controls = _volume_match_addresses(
        dropped_addresses,
        retained_addresses,
        previous_pnl,
        previous_pnl,
        cohort_limit,
    )[1]

    current_volume = _board_map(current, "VOL")
    losers = [
        address for address, row in current_volume.items()
        if row.get("pnl") is not None and float(row["pnl"]) < 0
    ]
    winners = [
        address for address, row in current_volume.items()
        if row.get("pnl") is not None and float(row["pnl"]) > 0
    ]
    matched_losers, matched_winners = _volume_match_addresses(
        losers, winners, current_volume, current_volume, cohort_limit
    )
    return {
        "dropped": dropped_addresses[:cohort_limit],
        "retained_control": retained_controls,
        "entered": entered_addresses[:cohort_limit],
        "volume_losers": matched_losers,
        "volume_winners": matched_winners,
    }


def build_strategy_fingerprint(
    trades: Iterable[Dict[str, Any]],
    start_timestamp: int,
    end_timestamp: int,
    truncated: bool = False,
) -> Dict[str, Any]:
    rows = []
    for raw in trades:
        timestamp = parse_timestamp(raw.get("timestamp"))
        if timestamp is None or timestamp < start_timestamp or timestamp > end_timestamp + 300:
            continue
        price = _optional_number(raw.get("price"))
        size = _optional_number(raw.get("usdcSize"))
        if size is None:
            token_size = _optional_number(raw.get("size"))
            if token_size is not None and price is not None:
                size = token_size * price
        condition_id = str(raw.get("conditionId") or "").lower()
        transaction_hash = str(raw.get("transactionHash") or "").lower()
        settlement = infer_interval_end_timestamp(raw)
        minutes_to_end = None
        if settlement is not None:
            minutes_to_end = (settlement - timestamp) / 60.0
        rows.append(
            {
                "timestamp": timestamp,
                "price": price,
                "usdc_size": size,
                "condition_id": condition_id,
                "transaction_hash": transaction_hash,
                "side": str(raw.get("side") or "").upper(),
                "minutes_to_end": minutes_to_end,
            }
        )
    rows.sort(key=lambda item: item["timestamp"], reverse=True)
    hashes = list(
        dict.fromkeys(
            row["transaction_hash"]
            for row in rows
            if len(row["transaction_hash"]) == 66
            and row["transaction_hash"].startswith("0x")
        )
    )
    markets = {row["condition_id"] for row in rows if row["condition_id"]}
    sizes = [float(row["usdc_size"]) for row in rows if row["usdc_size"] is not None]
    prices = [float(row["price"]) for row in rows if row["price"] is not None]
    buys = [row for row in rows if row["side"] == "BUY"]
    known_end = [
        row for row in rows
        if row["minutes_to_end"] is not None and row["minutes_to_end"] >= 0
    ]
    tail = [row for row in known_end if row["minutes_to_end"] <= 60]
    market_weights: Dict[str, float] = {}
    for row in rows:
        if not row["condition_id"]:
            continue
        market_weights[row["condition_id"]] = market_weights.get(
            row["condition_id"], 0.0
        ) + float(row["usdc_size"] if row["usdc_size"] is not None else 1.0)
    total_weight = sum(market_weights.values())
    concentration = (
        sum((value / total_weight) ** 2 for value in market_weights.values())
        if total_weight > 0
        else None
    )
    duration_hours = max((end_timestamp - start_timestamp) / 3600.0, 1.0 / 60.0)
    high_price_buys = [
        row for row in buys
        if row["price"] is not None and float(row["price"]) >= 0.9
    ]
    return {
        "trade_count": len(rows),
        "transaction_count": len(hashes),
        "trades_per_hour": round(len(rows) / duration_hours, 4),
        "records_per_transaction": round(len(rows) / len(hashes), 4)
        if hashes
        else None,
        "market_count": len(markets),
        "market_concentration": round(concentration, 6)
        if concentration is not None
        else None,
        "average_trade_usdc": round(statistics.mean(sizes), 4) if sizes else None,
        "median_trade_usdc": round(float(statistics.median(sizes)), 4)
        if sizes
        else None,
        "p90_trade_usdc": round(_percentile(sizes, 0.9), 4) if sizes else None,
        "buy_share": round(len(buys) / len(rows), 4) if rows else None,
        "average_price": round(statistics.mean(prices), 6) if prices else None,
        "high_price_buy_share": round(len(high_price_buys) / len(buys), 4)
        if buys
        else None,
        "settlement_coverage": round(len(known_end) / len(rows), 4) if rows else 0.0,
        "tail_60m_share": round(len(tail) / len(known_end), 4)
        if known_end
        else None,
        "activity_truncated": bool(truncated),
        "latest_trade_at": utc_iso(rows[0]["timestamp"]) if rows else None,
        "sample_transaction_hashes": hashes[:3],
    }


def compare_enriched_cohorts(
    cohorts: Dict[str, List[str]],
    fingerprints: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    definitions = (
        ("volume_loser_vs_winner", "高成交量亏损者 − 匹配赢家", "volume_losers", "volume_winners"),
        ("dropped_vs_retained", "掉榜者 − 留榜对照", "dropped", "retained_control"),
    )
    comparisons = []
    for comparison_id, label, first_key, second_key in definitions:
        metrics = []
        for field, field_label in FINGERPRINT_FIELDS:
            first_values = _metric_values(cohorts.get(first_key, []), fingerprints, field)
            second_values = _metric_values(cohorts.get(second_key, []), fingerprints, field)
            first_median = _median_or_none(first_values)
            second_median = _median_or_none(second_values)
            metrics.append(
                {
                    "field": field,
                    "label": field_label,
                    "first_median": first_median,
                    "second_median": second_median,
                    "difference": round(first_median - second_median, 6)
                    if first_median is not None and second_median is not None
                    else None,
                    "first_count": len(first_values),
                    "second_count": len(second_values),
                }
            )
        comparisons.append(
            {
                "id": comparison_id,
                "label": label,
                "first_cohort": first_key,
                "second_cohort": second_key,
                "first_addresses": list(cohorts.get(first_key, [])),
                "second_addresses": list(cohorts.get(second_key, [])),
                "metrics": metrics,
            }
        )
    return comparisons


def build_hypothesis_ledger(
    analyses: Sequence[Dict[str, Any]],
    minimum_days: int,
    minimum_unique: int,
    minimum_consistency: float,
) -> List[Dict[str, Any]]:
    hypotheses = (
        (
            "frequency_tax",
            "高频不一定带来更高收益",
            "volume_loser_vs_winner",
            "trades_per_hour",
            1,
            "在成交量相近的地址中，亏损组成交更频繁。",
        ),
        (
            "high_price_risk",
            "高胜率外观可能掩盖尾部损失",
            "volume_loser_vs_winner",
            "high_price_buy_share",
            1,
            "亏损组更常以 0.90 以上价格买入，单次错误的损失不对称。",
        ),
        (
            "concentration_risk",
            "少数市场的集中押注可能主导 PNL",
            "volume_loser_vs_winner",
            "market_concentration",
            1,
            "亏损组的名义成交规模更集中于少数市场。",
        ),
        (
            "dropout_inactivity",
            "掉榜可能来自活动衰减而非新亏损",
            "dropped_vs_retained",
            "trades_per_hour",
            -1,
            "掉榜组在截至掉榜快照的 24 小时内，成交频率低于成交量匹配的留榜组。",
        ),
    )
    result = []
    for hypothesis_id, title, comparison_id, field, expected_sign, statement in hypotheses:
        deltas = []
        first_addresses = set()
        second_addresses = set()
        for analysis in analyses:
            if analysis.get("eligible_for_insight") is False:
                continue
            comparison = next(
                (
                    item for item in analysis.get("comparisons") or []
                    if item.get("id") == comparison_id
                ),
                None,
            )
            if not comparison:
                continue
            metric = next(
                (item for item in comparison.get("metrics") or [] if item.get("field") == field),
                None,
            )
            if metric and metric.get("difference") is not None:
                deltas.append(float(metric["difference"]))
                first_addresses.update(comparison.get("first_addresses") or [])
                second_addresses.update(comparison.get("second_addresses") or [])
        nonzero = [value for value in deltas if value != 0]
        positive = sum(1 for value in nonzero if value > 0)
        negative = sum(1 for value in nonzero if value < 0)
        consistency = max(positive, negative) / len(nonzero) if nonzero else 0.0
        median_delta = _median_or_none(deltas)
        ci_low, ci_high = _bootstrap_median_ci(deltas, hypothesis_id)
        enough = (
            len(deltas) >= minimum_days
            and len(first_addresses) >= minimum_unique
            and len(second_addresses) >= minimum_unique
        )
        if not deltas:
            status = "collecting"
        elif not enough:
            status = "finding"
        elif (
            consistency >= minimum_consistency
            and ci_low is not None
            and ci_high is not None
            and (ci_low > 0 or ci_high < 0)
        ):
            status = "insight" if median_delta * expected_sign > 0 else "counter_signal"
        else:
            status = "inconclusive"
        result.append(
            {
                "id": hypothesis_id,
                "title": title,
                "statement": statement,
                "status": status,
                "days": len(deltas),
                "minimum_days": minimum_days,
                "first_unique_addresses": len(first_addresses),
                "second_unique_addresses": len(second_addresses),
                "minimum_unique_addresses": minimum_unique,
                "direction_consistency": round(consistency, 4),
                "median_difference": round(median_delta, 6)
                if median_delta is not None
                else None,
                "bootstrap_95_low": ci_low,
                "bootstrap_95_high": ci_high,
            }
        )
    result.append(
        {
            "id": "hidden_execution_variables",
            "title": "可见参数相同，不代表执行方式相同",
            "statement": "挂单创建时间、撤单、真实撮合延迟与完整库存路径在第一版不可观测；只列为隐藏变量，不用结果反推。",
            "status": "not_testable_v1",
            "days": 0,
        }
    )
    return result


def _board_map(snapshot: Dict[str, Any], board: str) -> Dict[str, Dict[str, Any]]:
    return {
        row["address"]: row
        for row in (snapshot.get("boards") or {}).get(board, [])
        if row.get("address")
    }


def _volume_match_addresses(
    first_addresses: Sequence[str],
    second_addresses: Sequence[str],
    first_rows: Dict[str, Dict[str, Any]],
    second_rows: Dict[str, Dict[str, Any]],
    limit: int,
) -> Tuple[List[str], List[str]]:
    first = [value for value in first_addresses if value in first_rows]
    first.sort(
        key=lambda value: float(first_rows[value].get("volume") or 0), reverse=True
    )
    available = {value for value in second_addresses if value in second_rows}
    matched_first = []
    matched_second = []
    for address in first:
        if not available or len(matched_first) >= limit:
            break
        target = math.log1p(max(0.0, float(first_rows[address].get("volume") or 0)))
        match = min(
            available,
            key=lambda value: abs(
                math.log1p(max(0.0, float(second_rows[value].get("volume") or 0)))
                - target
            ),
        )
        available.remove(match)
        matched_first.append(address)
        matched_second.append(match)
    return matched_first, matched_second


def _metric_values(
    addresses: Iterable[str],
    fingerprints: Dict[str, Dict[str, Any]],
    field: str,
) -> List[float]:
    values = []
    for address in addresses:
        value = (fingerprints.get(address) or {}).get(field)
        if value is not None:
            values.append(float(value))
    return values


def _median_or_none(values: Sequence[float]) -> Optional[float]:
    return float(statistics.median(values)) if values else None


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile requires values")
    index = min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered)) - 1)))
    return ordered[index]


def _bootstrap_median_ci(
    values: Sequence[float], seed_label: str
) -> Tuple[Optional[float], Optional[float]]:
    if len(values) < 2:
        return None, None
    seed = int(hashlib.sha256(seed_label.encode("utf-8")).hexdigest()[:16], 16)
    generator = random.Random(seed)
    medians = []
    source = list(values)
    for _index in range(1000):
        sample = [source[generator.randrange(len(source))] for _ in source]
        medians.append(float(statistics.median(sample)))
    medians.sort()
    return round(_percentile(medians, 0.025), 6), round(_percentile(medians, 0.975), 6)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
