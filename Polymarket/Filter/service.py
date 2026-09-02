"""Scan orchestration, live polling, caching, and export helpers."""

from __future__ import annotations

import copy
import csv
import io
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from filter_engine import (
    apply_chain_filter,
    apply_filter,
    build_address_metrics,
    enrich_trade_onchain,
    infer_interval_end_timestamp,
    normalize_trade,
    summarize_chain_receipts,
    summarize_positions,
    summarize_tail_analysis,
    trade_key,
    utc_iso,
)
from polymarket_client import PolymarketClient
from polygon_client import PolygonRpcClient
from research_engine import (
    ALLOWED_TOP_K,
    analyze_leaderboard_transition,
    build_hypothesis_ledger,
    build_research_snapshot,
    build_strategy_fingerprint,
    choose_research_cohorts,
    compare_enriched_cohorts,
    normalize_leaderboard_rows,
    research_slot_epoch,
    research_slot_id,
)
from research_store import ResearchStore


def validate_scan_config(payload: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """Build a bounded runtime config from the browser payload."""
    payload = payload or {}
    result = copy.deepcopy(base)
    leaderboard = result["leaderboard"]
    filters = result["filter"]
    chain = result["chain"]
    live = result["live"]

    period = str(payload.get("time_period", leaderboard["time_period"])).upper()
    if period not in {"DAY", "WEEK", "MONTH", "ALL"}:
        raise ValueError("time_period must be DAY, WEEK, MONTH, or ALL")
    order_by = str(payload.get("order_by", leaderboard["order_by"])).upper()
    if order_by not in {"PNL", "VOL"}:
        raise ValueError("order_by must be PNL or VOL")
    leaderboard["time_period"] = period
    leaderboard["order_by"] = order_by
    leaderboard["candidate_limit"] = _bounded_int(
        payload.get("candidate_limit", leaderboard["candidate_limit"]), 1, 1000
    )

    filters["lookback_hours"] = _bounded_float(
        payload.get("lookback_hours", filters["lookback_hours"]), 1, 24 * 90
    )
    filters["min_trade_count"] = _bounded_int(
        payload.get("min_trade_count", filters["min_trade_count"]), 0, 100000
    )
    filters["min_trades_per_day"] = _bounded_float(
        payload.get("min_trades_per_day", filters["min_trades_per_day"]), 0, 100000
    )
    filters["min_return_efficiency"] = _optional_bounded_float(
        payload.get("min_return_efficiency", filters.get("min_return_efficiency")),
        -1000,
        1000,
    )
    filters["min_estimated_pnl_per_activity"] = _optional_bounded_float(
        payload.get(
            "min_estimated_pnl_per_activity",
            filters.get("min_estimated_pnl_per_activity"),
        ),
        -1000000000,
        1000000000,
    )
    result_sort = str(
        payload.get("result_sort", filters.get("result_sort", "FREQUENCY"))
    ).upper()
    if result_sort not in {"FREQUENCY", "RETURN", "AVG_PNL", "PNL"}:
        raise ValueError("result_sort must be FREQUENCY, RETURN, AVG_PNL, or PNL")
    filters["result_sort"] = result_sort
    filters["min_median_hours_to_settlement"] = _optional_bounded_float(
        payload.get(
            "min_median_hours_to_settlement",
            filters.get("min_median_hours_to_settlement"),
        ),
        0,
        24 * 3650,
    )
    filters["max_median_hours_to_settlement"] = _optional_bounded_float(
        payload.get(
            "max_median_hours_to_settlement",
            filters.get("max_median_hours_to_settlement"),
        ),
        0,
        24 * 3650,
    )
    minimum = filters.get("min_median_hours_to_settlement")
    maximum = filters.get("max_median_hours_to_settlement")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("minimum settlement distance cannot exceed maximum")

    chain["receipts_per_address"] = _bounded_int(
        payload.get("chain_receipts_per_address", chain["receipts_per_address"]),
        1,
        50,
    )
    chain["min_confirmed_transactions"] = _bounded_int(
        payload.get(
            "min_chain_confirmed_transactions",
            chain["min_confirmed_transactions"],
        ),
        0,
        50,
    )
    chain["min_verification_rate"] = _bounded_float(
        payload.get("min_chain_verification_rate", chain["min_verification_rate"]),
        0,
        1,
    )
    chain["min_polymarket_contract_rate"] = _bounded_float(
        payload.get(
            "min_polymarket_contract_rate",
            chain["min_polymarket_contract_rate"],
        ),
        0,
        1,
    )
    if chain["min_confirmed_transactions"] > chain["receipts_per_address"]:
        raise ValueError("minimum confirmed transactions cannot exceed receipt sample")

    live["poll_seconds"] = _bounded_int(
        payload.get("poll_seconds", live["poll_seconds"]), 5, 300
    )
    return result


class FilterService:
    def __init__(
        self,
        base_dir: Path,
        config: Dict[str, Any],
        client: Optional[PolymarketClient] = None,
        chain_client: Optional[PolygonRpcClient] = None,
        clock: Callable[[], float] = time.time,
        start_background: bool = True,
    ) -> None:
        self.base_dir = base_dir
        self.data_dir = base_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.base_config = copy.deepcopy(config)
        self.client = client or PolymarketClient()
        self.chain_client = chain_client or PolygonRpcClient(
            config["chain"]["rpc_url"], config["chain"]["chain_id"]
        )
        self.clock = clock
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.market_cache: Dict[str, Dict[str, Any]] = self._load_json(
            self.data_dir / "market_cache.json", {}
        )
        self.receipt_cache: Dict[str, Dict[str, Any]] = self._load_json(
            self.data_dir / "receipt_cache.json", {}
        )
        self.research_config = copy.deepcopy(config.get("research") or {})
        self.research_store = ResearchStore(
            self.data_dir,
            int(self.research_config.get("retention_days", 90)),
        )
        self.research_state: Dict[str, Any] = {
            "status": "collecting" if self.research_config.get("enabled") else "disabled",
            "active": False,
            "last_capture_at": None,
            "last_slot_at": None,
            "next_retry_epoch": None,
            "errors": [],
        }
        self._research_capture_lock = threading.Lock()
        self.state: Dict[str, Any] = {
            "status": "idle",
            "scan_id": None,
            "category": "CRYPTO",
            "config": copy.deepcopy(config),
            "progress": {"stage": "idle", "completed": 0, "total": 0, "message": ""},
            "addresses": [],
            "filtered_addresses": [],
            "tail_analysis": {},
            "live_trades": [],
            "last_scan_at": None,
            "last_live_at": None,
            "last_live_epoch": None,
            "last_positions_epoch": None,
            "errors": [],
        }
        self._last_live_poll_monotonic = 0.0
        self._live_thread = threading.Thread(
            target=self._live_loop, name="polymarket-live-poll", daemon=True
        )
        self._research_thread = threading.Thread(
            target=self._research_loop,
            name="polymarket-leaderboard-research",
            daemon=True,
        )
        if start_background:
            self._live_thread.start()
            self._research_thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        if self._live_thread.is_alive():
            self._live_thread.join(timeout=3)
        if self._research_thread.is_alive():
            self._research_thread.join(timeout=3)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.state)

    def public_snapshot(self, live_limit: int = 200) -> Dict[str, Any]:
        """Return the compact browser view while keeping full data for exports."""
        snapshot = self.snapshot()
        snapshot["live_trades"] = snapshot["live_trades"][:live_limit]
        return snapshot

    def public_research_summary(self, top_k: int = 100) -> Dict[str, Any]:
        if top_k not in ALLOWED_TOP_K:
            raise ValueError("top_k must be 100, 500, or 1000")
        config = self.research_config
        with self.lock:
            runtime = copy.deepcopy(self.research_state)
        snapshots = self.research_store.load_latest(1)
        oldest = self.research_store.load_oldest()
        snapshot_count = self.research_store.count()
        collection = {
            "enabled": bool(config.get("enabled")),
            "category": "CRYPTO",
            "time_period": str(config.get("time_period", "DAY")).upper(),
            "cadence_hours": int(config.get("cadence_hours", 6)),
            "comparison_hours": int(config.get("comparison_hours", 24)),
            "snapshot_count": snapshot_count,
            "oldest_slot_at": oldest.get("slot_at") if oldest else None,
            "latest_slot_at": snapshots[0].get("slot_at") if snapshots else None,
            "last_capture_at": runtime.get("last_capture_at"),
            "active": bool(runtime.get("active")),
        }
        base = {
            "status": runtime.get("status", "collecting"),
            "collection": collection,
            "top_k": top_k,
            "current": None,
            "transition": None,
            "latest_analysis": None,
            "hypotheses": [],
            "errors": runtime.get("errors") or [],
        }
        if not snapshots:
            base["hypotheses"] = build_hypothesis_ledger(
                [],
                int(config.get("insight_min_days", 7)),
                int(config.get("insight_min_unique_addresses", 30)),
                float(config.get("insight_min_direction_consistency", 0.75)),
            )
            return base

        current = snapshots[0]
        pnl_rows = (current.get("boards") or {}).get("PNL") or []
        volume_rows = (current.get("boards") or {}).get("VOL") or []
        base["current"] = {
            "slot_at": current.get("slot_at"),
            "captured_at": current.get("captured_at"),
            "pnl_candidate_count": len(pnl_rows),
            "volume_candidate_count": len(volume_rows),
            "volume_winner_count": sum(
                1 for row in volume_rows if float(row.get("pnl") or 0) > 0
            ),
            "volume_loser_count": sum(
                1 for row in volume_rows if float(row.get("pnl") or 0) < 0
            ),
        }
        comparison_seconds = int(config.get("comparison_hours", 24)) * 3600
        previous = self.research_store.load_epoch(
            int(current["slot_epoch"]) - comparison_seconds
        )
        if previous and previous.get("complete") and current.get("complete"):
            base["transition"] = analyze_leaderboard_transition(
                previous, current, top_k
            )
            base["status"] = "ready"
        else:
            base["status"] = "collecting"

        analysis_entries = self.research_store.load_latest_analyses(
            int(config.get("retention_days", 90))
        )
        if analysis_entries:
            latest = analysis_entries[0]
            analysis = copy.deepcopy(latest["analysis"])
            analysis["slot_at"] = latest.get("slot_at")
            analysis["cohort_sizes"] = {
                key: len(value)
                for key, value in (analysis.get("cohorts") or {}).items()
            }
            base["latest_analysis"] = analysis
        analyses = [item["analysis"] for item in reversed(analysis_entries)]
        base["hypotheses"] = build_hypothesis_ledger(
            analyses,
            int(config.get("insight_min_days", 7)),
            int(config.get("insight_min_unique_addresses", 30)),
            float(config.get("insight_min_direction_consistency", 0.75)),
        )
        return base

    def export_research_csv(self, top_k: int = 100) -> bytes:
        summary = self.public_research_summary(top_k)
        transition = summary.get("transition") or {}
        fields = [
            "state",
            "address",
            "user_name",
            "previous_rank",
            "current_rank",
            "current_rank_exact",
            "previous_pnl",
            "current_pnl",
            "previous_volume",
            "current_volume",
            "visible_reason",
        ]
        return _csv_bytes(transition.get("rows") or [], fields)

    def start_scan(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        runtime_config = validate_scan_config(payload, self.base_config)
        with self.lock:
            if self.state["status"] == "scanning":
                return False, "a scan is already running"
            if self.research_state.get("active"):
                return False, "a bounded research capture is running; retry shortly"
            scan_id = uuid.uuid4().hex[:12]
            self.state.update(
                {
                    "status": "scanning",
                    "scan_id": scan_id,
                    "config": runtime_config,
                    "progress": {
                        "stage": "leaderboard",
                        "completed": 0,
                        "total": runtime_config["leaderboard"]["candidate_limit"],
                        "message": "正在读取 CRYPTO 排行榜",
                    },
                    "addresses": [],
                    "filtered_addresses": [],
                    "tail_analysis": {},
                    "live_trades": [],
                    "errors": [],
                }
            )
        worker = threading.Thread(
            target=self._run_scan,
            args=(scan_id, runtime_config),
            name="polymarket-scan-" + scan_id,
            daemon=True,
        )
        worker.start()
        return True, scan_id

    def export_addresses_csv(self) -> bytes:
        snapshot = self.snapshot()
        fields = [
            "rank",
            "address",
            "user_name",
            "verified",
            "leaderboard_pnl",
            "leaderboard_volume",
            "return_efficiency",
            "estimated_pnl_per_activity",
            "tail_analysis_in_scope",
            "tail_60m_trade_count",
            "tail_6h_trade_count",
            "tail_24h_trade_count",
            "tail_60m_share",
            "tail_60m_buy_count",
            "tail_60m_sell_count",
            "tail_60m_high_confidence_count",
            "tail_60m_avg_price",
            "tail_60m_usdc_volume",
            "tail_60m_market_count",
            "trade_count",
            "transaction_count",
            "trades_per_day",
            "median_hours_to_settlement",
            "settlement_coverage",
            "settlement_slug_inferred_count",
            "settlement_market_end_count",
            "activity_truncated",
            "open_position_count",
            "open_position_value",
            "open_position_cash_pnl",
            "chain_status",
            "chain_sample_size",
            "chain_receipt_count",
            "chain_confirmed_count",
            "chain_verification_rate",
            "polymarket_contract_hit_count",
            "polymarket_contract_rate",
            "chain_latest_block",
            "chain_log_count",
            "latest_trade_at",
        ]
        return _csv_bytes(snapshot["filtered_addresses"], fields)

    def export_trades_csv(self) -> bytes:
        snapshot = self.snapshot()
        fields = [
            "timestamp_utc",
            "address",
            "user_name",
            "side",
            "size",
            "usdc_size",
            "price",
            "title",
            "outcome",
            "minutes_to_settlement",
            "condition_id",
            "transaction_hash",
            "onchain_status",
            "onchain_block_number",
            "onchain_polymarket_contract",
        ]
        return _csv_bytes(snapshot["live_trades"], fields)

    def _research_loop(self) -> None:
        while not self.stop_event.is_set():
            config = self.research_config
            if not config.get("enabled"):
                if self.stop_event.wait(30.0):
                    return
                continue
            slot_epoch = research_slot_epoch(
                self.clock(), int(config.get("cadence_hours", 6))
            )
            with self.lock:
                scan_in_progress = self.state.get("status") == "scanning"
                next_retry_epoch = int(
                    self.research_state.get("next_retry_epoch") or 0
                )
            retry_ready = int(self.clock()) >= next_retry_epoch
            if (
                retry_ready
                and not scan_in_progress
                and not self.research_store.exists(slot_epoch)
            ):
                try:
                    self._capture_research_snapshot(slot_epoch)
                except Exception as exc:
                    self._append_research_error("capture", str(exc))
            elif self.research_store.exists(slot_epoch):
                latest = self.research_store.load_epoch(slot_epoch)
                if latest:
                    with self.lock:
                        self.research_state.update(
                            {
                                "status": "ready",
                                "last_capture_at": latest.get("captured_at"),
                                "last_slot_at": latest.get("slot_at"),
                            }
                        )
            if self.stop_event.wait(30.0):
                return

    def _capture_research_snapshot(self, slot_epoch: int) -> None:
        if not self._research_capture_lock.acquire(False):
            return
        config = self.research_config
        with self.lock:
            if self.state.get("status") == "scanning":
                self._research_capture_lock.release()
                return
            self.research_state["active"] = True
            self.research_state["status"] = "capturing"
        try:
            period = str(config.get("time_period", "DAY")).upper()
            limit = int(config.get("candidate_limit", 1000))
            pnl_rows = self.client.leaderboard(period, "PNL", limit)
            volume_rows = self.client.leaderboard(period, "VOL", limit)
            if not pnl_rows or not volume_rows:
                raise RuntimeError("official leaderboard returned an empty board")
            captured_epoch = int(self.clock())
            snapshot = build_research_snapshot(
                slot_epoch,
                captured_epoch,
                period,
                pnl_rows,
                volume_rows,
            )
            previous = self.research_store.load_epoch(
                slot_epoch - int(config.get("comparison_hours", 24)) * 3600
            )
            if previous and previous.get("complete"):
                top_k = int(config.get("default_top_k", 100))
                cohort_limit = int(config.get("cohort_limit", 30))
                cohorts = choose_research_cohorts(
                    previous, snapshot, top_k, cohort_limit
                )
                self._attach_research_rank_checks(
                    snapshot, cohorts.get("dropped") or [], period
                )
                slot_hour = datetime.fromtimestamp(
                    slot_epoch, tz=timezone.utc
                ).hour
                if (
                    config.get("enrichment_enabled", True)
                    and slot_hour == int(config.get("enrichment_hour_utc", 0))
                ):
                    self._attach_research_enrichment(
                        snapshot, cohorts, slot_epoch
                    )
            self.research_store.save(snapshot)
            with self.lock:
                self.research_state.update(
                    {
                        "status": "ready",
                        "last_capture_at": snapshot.get("captured_at"),
                        "last_slot_at": snapshot.get("slot_at"),
                        "next_retry_epoch": None,
                    }
                )
        finally:
            with self.lock:
                self.research_state["active"] = False
            self._research_capture_lock.release()

    def _attach_research_rank_checks(
        self,
        snapshot: Dict[str, Any],
        addresses: Iterable[str],
        period: str,
    ) -> None:
        checks: Dict[str, Dict[str, Any]] = {}
        for address in addresses:
            try:
                row = self.client.leaderboard_user(period, "PNL", address)
                normalized = normalize_leaderboard_rows([row] if row else [])
                if normalized:
                    checks[address] = normalized[0]
            except Exception as exc:
                snapshot["errors"].append(
                    {"scope": "rank:" + address, "message": str(exc)}
                )
        snapshot["rank_checks"] = checks

    def _attach_research_enrichment(
        self,
        snapshot: Dict[str, Any],
        cohorts: Dict[str, List[str]],
        slot_epoch: int,
    ) -> None:
        config = self.research_config
        addresses = list(
            dict.fromkeys(
                address
                for values in cohorts.values()
                for address in values
            )
        )
        activities: Dict[str, Tuple[List[Dict[str, Any]], bool]] = {}
        errors: List[Dict[str, str]] = []
        workers = max(1, int(config.get("max_workers", 3)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self.client.recent_activity,
                    address,
                    slot_epoch - 86400,
                    int(config.get("activity_page_size", 500)),
                    int(config.get("max_activity_pages", 2)),
                    slot_epoch,
                ): address
                for address in addresses
            }
            for future in as_completed(futures):
                address = futures[future]
                try:
                    activities[address] = future.result()
                except Exception as exc:
                    activities[address] = ([], False)
                    errors.append({"scope": "activity:" + address, "message": str(exc)})

        fingerprints = {}
        for address in addresses:
            rows, truncated = activities.get(address, ([], False))
            fingerprints[address] = build_strategy_fingerprint(
                rows,
                slot_epoch - 86400,
                slot_epoch,
                truncated,
            )

        if config.get("receipt_verification_enabled", True) and fingerprints:
            try:
                self.chain_client.verify_chain()
                cap = max(1, int(config.get("receipts_per_address", 2)))
                hashes_by_address = {
                    address: (item.get("sample_transaction_hashes") or [])[:cap]
                    for address, item in fingerprints.items()
                }
                all_hashes = [
                    transaction_hash
                    for values in hashes_by_address.values()
                    for transaction_hash in values
                ]
                self._ensure_receipts(
                    "research-" + research_slot_id(slot_epoch),
                    all_hashes,
                    self.base_config,
                )
                contracts = self.base_config["chain"]["polymarket_contracts"]
                for address, hashes in hashes_by_address.items():
                    fingerprints[address].update(
                        summarize_chain_receipts(
                            hashes,
                            self.receipt_cache,
                            contracts,
                        )
                    )
            except Exception as exc:
                errors.append({"scope": "research-chain", "message": str(exc)})

        comparisons = compare_enriched_cohorts(cohorts, fingerprints)
        snapshot["enrichment"] = {
            "window_start_at": utc_iso(slot_epoch - 86400),
            "window_end_at": utc_iso(slot_epoch),
            "address_count": len(addresses),
            "fingerprints": fingerprints,
            "errors": errors[-100:],
        }
        successful_addresses = sum(
            1 for address in addresses
            if not any(
                error["scope"] == "activity:" + address for error in errors
            )
        )
        truncated_addresses = sum(
            1 for item in fingerprints.values()
            if item.get("activity_truncated")
        )
        requested_addresses = len(addresses)
        eligible_for_insight = bool(
            requested_addresses
            and successful_addresses / requested_addresses >= 0.8
            and truncated_addresses / requested_addresses <= 0.2
        )
        snapshot["analysis"] = {
            "formal": True,
            "eligible_for_insight": eligible_for_insight,
            "cohorts": cohorts,
            "comparisons": comparisons,
            "data_quality": {
                "requested_addresses": requested_addresses,
                "successful_addresses": successful_addresses,
                "truncated_addresses": truncated_addresses,
                "receipt_verified_addresses": sum(
                    1 for item in fingerprints.values()
                    if item.get("chain_status") == "verified"
                ),
            },
        }
        snapshot["errors"].extend(errors[-100:])

    def _append_research_error(self, scope: str, message: str) -> None:
        with self.lock:
            self.research_state["status"] = "error"
            self.research_state["errors"].append(
                {"scope": scope, "message": message, "at": utc_iso(self.clock())}
            )
            self.research_state["next_retry_epoch"] = int(self.clock()) + 300
            self.research_state["errors"] = self.research_state["errors"][-20:]

    def _run_scan(self, scan_id: str, config: Dict[str, Any]) -> None:
        try:
            leaderboard_cfg = config["leaderboard"]
            leaderboard = self.client.leaderboard(
                leaderboard_cfg["time_period"],
                leaderboard_cfg["order_by"],
                leaderboard_cfg["candidate_limit"],
            )
            if config["chain"].get("enabled", True):
                self._set_progress(
                    scan_id,
                    "chain_check",
                    0,
                    1,
                    "正在确认 Polygon 主网链 ID",
                )
                self.chain_client.verify_chain()
                self._set_progress(
                    scan_id,
                    "chain_check",
                    1,
                    1,
                    "Polygon 主网链 ID 137 已确认",
                )
            now_timestamp = int(self.clock())
            cutoff = now_timestamp - int(config["filter"]["lookback_hours"] * 3600)
            self._set_progress(
                scan_id, "activity", 0, len(leaderboard), "正在读取候选地址交易活动"
            )

            activities: Dict[str, Tuple[List[Dict[str, Any]], bool]] = {}
            activity_errors: Dict[str, str] = {}
            sampling = config["sampling"]
            with ThreadPoolExecutor(max_workers=sampling["max_workers"]) as executor:
                futures = {
                    executor.submit(
                        self.client.recent_activity,
                        str(entry.get("proxyWallet") or "").lower(),
                        cutoff,
                        sampling["activity_page_size"],
                        sampling["max_activity_pages"],
                    ): str(entry.get("proxyWallet") or "").lower()
                    for entry in leaderboard
                    if entry.get("proxyWallet")
                }
                completed = 0
                for future in as_completed(futures):
                    address = futures[future]
                    try:
                        activities[address] = future.result()
                    except Exception as exc:  # one address must not abort the scan
                        activities[address] = ([], False)
                        activity_errors[address] = str(exc)
                    completed += 1
                    self._set_progress(
                        scan_id,
                        "activity",
                        completed,
                        len(futures),
                        "已读取 %d/%d 个候选地址" % (completed, len(futures)),
                    )

            # Stage one is intentionally cheap: use count and frequency before
            # performing market metadata or on-chain lookups.
            preliminary = []
            preliminary_by_address: Dict[str, Dict[str, Any]] = {}
            for entry in leaderboard:
                address = str(entry.get("proxyWallet") or "").lower()
                rows, truncated = activities.get(address, ([], False))
                metrics, _normalized = build_address_metrics(
                    entry,
                    rows,
                    self.market_cache,
                    config["filter"]["lookback_hours"],
                    now_timestamp,
                    truncated,
                )
                metrics = apply_filter(
                    metrics, config["filter"], check_settlement=False
                )
                preliminary.append(metrics)
                preliminary_by_address[address] = metrics
            sort_field = {
                "FREQUENCY": "trades_per_day",
                "RETURN": "return_efficiency",
                "AVG_PNL": "estimated_pnl_per_activity",
                "PNL": "leaderboard_pnl",
            }[config["filter"].get("result_sort", "FREQUENCY")]
            preliminary_filtered = [
                item for item in preliminary if item["passes"]
            ]
            preliminary_filtered.sort(
                key=lambda item: _descending_metric_key(item, sort_field),
                reverse=True,
            )
            tail_limit = config["sampling"]["tail_analysis_addresses"]
            tail_sample_addresses = {
                item["address"] for item in preliminary_filtered[:tail_limit]
            }
            entry_by_address = {
                str(entry.get("proxyWallet") or "").lower(): entry
                for entry in leaderboard
            }
            tail_sample_entries = [
                entry_by_address[address]
                for address in (
                    item["address"] for item in preliminary_filtered[:tail_limit]
                )
                if address in entry_by_address
            ]
            market_ids = self._market_sample_ids(
                tail_sample_entries, activities, config
            )
            # The expanded scan can contain up to a million activity rows.
            # Once the detailed sample is known, discard raw rows outside it;
            # their already-computed metrics are sufficient for the full
            # filter result. Chain mode retains all rows because it is an
            # explicit, opt-in per-transaction verification path.
            if not config["chain"].get("enabled", True):
                activities = {
                    address: value
                    for address, value in activities.items()
                    if address in tail_sample_addresses
                }
            missing_ids = [cid for cid in market_ids if cid not in self.market_cache]
            self._set_progress(
                scan_id,
                "markets",
                0,
                len(missing_ids),
                "正在补齐市场结算时间",
            )
            market_errors = self._fetch_markets(scan_id, missing_ids, sampling["max_workers"])
            self._save_market_cache()

            addresses = []
            normalized_by_address: Dict[str, List[Dict[str, Any]]] = {}
            for entry in leaderboard:
                address = str(entry.get("proxyWallet") or "").lower()
                if address in activities:
                    rows, truncated = activities[address]
                    metrics, normalized = build_address_metrics(
                        entry,
                        rows,
                        self.market_cache,
                        config["filter"]["lookback_hours"],
                        now_timestamp,
                        truncated,
                    )
                    metrics = apply_filter(metrics, config["filter"])
                else:
                    metrics = apply_filter(
                        preliminary_by_address[address],
                        config["filter"],
                        check_settlement=False,
                    )
                    normalized = []
                metrics["tail_analysis_in_scope"] = address in tail_sample_addresses
                if address in activity_errors:
                    metrics["error"] = activity_errors[address]
                if normalized:
                    normalized_by_address[address] = normalized
                addresses.append(metrics)
            addresses.sort(key=lambda item: item.get("rank") or 10 ** 9)
            offchain_filtered = [item for item in addresses if item["passes"]]

            chain_errors: List[Dict[str, str]] = []
            if config["chain"].get("enabled", True) and offchain_filtered:
                samples = self._chain_samples(
                    offchain_filtered, normalized_by_address, config
                )
                all_hashes = [
                    transaction_hash
                    for hashes in samples.values()
                    for transaction_hash in hashes
                ]
                self._ensure_receipts(scan_id, all_hashes, config)
                contracts = config["chain"]["polymarket_contracts"]
                chain_map = {}
                for item in offchain_filtered:
                    updated = dict(item)
                    updated.update(
                        summarize_chain_receipts(
                            samples.get(item["address"], []),
                            self.receipt_cache,
                            contracts,
                        )
                    )
                    updated = apply_chain_filter(updated, config["chain"])
                    chain_map[item["address"]] = updated
                addresses = [
                    chain_map.get(item["address"], item) for item in addresses
                ]
            filtered = [item for item in addresses if item["passes"]]
            filtered.sort(
                key=lambda item: _descending_metric_key(item, sort_field),
                reverse=True,
            )
            tracked_limit = config["live"]["max_tracked_addresses"]
            for index, item in enumerate(filtered):
                item["live_tracked"] = index < tracked_limit

            detail_limit = config["sampling"]["detail_addresses"]
            detailed_addresses = filtered[:detail_limit]
            self._set_progress(
                scan_id,
                "positions",
                0,
                len(detailed_addresses),
                "正在读取筛选地址的当前持仓",
            )
            position_errors = self._attach_positions(
                scan_id, detailed_addresses, sampling["max_workers"]
            )
            filtered_map = {item["address"]: item for item in filtered}
            addresses = [filtered_map.get(item["address"], item) for item in addresses]

            live_seed: List[Dict[str, Any]] = []
            names = {item["address"]: item.get("user_name") or "" for item in filtered}
            contracts = config["chain"]["polymarket_contracts"]
            tracked_addresses = {
                item["address"] for item in filtered if item.get("live_tracked")
            }
            for address in tracked_addresses:
                for trade in normalized_by_address.get(address, []):
                    transaction_hash = str(
                        trade.get("transaction_hash") or ""
                    ).lower()
                    row = enrich_trade_onchain(
                        trade,
                        self.receipt_cache.get(transaction_hash),
                        contracts,
                    )
                    row["user_name"] = names.get(address, "")
                    live_seed.append(row)
            live_seed.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
            live_seed = live_seed[: config["live"]["max_events"]]

            errors = []
            errors.extend(
                {"scope": address, "message": message}
                for address, message in activity_errors.items()
            )
            errors.extend(market_errors)
            errors.extend(chain_errors)
            errors.extend(position_errors)
            finished = int(self.clock())
            with self.lock:
                if self.state.get("scan_id") != scan_id:
                    return
                self.state.update(
                    {
                        "status": "ready",
                        "addresses": addresses,
                        "filtered_addresses": filtered,
                        "tail_analysis": summarize_tail_analysis(
                            filtered,
                            config["sampling"]["tail_analysis_addresses"],
                            config["sampling"]["settlement_markets_per_address"],
                        ),
                        "live_trades": live_seed,
                        "last_scan_at": utc_iso(finished),
                        "last_live_at": utc_iso(finished),
                        "last_live_epoch": finished,
                        "last_positions_epoch": finished,
                        "errors": errors[-100:],
                        "progress": {
                            "stage": "ready",
                            "completed": len(filtered),
                            "total": len(addresses),
                            "message": "筛选完成：%d/%d 个地址通过" % (
                                len(filtered),
                                len(addresses),
                            ),
                        },
                    }
                )
            self._save_snapshot()
        except Exception as exc:
            with self.lock:
                if self.state.get("scan_id") == scan_id:
                    self.state["status"] = "error"
                    self.state["progress"] = {
                        "stage": "error",
                        "completed": 0,
                        "total": 0,
                        "message": str(exc),
                    }
                    self.state["errors"] = [
                        {"scope": "scan", "message": str(exc)}
                    ]

    def _market_sample_ids(
        self,
        leaderboard: Iterable[Dict[str, Any]],
        activities: Dict[str, Tuple[List[Dict[str, Any]], bool]],
        config: Dict[str, Any],
    ) -> List[str]:
        cap = config["sampling"]["settlement_markets_per_address"]
        ordered: Dict[str, None] = {}
        for entry in leaderboard:
            address = str(entry.get("proxyWallet") or "").lower()
            rows = sorted(
                activities.get(address, ([], False))[0],
                key=lambda row: int(row.get("timestamp") or 0),
                reverse=True,
            )
            seen = set()
            for row in rows:
                if infer_interval_end_timestamp(row) is not None:
                    continue
                condition_id = str(row.get("conditionId") or "").lower()
                if not condition_id or condition_id in seen:
                    continue
                ordered.setdefault(condition_id, None)
                seen.add(condition_id)
                if len(seen) >= cap:
                    break
        return list(ordered.keys())

    def _fetch_markets(
        self, scan_id: str, condition_ids: List[str], max_workers: int
    ) -> List[Dict[str, str]]:
        errors: List[Dict[str, str]] = []
        if not condition_ids:
            return errors
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.client.market, condition_id): condition_id
                for condition_id in condition_ids
            }
            completed = 0
            for future in as_completed(futures):
                condition_id = futures[future]
                try:
                    market = future.result()
                    self.market_cache[condition_id] = market or {
                        "condition_id": condition_id,
                        "missing": True,
                    }
                except Exception as exc:
                    errors.append({"scope": condition_id, "message": str(exc)})
                completed += 1
                self._set_progress(
                    scan_id,
                    "markets",
                    completed,
                    len(futures),
                    "已补齐 %d/%d 个市场" % (completed, len(futures)),
                )
        return errors

    def _chain_samples(
        self,
        addresses: Iterable[Dict[str, Any]],
        normalized_by_address: Dict[str, List[Dict[str, Any]]],
        config: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        cap = config["chain"]["receipts_per_address"]
        samples: Dict[str, List[str]] = {}
        for item in addresses:
            address = item["address"]
            hashes = []
            seen = set()
            for trade in normalized_by_address.get(address, []):
                transaction_hash = str(
                    trade.get("transaction_hash") or ""
                ).lower()
                if (
                    len(transaction_hash) != 66
                    or not transaction_hash.startswith("0x")
                    or transaction_hash in seen
                ):
                    continue
                seen.add(transaction_hash)
                hashes.append(transaction_hash)
                if len(hashes) >= cap:
                    break
            samples[address] = hashes
        return samples

    def _ensure_receipts(
        self, scan_id: str, transaction_hashes: Iterable[str], config: Dict[str, Any]
    ) -> None:
        now_timestamp = int(self.clock())
        ordered = list(dict.fromkeys(str(value).lower() for value in transaction_hashes))
        missing = []
        for transaction_hash in ordered:
            cached = self.receipt_cache.get(transaction_hash)
            if cached is None:
                missing.append(transaction_hash)
            elif cached.get("missing") and now_timestamp - int(
                cached.get("checked_at") or 0
            ) >= 60:
                missing.append(transaction_hash)
        self._set_progress(
            scan_id,
            "chain_receipts",
            0,
            len(missing),
            "正在读取 Polygon 链上交易回执",
        )
        batch_size = config["chain"]["batch_size"]
        completed = 0
        for start in range(0, len(missing), batch_size):
            chunk = missing[start : start + batch_size]
            receipts = self.chain_client.transaction_receipts(chunk, batch_size)
            for transaction_hash in chunk:
                receipt = receipts.get(transaction_hash)
                if receipt is None:
                    self.receipt_cache[transaction_hash] = {
                        "transaction_hash": transaction_hash,
                        "missing": True,
                        "checked_at": now_timestamp,
                    }
                else:
                    stored = dict(receipt)
                    stored["checked_at"] = now_timestamp
                    self.receipt_cache[transaction_hash] = stored
            completed += len(chunk)
            self._set_progress(
                scan_id,
                "chain_receipts",
                completed,
                len(missing),
                "已验证 %d/%d 笔链上交易" % (completed, len(missing)),
            )
        if missing:
            self._save_receipt_cache()

    def _attach_positions(
        self,
        scan_id: str,
        addresses: List[Dict[str, Any]],
        max_workers: int,
    ) -> List[Dict[str, str]]:
        errors: List[Dict[str, str]] = []
        if not addresses:
            return errors
        by_address = {item["address"]: item for item in addresses}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.client.positions, address): address
                for address in by_address
            }
            completed = 0
            for future in as_completed(futures):
                address = futures[future]
                try:
                    by_address[address].update(summarize_positions(future.result()))
                except Exception as exc:
                    errors.append({"scope": address, "message": str(exc)})
                completed += 1
                self._set_progress(
                    scan_id,
                    "positions",
                    completed,
                    len(futures),
                    "已读取 %d/%d 个地址持仓" % (completed, len(futures)),
                )
        return errors

    def _live_loop(self) -> None:
        while not self.stop_event.wait(1.0):
            with self.lock:
                if (
                    self.state["status"] != "ready"
                    or self.research_state.get("active")
                ):
                    continue
                config = copy.deepcopy(self.state["config"])
                scan_id = self.state["scan_id"]
                addresses = [
                    item["address"] for item in self.state["filtered_addresses"]
                    if item.get("live_tracked")
                ]
            if not addresses:
                continue
            now_monotonic = time.monotonic()
            if now_monotonic - self._last_live_poll_monotonic < config["live"]["poll_seconds"]:
                continue
            self._last_live_poll_monotonic = now_monotonic
            try:
                self._poll_live(scan_id, addresses, config)
            except Exception as exc:
                self._append_error("live", str(exc))

    def _poll_live(
        self, scan_id: str, addresses: List[str], config: Dict[str, Any]
    ) -> None:
        now_timestamp = int(self.clock())
        with self.lock:
            start_timestamp = int(self.state.get("last_live_epoch") or now_timestamp) - 5
            names = {
                item["address"]: item.get("user_name") or ""
                for item in self.state["filtered_addresses"]
            }
        raw_rows: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        max_workers = config["sampling"]["max_workers"]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.client.live_activity,
                    address,
                    start_timestamp,
                    config["live"]["activity_limit"],
                ): address
                for address in addresses
            }
            for future in as_completed(futures):
                address = futures[future]
                try:
                    raw_rows.extend(future.result())
                except Exception as exc:
                    errors.append({"scope": address, "message": str(exc)})

        unseen = []
        for row in raw_rows:
            condition_id = str(row.get("conditionId") or "").lower()
            if condition_id and condition_id not in self.market_cache:
                unseen.append(condition_id)
        unique_unseen = list(dict.fromkeys(unseen))[:50]
        errors.extend(self._fetch_markets("live-" + scan_id, unique_unseen, max_workers))
        if unique_unseen:
            self._save_market_cache()

        live_hashes = list(
            dict.fromkeys(
                str(row.get("transactionHash") or "").lower()
                for row in raw_rows
                if row.get("transactionHash")
            )
        )[: config["chain"]["live_receipts_per_poll"]]
        if config["chain"].get("enabled", True) and live_hashes:
            try:
                self._ensure_receipts("live-" + scan_id, live_hashes, config)
            except Exception as exc:
                errors.append({"scope": "live-chain", "message": str(exc)})

        normalized = []
        contracts = config["chain"]["polymarket_contracts"]
        for raw in raw_rows:
            condition_id = str(raw.get("conditionId") or "").lower()
            row = normalize_trade(raw, self.market_cache.get(condition_id))
            transaction_hash = str(row.get("transaction_hash") or "").lower()
            row = enrich_trade_onchain(
                row, self.receipt_cache.get(transaction_hash), contracts
            )
            row["user_name"] = names.get(row["address"], "")
            normalized.append(row)

        with self.lock:
            if self.state.get("scan_id") != scan_id or self.state["status"] != "ready":
                return
            merged = {row["key"]: row for row in self.state["live_trades"]}
            for row in normalized:
                merged[row["key"]] = row
            trades = sorted(
                merged.values(), key=lambda item: item.get("timestamp") or 0, reverse=True
            )[: config["live"]["max_events"]]
            self.state["live_trades"] = trades
            self.state["last_live_at"] = utc_iso(now_timestamp)
            self.state["last_live_epoch"] = now_timestamp
            self.state["errors"].extend(errors)
            self.state["errors"] = self.state["errors"][-100:]

        last_positions = self.snapshot().get("last_positions_epoch") or 0
        if now_timestamp - int(last_positions) >= config["live"]["positions_refresh_seconds"]:
            self._refresh_positions(scan_id, addresses, max_workers, now_timestamp)
        self._save_snapshot()

    def _refresh_positions(
        self, scan_id: str, addresses: List[str], max_workers: int, now_timestamp: int
    ) -> None:
        updates: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.client.positions, address): address
                for address in addresses
            }
            for future in as_completed(futures):
                address = futures[future]
                try:
                    updates[address] = summarize_positions(future.result())
                except Exception as exc:
                    self._append_error(address, str(exc))
        with self.lock:
            if self.state.get("scan_id") != scan_id:
                return
            for collection in ("addresses", "filtered_addresses"):
                for item in self.state[collection]:
                    if item["address"] in updates:
                        item.update(updates[item["address"]])
            self.state["last_positions_epoch"] = now_timestamp

    def _set_progress(
        self,
        scan_id: str,
        stage: str,
        completed: int,
        total: int,
        message: str,
    ) -> None:
        with self.lock:
            if self.state.get("scan_id") == scan_id:
                self.state["progress"] = {
                    "stage": stage,
                    "completed": completed,
                    "total": total,
                    "message": message,
                }

    def _append_error(self, scope: str, message: str) -> None:
        with self.lock:
            self.state["errors"].append({"scope": scope, "message": message})
            self.state["errors"] = self.state["errors"][-100:]

    def _save_market_cache(self) -> None:
        self._save_json(self.data_dir / "market_cache.json", self.market_cache)

    def _save_receipt_cache(self) -> None:
        self._save_json(self.data_dir / "receipt_cache.json", self.receipt_cache)

    def _save_snapshot(self) -> None:
        self._save_json(self.data_dir / "snapshot.json", self.snapshot())

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def _save_json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError("expected an integer")
    if result < minimum or result > maximum:
        raise ValueError("integer must be between %d and %d" % (minimum, maximum))
    return result


def _descending_metric_key(item: Dict[str, Any], field: str) -> Tuple[bool, float]:
    value = item.get(field)
    return value is not None, float(value) if value is not None else float("-inf")


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("expected a number")
    if result < minimum or result > maximum:
        raise ValueError("number must be between %s and %s" % (minimum, maximum))
    return result


def _optional_bounded_float(
    value: Any, minimum: float, maximum: float
) -> Optional[float]:
    if value is None or value == "":
        return None
    return _bounded_float(value, minimum, maximum)


def _csv_bytes(rows: Iterable[Dict[str, Any]], fields: List[str]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return ("\ufeff" + output.getvalue()).encode("utf-8")
