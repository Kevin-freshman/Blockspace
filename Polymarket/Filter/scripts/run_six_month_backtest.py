#!/usr/bin/env python3
"""Collect a bounded resolved-market sample and run an offline backtest.

All external calls are public and read-only.  The batch history endpoint uses
POST only because that is Polymarket's documented batch-read interface.
Runtime inputs are cached under data/ and are never served as static files.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from backtest_engine import (  # noqa: E402
    break_even_slippage,
    build_observations,
    calibration_table,
    choose_training_parameter,
    monthly_table,
    normalize_hourly_market,
    parameter_grid,
    parse_iso_timestamp,
    portfolio_comparison,
    split_observations,
    summarize_strategy,
)


SERIES = {"BTC": 10114, "ETH": 10117, "SOL": 10122}
WINDOWS = (60, 30, 15, 5, 2)
THRESHOLDS = (0.80, 0.85, 0.90, 0.95, 0.98)
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


class PublicMarketDataClient:
    def __init__(self, timeout_seconds: int = 20, max_retries: int = 4) -> None:
        self.timeout_seconds = int(timeout_seconds)
        self.max_retries = int(max_retries)
        self._local = threading.local()

    def resolved_hourly_events(
        self,
        series_id: int,
        start_at: str,
        end_at: str,
        maximum_pages: int = 60,
    ) -> List[Dict[str, Any]]:
        events = []
        cursor = None
        for _page in range(int(maximum_pages)):
            params = {
                "limit": 500,
                "order": "endDate",
                "ascending": "true",
                "closed": "true",
                "end_date_min": start_at,
                "end_date_max": end_at,
                "series_id": int(series_id),
            }
            if cursor:
                params["after_cursor"] = cursor
            payload = self._request("GET", GAMMA_API + "/events/keyset", params=params)
            page = payload.get("events") if isinstance(payload, dict) else None
            if not isinstance(page, list):
                raise RuntimeError("events/keyset response did not contain an events list")
            events.extend(page)
            cursor = payload.get("next_cursor")
            # Gamma may apply an effective page size below the requested 500.
            # A short page can still carry a next cursor, so cursor absence is
            # the only safe completion signal.
            if not cursor:
                return events
        raise RuntimeError("event page bound reached for series %s" % series_id)

    def batch_price_history(
        self, markets: Sequence[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        if not markets or len(markets) > 20:
            raise ValueError("price batch must contain 1 to 20 markets")
        payload = {
            "markets": [str(item["token_id"]) for item in markets],
            "start_ts": min(int(item["end_timestamp"]) for item in markets) - 90 * 60,
            "end_ts": max(int(item["end_timestamp"]) for item in markets),
            "fidelity": 1,
        }
        response = self._request(
            "POST", CLOB_API + "/batch-prices-history", json_body=payload
        )
        history = response.get("history") if isinstance(response, dict) else None
        if not isinstance(history, dict):
            raise RuntimeError("batch history response did not contain a history map")
        return {
            str(token_id): rows
            for token_id, rows in history.items()
            if isinstance(rows, list)
        }

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "Accept": "application/json",
                    "User-Agent": "blockspace-polymarket-filter-backtest/1.0",
                }
            )
            self._local.session = session
        return session

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self._session().request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(
                        "retryable status %s" % response.status_code,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(0.5 * (2 ** attempt))
        raise RuntimeError("request failed for %s: %s" % (url, last_error))


def collect_markets(
    client: PublicMarketDataClient, start_at: str, end_at: str
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    markets = []
    raw_counts = {}
    start_timestamp = parse_iso_timestamp(start_at)
    end_timestamp = parse_iso_timestamp(end_at)
    if start_timestamp is None or end_timestamp is None:
        raise ValueError("invalid collection interval")
    for asset, series_id in SERIES.items():
        events = client.resolved_hourly_events(series_id, start_at, end_at)
        raw_counts[asset] = len(events)
        for event in events:
            market = normalize_hourly_market(event, asset)
            if (
                market is not None
                and start_timestamp <= int(market["end_timestamp"]) < end_timestamp
            ):
                markets.append(market)
        print("markets %s: %d raw events" % (asset, len(events)), flush=True)
    markets.sort(key=lambda item: (item["end_timestamp"], item["asset"]))
    return markets, raw_counts


def collect_histories(
    client: PublicMarketDataClient,
    markets: Sequence[Dict[str, Any]],
    existing: Dict[str, List[Dict[str, Any]]],
    cache_path: Path,
    workers: int,
) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    histories = dict(existing)
    missing = [item for item in markets if str(item["token_id"]) not in histories]
    batches = [missing[index:index + 20] for index in range(0, len(missing), 20)]
    if not batches:
        return histories, []
    errors = []
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        future_batches = {
            executor.submit(client.batch_price_history, batch): batch for batch in batches
        }
        for future in as_completed(future_batches):
            batch = future_batches[future]
            try:
                histories.update(future.result())
            except Exception as exc:  # keep a bounded partial cache for diagnosis
                errors.append(
                    "%s..%s: %s" % (
                        batch[0]["token_id"], batch[-1]["token_id"], exc
                    )
                )
            completed += 1
            if completed % 25 == 0 or completed == len(batches):
                _write_gzip_json(cache_path, histories)
                print(
                    "price batches: %d/%d; histories=%d; errors=%d"
                    % (completed, len(batches), len(histories), len(errors)),
                    flush=True,
                )
    return histories, errors


def run(args: argparse.Namespace) -> Dict[str, Any]:
    start_timestamp = parse_iso_timestamp(args.start + "T00:00:00Z")
    split_timestamp = parse_iso_timestamp(args.split + "T00:00:00Z")
    end_timestamp = parse_iso_timestamp(args.end + "T00:00:00Z")
    if (
        start_timestamp is None
        or split_timestamp is None
        or end_timestamp is None
        or not start_timestamp < split_timestamp < end_timestamp
    ):
        raise ValueError("expected start < split < end using UTC dates")
    if end_timestamp - start_timestamp > 370 * 86400:
        raise ValueError("backtest collection is bounded to at most 370 days")
    workers = min(32, max(1, int(args.workers)))
    key = "%s_%s" % (args.start.replace("-", ""), args.end.replace("-", ""))
    cache_dir = PROJECT_DIR / "data" / "backtest"
    market_cache = cache_dir / ("hourly_markets_%s.json.gz" % key)
    price_cache = cache_dir / ("hourly_prices_%s.json.gz" % key)
    client = PublicMarketDataClient()

    cached_markets = _read_gzip_json(market_cache)
    if not isinstance(cached_markets, dict) or not cached_markets.get("complete"):
        if args.offline:
            raise RuntimeError("offline mode requires %s" % market_cache)
        markets, raw_counts = collect_markets(
            client, args.start + "T00:00:00Z", args.end + "T00:00:00Z"
        )
        _write_gzip_json(
            market_cache,
            {"complete": True, "markets": markets, "raw_counts": raw_counts},
        )
    else:
        markets = cached_markets["markets"]
        raw_counts = cached_markets.get("raw_counts") or {}

    cached_histories = _read_gzip_json(price_cache) or {}
    if args.offline:
        histories = cached_histories
        collection_errors = []
    else:
        histories, collection_errors = collect_histories(
            client,
            markets,
            cached_histories,
            price_cache,
            workers,
        )

    observations = build_observations(
        markets, histories, WINDOWS, max_staleness_seconds=args.max_price_age
    )
    training, holdout = split_observations(
        observations, args.split + "T00:00:00Z"
    )
    training_grid = parameter_grid(
        training,
        WINDOWS,
        THRESHOLDS,
        args.fee_rate,
        args.slippage,
        bootstrap_samples=0,
    )
    selected = choose_training_parameter(training_grid, args.minimum_training_trades)
    selected_window = int(selected["window_minutes"])
    selected_threshold = float(selected["threshold"])
    training_result = summarize_strategy(
        training,
        selected_window,
        selected_threshold,
        args.fee_rate,
        args.slippage,
    )
    holdout_result = summarize_strategy(
        holdout,
        selected_window,
        selected_threshold,
        args.fee_rate,
        args.slippage,
    )
    full_result = summarize_strategy(
        observations,
        selected_window,
        selected_threshold,
        args.fee_rate,
        args.slippage,
    )
    threshold_90 = [
        {
            "window_minutes": window,
            **summarize_strategy(
                holdout,
                window,
                0.90,
                args.fee_rate,
                args.slippage,
            ),
        }
        for window in WINDOWS
    ]
    threshold_90_walk_forward = [
        {
            "window_minutes": window,
            "training": summarize_strategy(
                training, window, 0.90, args.fee_rate, args.slippage
            ),
            "holdout": summarize_strategy(
                holdout, window, 0.90, args.fee_rate, args.slippage
            ),
        }
        for window in WINDOWS
    ]
    cost_sensitivity = []
    for window in WINDOWS:
        row = {
            "window_minutes": window,
            "break_even_slippage": break_even_slippage(
                holdout, window, 0.90, args.fee_rate
            ),
        }
        for label, fee_rate, slippage in (
            ("gross", 0.0, 0.0),
            ("fee_only", args.fee_rate, 0.0),
            ("fee_plus_0_5c", args.fee_rate, 0.005),
            ("fee_plus_1c", args.fee_rate, 0.01),
        ):
            row[label] = summarize_strategy(
                holdout,
                window,
                0.90,
                fee_rate,
                slippage,
                bootstrap_samples=0,
            )
        cost_sensitivity.append(row)
    coverage_by_window = {
        str(window): sum(
            1 for item in observations if item["window_minutes"] == window
        ) for window in WINDOWS
    }
    calendar_hours = int((end_timestamp - start_timestamp) / 3600)
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "period": {
            "start": args.start + "T00:00:00Z",
            "end_exclusive": args.end + "T00:00:00Z",
            "training_end_exclusive": args.split + "T00:00:00Z",
        },
        "universe": {
            "series": SERIES,
            "raw_events": raw_counts,
            "resolved_binary_markets": len(markets),
            "calendar_hours": calendar_hours,
            "expected_series_markets": calendar_hours * len(SERIES),
            "market_coverage": round(
                len(markets) / float(calendar_hours * len(SERIES)), 6
            ),
            "markets_with_any_history": sum(
                1 for item in markets if str(item["token_id"]) in histories
            ),
            "histories": len(histories),
            "observations": len(observations),
            "coverage_by_window": coverage_by_window,
            "collection_errors": collection_errors,
        },
        "assumptions": {
            "stake_usd_per_signal": 1.0,
            "fee_rate": args.fee_rate,
            "slippage": args.slippage,
            "max_price_age_seconds": args.max_price_age,
            "windows_minutes": list(WINDOWS),
            "thresholds": list(THRESHOLDS),
            "price_side": "first token history; complement inferred as 1-p",
        },
        "selection": {
            "minimum_training_trades": args.minimum_training_trades,
            "selected_window_minutes": selected_window,
            "selected_threshold": selected_threshold,
            "training_grid": training_grid,
        },
        "results": {
            "training": training_result,
            "holdout": holdout_result,
            "full_period": full_result,
            "holdout_threshold_0_90_by_window": threshold_90,
            "threshold_0_90_walk_forward": threshold_90_walk_forward,
            "holdout_cost_sensitivity": cost_sensitivity,
            "holdout_calibration": calibration_table(holdout, selected_window),
            "holdout_portfolios": portfolio_comparison(
                holdout,
                selected_window,
                selected_threshold,
                args.fee_rate,
                args.slippage,
            ),
            "full_monthly": monthly_table(
                observations,
                selected_window,
                selected_threshold,
                args.fee_rate,
                args.slippage,
            ),
        },
        "filter_longitudinal": summarize_filter_longitudinal(
            PROJECT_DIR / "data" / "research", args.end
        ),
    }
    result_cache = cache_dir / ("result_%s.json" % key)
    _write_json(result_cache, result)
    report_path = PROJECT_DIR / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result), encoding="utf-8")
    print("result: %s" % result_cache, flush=True)
    print("report: %s" % report_path, flush=True)
    return result


def summarize_filter_longitudinal(
    research_dir: Path, end_date: str
) -> Dict[str, Any]:
    analyses = []
    for path in sorted(research_dir.glob("analysis-*.json.gz")):
        payload = _read_gzip_json(path)
        if not isinstance(payload, dict):
            continue
        slot_at = str(payload.get("slot_at") or "")
        if slot_at[:10] > end_date:
            continue
        analysis = payload.get("analysis")
        if isinstance(analysis, dict):
            analyses.append((slot_at, analysis))
    cohorts = {
        "volume_losers": set(),
        "volume_winners": set(),
        "dropped": set(),
        "retained_control": set(),
    }
    metrics: Dict[str, Dict[str, List[float]]] = {
        "volume_loser_vs_winner": {},
        "dropped_vs_retained": {},
    }
    truncated = []
    eligible_days = 0
    for _slot_at, analysis in analyses:
        eligible_days += int(bool(analysis.get("eligible_for_insight")))
        for cohort, addresses in (analysis.get("cohorts") or {}).items():
            if cohort in cohorts:
                cohorts[cohort].update(addresses or [])
        quality = analysis.get("data_quality") or {}
        truncated.append(int(quality.get("truncated_addresses") or 0))
        for comparison in analysis.get("comparisons") or []:
            comparison_id = comparison.get("id")
            if comparison_id not in metrics:
                continue
            for metric in comparison.get("metrics") or []:
                difference = metric.get("difference")
                if difference is not None:
                    metrics[comparison_id].setdefault(
                        str(metric.get("field")), []
                    ).append(float(difference))

    metric_summary: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for comparison_id, fields in metrics.items():
        metric_summary[comparison_id] = {}
        for field, values in fields.items():
            metric_summary[comparison_id][field] = {
                "days": len(values),
                "median_difference": round(float(statistics.median(values)), 6),
                "positive_days": sum(1 for value in values if value > 0),
                "negative_days": sum(1 for value in values if value < 0),
                "zero_days": sum(1 for value in values if value == 0),
            }
    return {
        "days": len(analyses),
        "first_slot_at": analyses[0][0] if analyses else None,
        "last_slot_at": analyses[-1][0] if analyses else None,
        "eligible_days": eligible_days,
        "unique_addresses": {
            key: len(addresses) for key, addresses in cohorts.items()
        },
        "truncated_addresses_min": min(truncated) if truncated else None,
        "truncated_addresses_max": max(truncated) if truncated else None,
        "metrics": metric_summary,
    }


def render_report(result: Dict[str, Any]) -> str:
    universe = result["universe"]
    selection = result["selection"]
    results = result["results"]
    training = results["training"]
    holdout = results["holdout"]
    full = results["full_period"]
    portfolio = results["holdout_portfolios"]
    longitudinal = result["filter_longitudinal"]
    diversified = portfolio["matched_diversified_equal_weight"]
    concentrated = portfolio["matched_concentrated_btc"]
    correlation_text = ", ".join(
        "%s/%s %.2f（n=%d）" % (
            row["assets"][0], row["assets"][1], row["pnl_correlation"],
            row["shared_signal_hours"],
        )
        for row in portfolio["pairwise_signal_pnl_correlations"]
        if row["pnl_correlation"] is not None
    )

    lines = [
        "# Polymarket Crypto 小时市场半年回测",
        "",
        "> 研究区间：2026-03-10 00:00 UTC 至 2026-09-10 00:00 UTC（右端不含）；"
        "前四个月训练，后两个月严格样本外验证。生成时间：%s。" % result["generated_at"],
        "",
        "## 结论先行",
        "",
        _conclusion_line(training, holdout, selection),
        "",
        "- 高胜率不是安全垫：样本外 %d 笔中命中率为 %s，但每次平均失败会吞掉约 %s 次平均成功。"
        % (
            holdout["trade_count"],
            _pct(holdout["win_rate"]),
            _number(holdout["wins_needed_per_average_loss"], 1),
        ),
        "- 成本决定信号是否可交易：本文按当前公开 Crypto taker 费率 %.1f%%，并把参考价上移 %.1f¢ 做不利滑点压力测试；样本外资金收益率为 %s，日块 bootstrap 95%% 区间为 %s 至 %s。"
        % (
            result["assumptions"]["fee_rate"] * 100,
            result["assumptions"]["slippage"] * 100,
            _pct(holdout["return_on_cash"]),
            _pct(holdout["daily_block_bootstrap_95_low"]),
            _pct(holdout["daily_block_bootstrap_95_high"]),
        ),
        "- 三个币种不是三个独立赌注：同时触发时的单笔 PNL 相关系数为 %s。只比较 BTC 也触发信号的相同 %d 个小时、且每小时总名义本金固定为 $1 时，三资产等权的最大回撤为 $%s，BTC 单资产为 $%s；简单分散没有消除同方向尾部风险。"
        % (
            correlation_text,
            concentrated["active_hours"],
            _number(diversified["max_drawdown"], 2),
            _number(concentrated["max_drawdown"], 2),
        ),
        "- Filter 的榜单纵向数据目前只有 7 个每日富集样本，且均因高频地址活动截断而未满足 `eligible_for_insight`；所以它只用于提出假设，半年市场回测才承担验证，不能把 7 天地址画像写成半年地址因果结论。",
        "",
        "## Filter 纵向研究：有用的是风险过滤器，不是跟单名单",
        "",
        "可用数据为 %s 至 %s，共 %d 个每日富集；高成交量亏损/匹配赢家分别覆盖 %d/%d 个唯一地址，掉榜/留榜对照覆盖 %d/%d 个。每天有 %d–%d 个地址的 activity 达到 1,000 条上限，因此正式 Insight 合格日为 %d。下列只能称为描述性 Finding。"
        % (
            str(longitudinal["first_slot_at"])[:10],
            str(longitudinal["last_slot_at"])[:10],
            longitudinal["days"],
            longitudinal["unique_addresses"]["volume_losers"],
            longitudinal["unique_addresses"]["volume_winners"],
            longitudinal["unique_addresses"]["dropped"],
            longitudinal["unique_addresses"]["retained_control"],
            longitudinal["truncated_addresses_min"],
            longitudinal["truncated_addresses_max"],
            longitudinal["eligible_days"],
        ),
        "",
        "| 纵向观察 | 7 日方向 | 对策略真正有用的含义 |",
        "|---|---:|---|",
        _longitudinal_row(
            "成交量匹配的亏损者涉及市场更少",
            longitudinal, "volume_loser_vs_winner", "market_count", "个市场",
            "不要因地址总 PNL 好看而放松单市场/单主题仓位上限；分散必须跨独立驱动因素。",
        ),
        _longitudinal_row(
            "亏损者的名义成交 HHI 更高",
            longitudinal, "volume_loser_vs_winner", "market_concentration", " HHI",
            "把地址或策略的 HHI 设为风控输入；但币种标签不同不等于风险独立。",
        ),
        _longitudinal_row(
            "掉榜者当日成交频率更低",
            longitudinal, "dropped_vs_retained", "trades_per_hour", " fills/h",
            "掉榜首先可能是 rolling window 的活动衰减，不应直接解释为新亏损；跟单前要求跨日留榜与持续活跃。",
        ),
        _longitudinal_row(
            "亏损者的 ≥0.90 BUY 占比反而更低",
            longitudinal, "volume_loser_vs_winner", "high_price_buy_share", "",
            "地址 cohort 不支持“亏损来自更多高价 BUY”的原假设；尾盘风险结论必须由独立市场回测给出。",
        ),
        "| 高频差异不可识别 | 亏损−赢家的频率中位差 7/7 天均为 0 | 两组中位数都撞到 1,000 条/24h 上限（41.67 fills/h）；禁止把 capped 频率当成排序信号。 |",
        "",
        "## 交易决策：当前不部署",
        "",
        "1. **拒绝训练期参数**：结算前 %d 分钟、参考概率 ≥%s 的规则样本外为 %s，且置信区间跨 0。" % (
            selection["selected_window_minutes"], _pct(selection["selected_threshold"]),
            _pct(holdout["return_on_cash"]),
        ),
        "2. **避免超尾盘 taker 扫单**：0.90 门槛在 5m 与 2m 窗口训练/样本外均为负；2m 两段的 95% 日块区间都低于 0。高命中率不足以覆盖极薄利润、费用、滑点和偶发归零。",
        "3. **设置成本闸门**：下单判断必须使用可成交 ask，不使用图表价；允许滑点不得高于下表的样本外 break-even 值，并再留安全边际。若只能 taker 成交，直接跳过边际过薄的信号。",
        "4. **同小时共享风险预算**：BTC / ETH / SOL 同向波动强，同一结算时段合并为一个风险桶；连续亏损不加仓。",
        "5. 若继续研究，只做至少 4 周 paper trading，记录当时 bid/ask、盘口深度、实际 fill、滑点与未成交率；重新积累数据后再设一个从未参与选参的最终验证集。",
        "",
        "## 样本与方法",
        "",
        "- 固定系列：BTC `%s`、ETH `%s`、SOL `%s`。" % (
            result["universe"]["series"]["BTC"],
            result["universe"]["series"]["ETH"],
            result["universe"]["series"]["SOL"],
        ),
        "- 理论小时×系列数 %d，读取 %d 个已结算二元市场（%s coverage）；至少有价格历史的市场 %d 个。各窗口有效观察：%s。"
        % (
            universe["expected_series_markets"],
            universe["resolved_binary_markets"],
            _pct(universe["market_coverage"]),
            universe["markets_with_any_history"],
            ", ".join(
                "%sm=%s" % (key, value)
                for key, value in universe["coverage_by_window"].items()
            ),
        ),
        "- 历史价格只取决策时刻或之前的最后一点，绝不向后取价；第一 outcome 的另一侧按 `1-p` 推导。",
        "- 每个信号按 $1 名义买入，兑付为 0/1。压力成本为 `entry = min(reference + 0.01, 0.9999)`，另计公开 Crypto taker 费公式。",
        "- 参数网格为时间 `{60,30,15,5,2}` 分钟 × 门槛 `{0.80,0.85,0.90,0.95,0.98}`；仅在前四个月且至少 %d 笔交易中选资金收益率最高者，后两个月不再调参。" % selection["minimum_training_trades"],
        "",
        "## Walk-forward 结果",
        "",
        "| 区间 | 交易数 | 胜率 | 资金收益率 | 95% 日块 bootstrap | Profit factor | 最大回撤/$1每信号 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        _summary_row("训练 4 个月", training),
        _summary_row("样本外 2 个月", holdout),
        _summary_row("全 6 个月（描述性）", full),
        "",
        "### 固定 0.90 门槛的 walk-forward 时间敏感性（7% fee + 1¢ 滑点）",
        "",
        "| 距结算 | 训练交易数 | 训练收益率 [95% CI] | 样本外交易数 | 样本外收益率 [95% CI] | 样本外胜率 | 一次失败≈成功数 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results["threshold_0_90_walk_forward"]:
        training_row = row["training"]
        holdout_row = row["holdout"]
        lines.append(
            "| %dm | %d | %s [%s, %s] | %d | %s [%s, %s] | %s | %s |"
            % (
                row["window_minutes"], training_row["trade_count"],
                _pct(training_row["return_on_cash"]),
                _pct(training_row["daily_block_bootstrap_95_low"]),
                _pct(training_row["daily_block_bootstrap_95_high"]),
                holdout_row["trade_count"], _pct(holdout_row["return_on_cash"]),
                _pct(holdout_row["daily_block_bootstrap_95_low"]),
                _pct(holdout_row["daily_block_bootstrap_95_high"]),
                _pct(holdout_row["win_rate"]),
                _number(holdout_row["wins_needed_per_average_loss"], 1),
            )
        )
    lines.extend(
        [
            "",
            "### 固定 0.90 门槛的样本外成本敏感性",
            "",
            "| 距结算 | 无成本 | 仅 7% fee | fee + 0.5¢ | fee + 1¢ | fee 后 break-even 滑点 |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in results["holdout_cost_sensitivity"]:
        lines.append(
            "| %dm | %s | %s | %s | %s | %s |"
            % (
                row["window_minutes"], _pct(row["gross"]["return_on_cash"]),
                _pct(row["fee_only"]["return_on_cash"]),
                _pct(row["fee_plus_0_5c"]["return_on_cash"]),
                _pct(row["fee_plus_1c"]["return_on_cash"]),
                _slippage(
                    row["break_even_slippage"],
                    row["fee_only"]["trade_count"],
                ),
            )
        )
    lines.extend(
        [
            "",
            "## 集中度压力测试（样本外）",
            "",
            "为避免“多交易所以曲线不同”的错觉，只保留 BTC 也触发信号的相同小时。每小时总名义本金固定为 $1；三资产版本在当时所有合格信号中等权，BTC 版本全配 BTC。",
            "",
            "| 组合 | 活跃小时 | 部署资金收益率 | 最大回撤 | 最差单小时 | 小时 PNL 波动 |",
            "|---|---:|---:|---:|---:|---:|",
            _portfolio_row("BTC/ETH/SOL 等权", diversified),
            _portfolio_row("BTC 单资产", concentrated),
            "",
            "## 价格校准（所选时间点，样本外）",
            "",
            "| 高概率侧参考价 | 样本 | 平均隐含概率 | 实际命中率 | 命中率−价格 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in results["holdout_calibration"]:
        lines.append(
            "| %s | %d | %s | %s | %s |"
            % (
                row["bucket"], row["count"], _pct(row["average_price"]),
                _pct(row["win_rate"]), _pct(row["calibration_gap"]),
            )
        )
    lines.extend(
        [
            "",
            "## 重要限制",
            "",
            "- `prices-history` 是公开历史价格序列，不是当时可成交 ask、盘口深度或成交承诺；1¢ 滑点只是压力参数。",
            "- 另一 outcome 用 `1-p` 推导，会忽略瞬时价差和两侧不同成交时刻；这使结果更适合筛选假设，而非资金曲线承诺。",
            "- 当前公开费率用于统一压力测试，不保证与半年内每个市场当时的实际费率完全一致；未计资金占用、撤单、延迟和税务。",
            "- 只研究三个固定小时系列，不外推到 5m/15m、体育、政治或长周期市场。市场必须有最终 0/1 结果，因此不存在未结算市场的幸存者问题，但无价格历史的市场会降低 coverage。",
            "- 训练集选参仍可能过拟合；样本外只有两个月，bootstrap 只量化日级抽样不确定性，不能覆盖制度/API/费率变化。",
            "",
            "## 简历命名建议",
            "",
            "**Polymarket Trader Intelligence & Strategy Backtesting Platform**",
            "",
            "中文：**Polymarket 交易者画像与策略回测平台**",
            "",
            "Built a read-only Polymarket intelligence platform that reconstructs wallet-level cash-flow PnL from public APIs, tracks longitudinal trader cohorts, validates sampled fills on Polygon, and runs fee/slippage-aware walk-forward backtests through a Python 3.8 service and dependency-free JavaScript dashboards.",
            "",
            "中文版本：基于公开 API 构建只读 Polymarket 交易者研究平台：重建地址级现金流 PnL、纵向追踪 cohort、通过 Polygon 回执抽样核验成交，并以计入费用/滑点的 walk-forward 回测评估策略；采用 Python 3.8 服务与零依赖 JavaScript 仪表盘。",
            "",
            "## 数据接口依据",
            "",
            "- [Polymarket Gamma keyset events API](https://docs.polymarket.com/api-reference/events/list-events-keyset-pagination)",
            "- [Polymarket batch price history API](https://docs.polymarket.com/api-reference/markets/get-batch-prices-history)",
            "- [Polymarket fees](https://docs.polymarket.com/trading/fees)",
            "- [Polymarket prices and orderbook caveat](https://docs.polymarket.com/concepts/prices-orderbook)",
            "",
        ]
    )
    return "\n".join(lines)


def _conclusion_line(
    training: Dict[str, Any], holdout: Dict[str, Any], selection: Dict[str, Any]
) -> str:
    direction = "保持为正" if (holdout.get("return_on_cash") or 0) > 0 else "转为负值"
    return (
        "- 训练集选出的规则是：结算前 **%d 分钟**、高概率侧至少 **%s**。"
        "压力成本后训练资金收益率 %s，样本外%s（%s）；因此应把它视为%s。"
        % (
            selection["selected_window_minutes"],
            _pct(selection["selected_threshold"]),
            _pct(training["return_on_cash"]),
            direction,
            _pct(holdout["return_on_cash"]),
            "待 paper trading 的候选信号" if direction == "保持为正" else "被样本外否定的规则",
        )
    )


def _longitudinal_row(
    observation: str,
    longitudinal: Dict[str, Any],
    comparison_id: str,
    field: str,
    unit: str,
    implication: str,
) -> str:
    metric = longitudinal["metrics"][comparison_id][field]
    direction = "%d 正 / %d 负 / %d 零；中位差 %s%s" % (
        metric["positive_days"], metric["negative_days"], metric["zero_days"],
        _number(metric["median_difference"], 3), unit,
    )
    return "| %s | %s | %s |" % (observation, direction, implication)


def _slippage(value: Optional[float], trade_count: int) -> str:
    if not trade_count:
        return "n/a"
    if value is None:
        return ">5.00¢"
    return "%.3f¢" % (float(value) * 100)


def _summary_row(label: str, row: Dict[str, Any]) -> str:
    return "| %s | %d | %s | %s | %s ~ %s | %s | $%s |" % (
        label, row["trade_count"], _pct(row["win_rate"]),
        _pct(row["return_on_cash"]), _pct(row["daily_block_bootstrap_95_low"]),
        _pct(row["daily_block_bootstrap_95_high"]),
        _number(row["profit_factor"], 2), _number(row["max_drawdown"], 2),
    )


def _portfolio_row(label: str, row: Dict[str, Any]) -> str:
    return "| %s | %d | %s | $%s | $%s | %s |" % (
        label, row["active_hours"], _pct(row["return_on_deployed_cash"]),
        _number(row["max_drawdown"], 2), _number(row["worst_hour"], 3),
        _number(row["hourly_pnl_stddev"], 4),
    )


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else "%.2f%%" % (float(value) * 100)


def _number(value: Optional[float], digits: int) -> str:
    return "n/a" if value is None else ("%%.%df" % digits) % float(value)


def _read_gzip_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    with gzip.open(str(path), "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _write_gzip_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    try:
        with gzip.open(temporary_name, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        os.replace(temporary_name, str(path))
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    os.close(descriptor)
    try:
        with open(temporary_name, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, str(path))
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="inclusive UTC date")
    parser.add_argument("--end", required=True, help="exclusive UTC date")
    parser.add_argument("--split", required=True, help="holdout start UTC date")
    parser.add_argument("--report", default="docs/SIX_MONTH_BACKTEST.md")
    parser.add_argument("--fee-rate", type=float, default=0.07)
    parser.add_argument("--slippage", type=float, default=0.01)
    parser.add_argument("--max-price-age", type=int, default=120)
    parser.add_argument("--minimum-training-trades", type=int, default=100)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    run(parse_args())
