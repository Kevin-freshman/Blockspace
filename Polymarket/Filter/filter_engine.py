"""Pure filtering and normalization logic for the Polymarket Filter."""

from __future__ import annotations

import re
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
    end_timestamp = infer_interval_end_timestamp(trade)
    settlement_time_source = "slug_interval" if end_timestamp is not None else None
    if end_timestamp is None and market:
        end_timestamp = parse_timestamp(market.get("end_timestamp"))
        if end_timestamp is None:
            end_timestamp = parse_iso_timestamp(
                market.get("end_date_iso") or market.get("endDate")
            )
        if end_timestamp is not None:
            settlement_time_source = "market_end_date"

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
        "settlement_time_source": settlement_time_source,
    }
    return result


def infer_interval_end_timestamp(trade: Dict[str, Any]) -> Optional[int]:
    """Infer precise end time for slugs like btc-updown-5m-1700000000."""
    slug = str(trade.get("eventSlug") or trade.get("slug") or "").lower()
    match = re.search(r"-(\d+)(m|h)-(\d{10})$", slug)
    if not match:
        return None
    amount = int(match.group(1))
    seconds = amount * (60 if match.group(2) == "m" else 3600)
    if seconds <= 0 or seconds > 7 * 24 * 3600:
        return None
    start_timestamp = parse_timestamp(match.group(3))
    return start_timestamp + seconds if start_timestamp is not None else None


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
    tail_60m = [
        item for item in normalized
        if item.get("minutes_to_settlement") is not None
        and 0 <= item["minutes_to_settlement"] <= 60
    ]
    tail_6h_count = sum(
        1 for item in normalized
        if item.get("minutes_to_settlement") is not None
        and 0 <= item["minutes_to_settlement"] <= 360
    )
    tail_24h_count = sum(
        1 for item in normalized
        if item.get("minutes_to_settlement") is not None
        and 0 <= item["minutes_to_settlement"] <= 1440
    )
    after_scheduled_end = sum(
        1
        for item in normalized
        if item.get("minutes_to_settlement") is not None
        and item["minutes_to_settlement"] < 0
    )
    trade_count = len(normalized)
    lookback_days = max(float(lookback_hours) / 24.0, 1.0 / 24.0)
    settlement_count = len(positive_distances)
    slug_time_count = sum(
        1 for item in normalized
        if item.get("minutes_to_settlement") is not None
        and item["minutes_to_settlement"] >= 0
        and item.get("settlement_time_source") == "slug_interval"
    )
    market_time_count = sum(
        1 for item in normalized
        if item.get("minutes_to_settlement") is not None
        and item["minutes_to_settlement"] >= 0
        and item.get("settlement_time_source") == "market_end_date"
    )
    median_minutes = (
        round(float(statistics.median(positive_distances)), 2)
        if positive_distances
        else None
    )
    address = str(leaderboard_entry.get("proxyWallet") or "").lower()
    leaderboard_pnl = _number(leaderboard_entry.get("pnl"))
    leaderboard_volume = _number(leaderboard_entry.get("vol"))
    return_efficiency = (
        round(leaderboard_pnl / leaderboard_volume, 6)
        if leaderboard_volume > 0
        else None
    )
    # PNL and activity use the same selected period.  This is an average per
    # API activity row, not a matched position-level profit.  A capped activity
    # response cannot provide a defensible denominator, so leave it unknown.
    estimated_pnl_per_activity = (
        round(leaderboard_pnl / trade_count, 4)
        if trade_count > 0 and not truncated
        else None
    )
    metrics = {
        "rank": _integer(leaderboard_entry.get("rank")),
        "address": address,
        "user_name": leaderboard_entry.get("userName") or "",
        "verified": bool(leaderboard_entry.get("verifiedBadge")),
        "leaderboard_pnl": leaderboard_pnl,
        "leaderboard_volume": leaderboard_volume,
        # This is a leaderboard-period efficiency ratio, not capital ROI.
        "return_efficiency": return_efficiency,
        "estimated_pnl_per_activity": estimated_pnl_per_activity,
        "trade_count": trade_count,
        "transaction_count": len(transaction_hashes),
        "trades_per_day": round(trade_count / lookback_days, 2),
        "median_minutes_to_settlement": median_minutes,
        "median_hours_to_settlement": (
            round(median_minutes / 60.0, 2) if median_minutes is not None else None
        ),
        "settlement_trade_count": settlement_count,
        "settlement_slug_inferred_count": slug_time_count,
        "settlement_market_end_count": market_time_count,
        "settlement_coverage": (
            round(settlement_count / trade_count, 4) if trade_count else 0.0
        ),
        "tail_60m_trade_count": len(tail_60m),
        "tail_6h_trade_count": tail_6h_count,
        "tail_24h_trade_count": tail_24h_count,
        "tail_60m_share": (
            round(len(tail_60m) / settlement_count, 4)
            if settlement_count else None
        ),
        "tail_60m_buy_count": sum(
            1 for item in tail_60m if str(item.get("side") or "").upper() == "BUY"
        ),
        "tail_60m_sell_count": sum(
            1 for item in tail_60m if str(item.get("side") or "").upper() == "SELL"
        ),
        "tail_60m_high_confidence_count": sum(
            1 for item in tail_60m
            if str(item.get("side") or "").upper() == "BUY"
            and item.get("price") is not None
            and float(item["price"]) >= 0.9
        ),
        "tail_60m_avg_price": (
            round(statistics.mean(
                float(item["price"]) for item in tail_60m
                if item.get("price") is not None
            ), 4)
            if any(item.get("price") is not None for item in tail_60m)
            else None
        ),
        "tail_60m_usdc_volume": round(sum(
            float(item.get("usdc_size") or 0) for item in tail_60m
        ), 2),
        "tail_60m_market_count": len({
            item.get("condition_id") for item in tail_60m
            if item.get("condition_id")
        }),
        "tail_analysis_in_scope": False,
        "after_scheduled_end_count": after_scheduled_end,
        "latest_trade_at": normalized[0]["timestamp_utc"] if normalized else None,
        "activity_truncated": bool(truncated),
        "open_position_count": None,
        "open_position_value": None,
        "open_position_cash_pnl": None,
        "chain_status": "not_checked",
        "chain_sample_size": 0,
        "chain_receipt_count": 0,
        "chain_confirmed_count": 0,
        "chain_verification_rate": 0.0,
        "polymarket_contract_hit_count": 0,
        "polymarket_contract_rate": 0.0,
        "chain_latest_block": None,
        "chain_log_count": 0,
        "passes": False,
        "filter_reasons": [],
    }
    return metrics, normalized


