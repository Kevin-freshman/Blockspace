"""Pure, offline logic for resolved-market strategy backtests.

The live collector deliberately lives in ``scripts/run_six_month_backtest.py``.
Keeping simulation here makes the assumptions testable without network access.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PRICE_BUCKETS = (
    (0.50, 0.60, "0.50-0.60"),
    (0.60, 0.70, "0.60-0.70"),
    (0.70, 0.80, "0.70-0.80"),
    (0.80, 0.90, "0.80-0.90"),
    (0.90, 0.95, "0.90-0.95"),
    (0.95, 0.98, "0.95-0.98"),
    (0.98, 1.000001, "0.98-1.00"),
)


def normalize_hourly_market(
    event: Dict[str, Any], asset: str
) -> Optional[Dict[str, Any]]:
    """Return the one resolved binary CLOB market attached to an hourly event."""
    markets = event.get("markets") or []
    if len(markets) != 1 or not isinstance(markets[0], dict):
        return None
    market = markets[0]
    outcomes = _json_list(market.get("outcomes"))
    final_prices = _json_list(market.get("outcomePrices"))
    token_ids = _json_list(market.get("clobTokenIds"))
    if len(outcomes) != 2 or len(final_prices) != 2 or len(token_ids) != 2:
        return None
    try:
        resolved = [float(value) for value in final_prices]
    except (TypeError, ValueError):
        return None
    winner_indexes = [index for index, value in enumerate(resolved) if value >= 0.999]
    loser_indexes = [index for index, value in enumerate(resolved) if value <= 0.001]
    if len(winner_indexes) != 1 or len(loser_indexes) != 1:
        return None
    end_at = market.get("endDate") or event.get("endDate")
    end_timestamp = parse_iso_timestamp(end_at)
    if end_timestamp is None:
        return None
    token_id = str(token_ids[0])
    if not token_id.isdigit():
        return None
    return {
        "asset": str(asset).upper(),
        "event_id": str(event.get("id") or ""),
        "market_id": str(market.get("id") or ""),
        "condition_id": str(market.get("conditionId") or "").lower(),
        "slug": str(market.get("slug") or event.get("slug") or ""),
        "end_at": utc_iso(end_timestamp),
        "end_timestamp": end_timestamp,
        "token_id": token_id,
        "outcomes": [str(value) for value in outcomes],
        "winner_index": winner_indexes[0],
        "fees_enabled": bool(market.get("feesEnabled")),
        "volume": _optional_float(market.get("volumeNum", market.get("volume"))),
    }


def price_at_or_before(
    history: Sequence[Dict[str, Any]],
    target_timestamp: int,
    max_staleness_seconds: int = 120,
) -> Optional[Tuple[float, int]]:
    """Select a historical price without looking past the decision timestamp."""
    points = []
    for raw in history:
        try:
            timestamp = int(raw.get("t"))
            price = float(raw.get("p"))
        except (AttributeError, TypeError, ValueError):
            continue
        if 0 < price < 1:
            points.append((timestamp, price))
    points.sort()
    if not points:
        return None
    timestamps = [item[0] for item in points]
    index = bisect.bisect_right(timestamps, int(target_timestamp)) - 1
    if index < 0:
        return None
    timestamp, price = points[index]
    age = int(target_timestamp) - timestamp
    if age < 0 or age > int(max_staleness_seconds):
        return None
    return price, age


def build_observations(
    markets: Iterable[Dict[str, Any]],
    histories: Dict[str, Sequence[Dict[str, Any]]],
    windows_minutes: Sequence[int],
    max_staleness_seconds: int = 120,
) -> List[Dict[str, Any]]:
    observations = []
    for market in markets:
        end_timestamp = int(market["end_timestamp"])
        history = histories.get(str(market["token_id"])) or []
        for window in windows_minutes:
            target = end_timestamp - int(window) * 60
            selected = price_at_or_before(history, target, max_staleness_seconds)
            if selected is None:
                continue
            token_zero_price, age = selected
            favorite_index = 0 if token_zero_price >= 0.5 else 1
            favorite_price = (
                token_zero_price if favorite_index == 0 else 1.0 - token_zero_price
            )
            observations.append(
                {
                    "asset": market["asset"],
                    "market_id": market["market_id"],
                    "condition_id": market["condition_id"],
                    "slug": market["slug"],
                    "end_at": market["end_at"],
                    "end_timestamp": end_timestamp,
                    "date": market["end_at"][:10],
                    "window_minutes": int(window),
                    "reference_token_zero_price": round(token_zero_price, 6),
                    "favorite_price": round(favorite_price, 6),
                    "favorite_index": favorite_index,
                    "winner_index": int(market["winner_index"]),
                    "won": favorite_index == int(market["winner_index"]),
                    "price_age_seconds": age,
                }
            )
    observations.sort(
        key=lambda item: (
            item["end_timestamp"], item["asset"], item["window_minutes"]
        )
    )
    return observations


def simulate_trade(
    observation: Dict[str, Any],
    threshold: float,
    fee_rate: float,
    slippage: float,
) -> Optional[Dict[str, Any]]:
    """Simulate a $1 taker buy of the reference-price favorite.

    The public history is not an executable ask.  ``slippage`` therefore moves
    the reference price against the buyer before sizing shares.  The fee model
    follows the currently documented crypto formula per dollar of trade value:
    fee / notional = fee_rate * (1 - entry_price).
    """
    favorite_price = float(observation["favorite_price"])
    if favorite_price + 1e-12 < float(threshold):
        return None
    entry_price = min(0.9999, max(0.0001, favorite_price + float(slippage)))
    fee = max(0.0, float(fee_rate)) * (1.0 - entry_price)
    payout = (1.0 / entry_price) if observation["won"] else 0.0
    pnl = payout - 1.0 - fee
    cash_outlay = 1.0 + fee
    trade = dict(observation)
    trade.update(
        {
            "threshold": float(threshold),
            "reference_price": favorite_price,
            "entry_price": entry_price,
            "fee": fee,
            "cash_outlay": cash_outlay,
            "payout": payout,
            "pnl": pnl,
            "cash_return": pnl / cash_outlay,
        }
    )
    return trade


def summarize_strategy(
    observations: Iterable[Dict[str, Any]],
    window_minutes: int,
    threshold: float,
    fee_rate: float,
    slippage: float,
    bootstrap_samples: int = 1000,
) -> Dict[str, Any]:
    scoped = [
        item for item in observations
        if int(item["window_minutes"]) == int(window_minutes)
    ]
    trades = [
        trade for trade in (
            simulate_trade(item, threshold, fee_rate, slippage) for item in scoped
        ) if trade is not None
    ]
    return summarize_trades(
        trades,
        bootstrap_samples=bootstrap_samples,
        seed_label="%s|%s|%s|%s" % (
            window_minutes, threshold, fee_rate, slippage
        ),
    )


def summarize_trades(
    trades: Sequence[Dict[str, Any]],
    bootstrap_samples: int = 1000,
    seed_label: str = "strategy",
) -> Dict[str, Any]:
    ordered = sorted(trades, key=lambda item: (item["end_timestamp"], item["asset"]))
    trade_count = len(ordered)
    wins = sum(1 for item in ordered if item["won"])
    losses = trade_count - wins
    total_pnl = sum(float(item["pnl"]) for item in ordered)
    total_cash = sum(float(item["cash_outlay"]) for item in ordered)
    positive_pnl = sum(max(0.0, float(item["pnl"])) for item in ordered)
    negative_pnl = -sum(min(0.0, float(item["pnl"])) for item in ordered)
    winning_pnls = [float(item["pnl"]) for item in ordered if item["pnl"] > 0]
    losing_pnls = [-float(item["pnl"]) for item in ordered if item["pnl"] < 0]
    ci_low, ci_high = _daily_block_bootstrap_ci(
        ordered, bootstrap_samples, seed_label
    )
    return {
        "trade_count": trade_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / trade_count, 6) if trade_count else None,
        "average_reference_price": _mean_or_none(
            [float(item["reference_price"]) for item in ordered]
        ),
        "average_entry_price": _mean_or_none(
            [float(item["entry_price"]) for item in ordered]
        ),
        "average_price_age_seconds": _mean_or_none(
            [float(item["price_age_seconds"]) for item in ordered]
        ),
        "total_pnl_per_one_usd_stake": round(total_pnl, 6),
        "return_on_cash": round(total_pnl / total_cash, 6) if total_cash else None,
        "daily_block_bootstrap_95_low": ci_low,
        "daily_block_bootstrap_95_high": ci_high,
        "profit_factor": round(positive_pnl / negative_pnl, 6)
        if negative_pnl else None,
        "average_win": round(statistics.mean(winning_pnls), 6)
        if winning_pnls else None,
        "average_loss": round(statistics.mean(losing_pnls), 6)
        if losing_pnls else None,
        "wins_needed_per_average_loss": round(
            statistics.mean(losing_pnls) / statistics.mean(winning_pnls), 3
        ) if losing_pnls and winning_pnls else None,
        "max_drawdown": round(_max_drawdown(ordered), 6),
        "worst_trade": round(min((float(item["pnl"]) for item in ordered), default=0.0), 6),
        "assets": sorted({str(item["asset"]) for item in ordered}),
    }


def parameter_grid(
    observations: Sequence[Dict[str, Any]],
    windows_minutes: Sequence[int],
    thresholds: Sequence[float],
    fee_rate: float,
    slippage: float,
    bootstrap_samples: int = 0,
) -> List[Dict[str, Any]]:
    rows = []
    for window in windows_minutes:
        for threshold in thresholds:
            summary = summarize_strategy(
                observations,
                window,
                threshold,
                fee_rate,
                slippage,
                bootstrap_samples=bootstrap_samples,
            )
            row = {
                "window_minutes": int(window),
                "threshold": float(threshold),
            }
            row.update(summary)
            rows.append(row)
    return rows


def break_even_slippage(
    observations: Sequence[Dict[str, Any]],
    window_minutes: int,
    threshold: float,
    fee_rate: float,
    maximum_slippage: float = 0.05,
    iterations: int = 30,
) -> Optional[float]:
    """Return the largest additive slippage with non-negative aggregate return."""
    at_zero = summarize_strategy(
        observations, window_minutes, threshold, fee_rate, 0.0, bootstrap_samples=0
    )
    if at_zero["return_on_cash"] is None:
        return None
    if float(at_zero["return_on_cash"]) <= 0:
        return 0.0
    at_maximum = summarize_strategy(
        observations,
        window_minutes,
        threshold,
        fee_rate,
        maximum_slippage,
        bootstrap_samples=0,
    )
    if (
        at_maximum["return_on_cash"] is not None
        and float(at_maximum["return_on_cash"]) >= 0
    ):
        return None
    lower = 0.0
    upper = float(maximum_slippage)
    for _index in range(max(1, int(iterations))):
        middle = (lower + upper) / 2.0
        summary = summarize_strategy(
            observations,
            window_minutes,
            threshold,
            fee_rate,
            middle,
            bootstrap_samples=0,
        )
        if (
            summary["return_on_cash"] is not None
            and float(summary["return_on_cash"]) >= 0
        ):
            lower = middle
        else:
            upper = middle
    return round(lower, 6)


def choose_training_parameter(
    grid: Sequence[Dict[str, Any]], minimum_trades: int
) -> Dict[str, Any]:
    eligible = [
        item for item in grid
        if int(item.get("trade_count") or 0) >= int(minimum_trades)
        and item.get("return_on_cash") is not None
    ]
    if not eligible:
        raise ValueError("no parameter has the required training trades")
    return max(
        eligible,
        key=lambda item: (
            float(item["return_on_cash"]),
            int(item["trade_count"]),
            -int(item["window_minutes"]),
            -float(item["threshold"]),
        ),
    )


def calibration_table(
    observations: Sequence[Dict[str, Any]], window_minutes: int
) -> List[Dict[str, Any]]:
    scoped = [
        item for item in observations
        if int(item["window_minutes"]) == int(window_minutes)
    ]
    result = []
    for lower, upper, label in PRICE_BUCKETS:
        rows = [
            item for item in scoped
            if lower <= float(item["favorite_price"]) < upper
        ]
        wins = sum(1 for item in rows if item["won"])
        result.append(
            {
                "bucket": label,
                "count": len(rows),
                "average_price": _mean_or_none(
                    [float(item["favorite_price"]) for item in rows]
                ),
                "win_rate": round(wins / len(rows), 6) if rows else None,
                "calibration_gap": round(
                    wins / len(rows)
                    - statistics.mean(float(item["favorite_price"]) for item in rows),
                    6,
                ) if rows else None,
            }
        )
    return result


def portfolio_comparison(
    observations: Sequence[Dict[str, Any]],
    window_minutes: int,
    threshold: float,
    fee_rate: float,
    slippage: float,
    concentrated_asset: str = "BTC",
) -> Dict[str, Any]:
    """Compare $1/hour equal-weight deployment with a one-asset portfolio."""
    scoped = [
        item for item in observations
        if int(item["window_minutes"]) == int(window_minutes)
    ]
    hours: Dict[int, List[Dict[str, Any]]] = {}
    for item in scoped:
        hours.setdefault(int(item["end_timestamp"]), []).append(item)

    def path(
        asset: Optional[str], included_hours: Optional[Sequence[int]] = None
    ) -> Dict[str, Any]:
        returns = []
        active = 0
        deployed_cash = 0.0
        total_pnl = 0.0
        timestamps = sorted(hours) if included_hours is None else sorted(included_hours)
        for timestamp in timestamps:
            candidates = hours[timestamp]
            if asset is not None:
                candidates = [item for item in candidates if item["asset"] == asset]
            trades = [
                trade for trade in (
                    simulate_trade(item, threshold, fee_rate, slippage)
                    for item in candidates
                ) if trade is not None
            ]
            if not trades:
                returns.append(0.0)
                continue
            active += 1
            pnl = statistics.mean(float(item["pnl"]) for item in trades)
            cash = statistics.mean(float(item["cash_outlay"]) for item in trades)
            returns.append(pnl)
            deployed_cash += cash
            total_pnl += pnl
        return {
            "calendar_hours": len(timestamps),
            "active_hours": active,
            "total_pnl_per_one_usd_hour": round(total_pnl, 6),
            "return_on_deployed_cash": round(total_pnl / deployed_cash, 6)
            if deployed_cash else None,
            "max_drawdown": round(_max_drawdown_from_values(returns), 6),
            "worst_hour": round(min(returns), 6) if returns else None,
            "hourly_pnl_stddev": round(statistics.pstdev(returns), 6)
            if len(returns) > 1 else None,
        }

    anchor_hours = []
    for timestamp, candidates in hours.items():
        btc_trades = [
            trade for trade in (
                simulate_trade(item, threshold, fee_rate, slippage)
                for item in candidates if item["asset"] == concentrated_asset
            ) if trade is not None
        ]
        if btc_trades:
            anchor_hours.append(timestamp)

    trade_paths: Dict[str, Dict[int, float]] = {}
    for asset in sorted({str(item["asset"]) for item in scoped}):
        trade_paths[asset] = {}
    for item in scoped:
        trade = simulate_trade(item, threshold, fee_rate, slippage)
        if trade is not None:
            trade_paths[str(item["asset"])][int(item["end_timestamp"])] = float(
                trade["pnl"]
            )
    correlations = []
    assets = sorted(trade_paths)
    for first_index, first_asset in enumerate(assets):
        for second_asset in assets[first_index + 1:]:
            shared = sorted(
                set(trade_paths[first_asset]) & set(trade_paths[second_asset])
            )
            first_values = [trade_paths[first_asset][key] for key in shared]
            second_values = [trade_paths[second_asset][key] for key in shared]
            correlation = _correlation(first_values, second_values)
            correlations.append(
                {
                    "assets": [first_asset, second_asset],
                    "shared_signal_hours": len(shared),
                    "pnl_correlation": round(correlation, 6)
                    if correlation is not None else None,
                    "joint_loss_hours": sum(
                        1 for first, second in zip(first_values, second_values)
                        if first < 0 and second < 0
                    ),
                }
            )

    return {
        "diversified_equal_weight": path(None),
        "concentrated_%s" % concentrated_asset.lower(): path(concentrated_asset),
        "matched_diversified_equal_weight": path(None, anchor_hours),
        "matched_concentrated_%s" % concentrated_asset.lower(): path(
            concentrated_asset, anchor_hours
        ),
        "pairwise_signal_pnl_correlations": correlations,
    }


def monthly_table(
    observations: Sequence[Dict[str, Any]],
    window_minutes: int,
    threshold: float,
    fee_rate: float,
    slippage: float,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in observations:
        if int(item["window_minutes"]) != int(window_minutes):
            continue
        trade = simulate_trade(item, threshold, fee_rate, slippage)
        if trade is not None:
            grouped.setdefault(str(item["date"])[:7], []).append(trade)
    result = []
    for month in sorted(grouped):
        row = {"month": month}
        row.update(summarize_trades(grouped[month], bootstrap_samples=0))
        result.append(row)
    return result


def split_observations(
    observations: Sequence[Dict[str, Any]], split_at: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    split_timestamp = parse_iso_timestamp(split_at)
    if split_timestamp is None:
        raise ValueError("invalid split timestamp")
    training = [item for item in observations if item["end_timestamp"] < split_timestamp]
    holdout = [item for item in observations if item["end_timestamp"] >= split_timestamp]
    return training, holdout


def parse_iso_timestamp(value: Any) -> Optional[int]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def utc_iso(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()


def _daily_block_bootstrap_ci(
    trades: Sequence[Dict[str, Any]], samples: int, seed_label: str
) -> Tuple[Optional[float], Optional[float]]:
    if samples <= 0 or not trades:
        return None, None
    days: Dict[str, Tuple[float, float]] = {}
    for item in trades:
        day = str(item["date"])
        pnl, cash = days.get(day, (0.0, 0.0))
        days[day] = (pnl + float(item["pnl"]), cash + float(item["cash_outlay"]))
    blocks = list(days.values())
    if len(blocks) < 2:
        return None, None
    seed = int(hashlib.sha256(seed_label.encode("utf-8")).hexdigest()[:16], 16)
    generator = random.Random(seed)
    estimates = []
    for _index in range(int(samples)):
        chosen = [blocks[generator.randrange(len(blocks))] for _ in blocks]
        total_pnl = sum(item[0] for item in chosen)
        total_cash = sum(item[1] for item in chosen)
        estimates.append(total_pnl / total_cash if total_cash else 0.0)
    estimates.sort()
    return (
        round(_percentile(estimates, 0.025), 6),
        round(_percentile(estimates, 0.975), 6),
    )


def _max_drawdown(trades: Sequence[Dict[str, Any]]) -> float:
    return _max_drawdown_from_values([float(item["pnl"]) for item in trades])


def _max_drawdown_from_values(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile requires values")
    position = (len(ordered) - 1) * min(1.0, max(0.0, fraction))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _mean_or_none(values: Sequence[float]) -> Optional[float]:
    return round(statistics.mean(values), 6) if values else None


def _correlation(
    first: Sequence[float], second: Sequence[float]
) -> Optional[float]:
    if len(first) != len(second) or len(first) < 2:
        return None
    first_std = statistics.pstdev(first)
    second_std = statistics.pstdev(second)
    if first_std == 0 or second_std == 0:
        return None
    first_mean = statistics.mean(first)
    second_mean = statistics.mean(second)
    covariance = sum(
        (left - first_mean) * (right - second_mean)
        for left, right in zip(first, second)
    ) / len(first)
    return covariance / (first_std * second_std)


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
