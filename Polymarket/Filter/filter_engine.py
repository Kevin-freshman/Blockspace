"""Pure filtering and normalization logic for the Polymarket Filter."""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


def utc_iso(timestamp: Optional[float]) -> Optional[str]:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()


def parse_timestamp(value: Any) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def parse_iso_timestamp(value: Any) -> Optional[int]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def trade_key(trade: Dict[str, Any]) -> str:
    """Return a stable key without assuming one transaction equals one fill."""
    parts = (
        trade.get("transactionHash") or trade.get("transaction_hash") or "",
        trade.get("conditionId") or trade.get("condition_id") or "",
        trade.get("asset") or "",
        trade.get("side") or "",
        str(trade.get("size") or ""),
        str(trade.get("timestamp") or ""),
    )
    return "|".join(str(part).lower() for part in parts)


def normalize_trade(
    trade: Dict[str, Any],
    market: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    timestamp = parse_timestamp(trade.get("timestamp"))
    condition_id = str(
        trade.get("conditionId") or trade.get("condition_id") or ""
    ).lower()
    end_timestamp = None
    if market:
        end_timestamp = parse_timestamp(market.get("end_timestamp"))
        if end_timestamp is None:
            end_timestamp = parse_iso_timestamp(
                market.get("end_date_iso") or market.get("endDate")
            )

    minutes_to_settlement = None
    if timestamp is not None and end_timestamp is not None:
        minutes_to_settlement = round((end_timestamp - timestamp) / 60.0, 2)

    result = {
        "key": trade_key(trade),
        "address": str(trade.get("proxyWallet") or "").lower(),
        "timestamp": timestamp,
        "timestamp_utc": utc_iso(timestamp),
        "transaction_hash": trade.get("transactionHash") or "",
        "condition_id": condition_id,
        "side": trade.get("side") or "",
        "size": _number(trade.get("size")),
        "usdc_size": _number(trade.get("usdcSize")),
        "price": _number(trade.get("price")),
        "title": trade.get("title") or (market or {}).get("question") or "",
        "slug": trade.get("slug") or (market or {}).get("market_slug") or "",
        "event_slug": trade.get("eventSlug") or "",
        "outcome": trade.get("outcome") or "",
        "minutes_to_settlement": minutes_to_settlement,
        "settlement_time_utc": utc_iso(end_timestamp),
    }
    return result


def build_address_metrics(
    leaderboard_entry: Dict[str, Any],
    trades: Iterable[Dict[str, Any]],
    market_cache: Dict[str, Dict[str, Any]],
    lookback_hours: float,
    now_timestamp: int,
    truncated: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cutoff = now_timestamp - int(lookback_hours * 3600)
    normalized = []
    for raw in trades:
        timestamp = parse_timestamp(raw.get("timestamp"))
        if timestamp is None or timestamp < cutoff or timestamp > now_timestamp + 300:
            continue
        condition_id = str(raw.get("conditionId") or "").lower()
        normalized.append(normalize_trade(raw, market_cache.get(condition_id)))
    normalized.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)

    transaction_hashes = {
        item["transaction_hash"].lower()
        for item in normalized
        if item.get("transaction_hash")
    }
    positive_distances = [
        item["minutes_to_settlement"]
        for item in normalized
        if item.get("minutes_to_settlement") is not None
        and item["minutes_to_settlement"] >= 0
    ]
    after_scheduled_end = sum(
        1
        for item in normalized
        if item.get("minutes_to_settlement") is not None
        and item["minutes_to_settlement"] < 0
    )
    trade_count = len(normalized)
    lookback_days = max(float(lookback_hours) / 24.0, 1.0 / 24.0)
    settlement_count = len(positive_distances)
    median_minutes = (
        round(float(statistics.median(positive_distances)), 2)
        if positive_distances
        else None
    )
    address = str(leaderboard_entry.get("proxyWallet") or "").lower()
    metrics = {
        "rank": _integer(leaderboard_entry.get("rank")),
        "address": address,
        "user_name": leaderboard_entry.get("userName") or "",
        "verified": bool(leaderboard_entry.get("verifiedBadge")),
        "leaderboard_pnl": _number(leaderboard_entry.get("pnl")),
        "leaderboard_volume": _number(leaderboard_entry.get("vol")),
        "trade_count": trade_count,
        "transaction_count": len(transaction_hashes),
        "trades_per_day": round(trade_count / lookback_days, 2),
        "median_minutes_to_settlement": median_minutes,
        "median_hours_to_settlement": (
            round(median_minutes / 60.0, 2) if median_minutes is not None else None
        ),
        "settlement_trade_count": settlement_count,
        "settlement_coverage": (
            round(settlement_count / trade_count, 4) if trade_count else 0.0
        ),
        "after_scheduled_end_count": after_scheduled_end,
        "latest_trade_at": normalized[0]["timestamp_utc"] if normalized else None,
        "activity_truncated": bool(truncated),
        "open_position_count": None,
        "open_position_value": None,
        "open_position_cash_pnl": None,
        "passes": False,
        "filter_reasons": [],
    }
    return metrics, normalized


def apply_filter(metrics: Dict[str, Any], filters: Dict[str, Any]) -> Dict[str, Any]:
    reasons = []
    if metrics["trade_count"] < filters["min_trade_count"]:
        reasons.append("trade_count")
    if metrics["trades_per_day"] < filters["min_trades_per_day"]:
        reasons.append("trades_per_day")

    distance = metrics.get("median_hours_to_settlement")
    minimum = filters.get("min_median_hours_to_settlement")
    maximum = filters.get("max_median_hours_to_settlement")
    if minimum is not None or maximum is not None:
        if distance is None:
            reasons.append("settlement_distance_unknown")
        else:
            if minimum is not None and distance < minimum:
                reasons.append("settlement_distance_min")
            if maximum is not None and distance > maximum:
                reasons.append("settlement_distance_max")

    result = dict(metrics)
    result["filter_reasons"] = reasons
    result["passes"] = not reasons
    return result


def summarize_positions(positions: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(positions)
    return {
        "open_position_count": len(rows),
        "open_position_value": round(
            sum(_number(row.get("currentValue")) for row in rows), 2
        ),
        "open_position_cash_pnl": round(
            sum(_number(row.get("cashPnl")) for row in rows), 2
        ),
    }


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