def summarize_tail_analysis(
    addresses: Iterable[Dict[str, Any]],
    sample_limit: int,
    market_cap_per_address: int,
) -> Dict[str, Any]:
    """Summarize bounded, scheduled-end tail activity for the browser lesson."""
    scoped = [item for item in addresses if item.get("tail_analysis_in_scope")]
    known_trades = sum(int(item.get("settlement_trade_count") or 0) for item in scoped)
    total_trades = sum(int(item.get("trade_count") or 0) for item in scoped)
    tail_trades = sum(int(item.get("tail_60m_trade_count") or 0) for item in scoped)
    high_confidence = sum(
        int(item.get("tail_60m_high_confidence_count") or 0) for item in scoped
    )
    return {
        "window_minutes": 60,
        "sample_limit": int(sample_limit),
        "sampled_addresses": len(scoped),
        "capped_addresses": sum(
            1 for item in scoped if item.get("activity_truncated")
        ),
        "market_cap_per_address": int(market_cap_per_address),
        "known_trade_count": known_trades,
        "slug_inferred_trade_count": sum(
            int(item.get("settlement_slug_inferred_count") or 0) for item in scoped
        ),
        "market_end_trade_count": sum(
            int(item.get("settlement_market_end_count") or 0) for item in scoped
        ),
        "total_trade_count": total_trades,
        "settlement_coverage": (
            round(known_trades / total_trades, 4) if total_trades else 0.0
        ),
        "addresses_with_tail_trades": sum(
            1 for item in scoped if int(item.get("tail_60m_trade_count") or 0) > 0
        ),
        "tail_trade_count": tail_trades,
        "tail_trade_share": (
            round(tail_trades / known_trades, 4) if known_trades else None
        ),
        "high_confidence_tail_count": high_confidence,
        "tail_usdc_volume": round(sum(
            float(item.get("tail_60m_usdc_volume") or 0) for item in scoped
        ), 2),
    }


def apply_filter(
    metrics: Dict[str, Any],
    filters: Dict[str, Any],
    check_settlement: bool = True,
) -> Dict[str, Any]:
    reasons = []
    if metrics["trade_count"] < filters["min_trade_count"]:
        reasons.append("trade_count")
    if metrics["trades_per_day"] < filters["min_trades_per_day"]:
        reasons.append("trades_per_day")
    minimum_efficiency = filters.get("min_return_efficiency")
    if minimum_efficiency is not None:
        efficiency = metrics.get("return_efficiency")
        if efficiency is None or efficiency < minimum_efficiency:
            reasons.append("return_efficiency")
    minimum_pnl_per_activity = filters.get("min_estimated_pnl_per_activity")
    if minimum_pnl_per_activity is not None:
        pnl_per_activity = metrics.get("estimated_pnl_per_activity")
        if pnl_per_activity is None or pnl_per_activity < minimum_pnl_per_activity:
            reasons.append("estimated_pnl_per_activity")

    if check_settlement:
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


def summarize_chain_receipts(
    transaction_hashes: Iterable[str],
    receipt_cache: Dict[str, Dict[str, Any]],
    polymarket_contracts: Iterable[str],
) -> Dict[str, Any]:
    hashes = list(
        dict.fromkeys(
            str(value).lower()
            for value in transaction_hashes
            if str(value).startswith("0x") and len(str(value)) == 66
        )
    )
    contracts = {str(value).lower() for value in polymarket_contracts}
    receipt_count = confirmed_count = contract_hits = log_count = 0
    blocks = []
    for transaction_hash in hashes:
        receipt = receipt_cache.get(transaction_hash)
        if not receipt or receipt.get("missing"):
            continue
        receipt_count += 1
        status = receipt.get("status")
        if status == 1:
            confirmed_count += 1
        block_number = receipt.get("block_number")
        if isinstance(block_number, int):
            blocks.append(block_number)
        log_count += int(receipt.get("log_count") or 0)
        if receipt_has_polymarket_contract(receipt, contracts):
            contract_hits += 1

    sample_size = len(hashes)
    verification_rate = confirmed_count / sample_size if sample_size else 0.0
    contract_rate = contract_hits / sample_size if sample_size else 0.0
    if not sample_size:
        status = "no_sample"
    elif confirmed_count == sample_size:
        status = "verified"
    elif confirmed_count:
        status = "partial"
    else:
        status = "unverified"
    return {
        "chain_status": status,
        "chain_sample_size": sample_size,
        "chain_receipt_count": receipt_count,
        "chain_confirmed_count": confirmed_count,
        "chain_verification_rate": round(verification_rate, 4),
        "polymarket_contract_hit_count": contract_hits,
        "polymarket_contract_rate": round(contract_rate, 4),
        "chain_latest_block": max(blocks) if blocks else None,
        "chain_log_count": log_count,
    }


def apply_chain_filter(
    metrics: Dict[str, Any], chain_filters: Dict[str, Any]
) -> Dict[str, Any]:
    result = dict(metrics)
    reasons = list(result.get("filter_reasons") or [])
    if not chain_filters.get("enabled", True):
        result["filter_reasons"] = reasons
        result["passes"] = not reasons
        return result
    if result.get("chain_confirmed_count", 0) < chain_filters["min_confirmed_transactions"]:
        reasons.append("chain_confirmed_transactions")
    if result.get("chain_verification_rate", 0) < chain_filters["min_verification_rate"]:
        reasons.append("chain_verification_rate")
    if result.get("polymarket_contract_rate", 0) < chain_filters["min_polymarket_contract_rate"]:
        reasons.append("polymarket_contract_rate")
    result["filter_reasons"] = reasons
    result["passes"] = not reasons
    return result


def enrich_trade_onchain(
    trade: Dict[str, Any],
    receipt: Optional[Dict[str, Any]],
    polymarket_contracts: Iterable[str],
) -> Dict[str, Any]:
    result = dict(trade)
    contracts = {str(value).lower() for value in polymarket_contracts}
    if not receipt:
        result.update(
            {
                "onchain_status": "not_checked",
                "onchain_block_number": None,
                "onchain_polymarket_contract": False,
            }
        )
    elif receipt.get("missing"):
        result.update(
            {
                "onchain_status": "missing",
                "onchain_block_number": None,
                "onchain_polymarket_contract": False,
            }
        )
    else:
        result.update(
            {
                "onchain_status": (
                    "confirmed" if receipt.get("status") == 1 else "reverted"
                ),
                "onchain_block_number": receipt.get("block_number"),
                "onchain_polymarket_contract": receipt_has_polymarket_contract(
                    receipt, contracts
                ),
            }
        )
    return result


def receipt_has_polymarket_contract(
    receipt: Dict[str, Any], contracts: Iterable[str]
) -> bool:
    contract_set = {str(value).lower() for value in contracts}
    if str(receipt.get("to") or "").lower() in contract_set:
        return True
    return any(
        str(address).lower() in contract_set
        for address in receipt.get("log_addresses") or []
    )


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
