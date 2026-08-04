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
    apply_filter,
    build_address_metrics,
    normalize_trade,
    summarize_positions,
    trade_key,
    utc_iso,
)
from polymarket_client import PolymarketClient


def validate_scan_config(payload: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """Build a bounded runtime config from the browser payload."""
    payload = payload or {}
    result = copy.deepcopy(base)
    leaderboard = result["leaderboard"]
    filters = result["filter"]
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
        payload.get("candidate_limit", leaderboard["candidate_limit"]), 1, 50
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
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.base_dir = base_dir
        self.data_dir = base_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.base_config = copy.deepcopy(config)
        self.client = client or PolymarketClient()
        self.clock = clock
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.market_cache: Dict[str, Dict[str, Any]] = self._load_json(
            self.data_dir / "market_cache.json", {}
        )
        self.state: Dict[str, Any] = {
            "status": "idle",
            "scan_id": None,
            "category": "CRYPTO",
            "config": copy.deepcopy(config),
            "progress": {"stage": "idle", "completed": 0, "total": 0, "message": ""},
            "addresses": [],
            "filtered_addresses": [],
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
        self._live_thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        self._live_thread.join(timeout=3)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.state)

    def public_snapshot(self, live_limit: int = 200) -> Dict[str, Any]:
        """Return the compact browser view while keeping full data for exports."""
        snapshot = self.snapshot()
        snapshot["live_trades"] = snapshot["live_trades"][:live_limit]
        return snapshot

    def start_scan(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        runtime_config = validate_scan_config(payload, self.base_config)
        with self.lock:
            if self.state["status"] == "scanning":
                return False, "a scan is already running"
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
            "trade_count",
            "transaction_count",
            "trades_per_day",
            "median_hours_to_settlement",
            "settlement_coverage",
            "activity_truncated",
            "open_position_count",
            "open_position_value",
            "open_position_cash_pnl",
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
        ]
        return _csv_bytes(snapshot["live_trades"], fields)

    def _run_scan(self, scan_id: str, config: Dict[str, Any]) -> None:
        try:
            leaderboard_cfg = config["leaderboard"]
            leaderboard = self.client.leaderboard(
                leaderboard_cfg["time_period"],
                leaderboard_cfg["order_by"],
                leaderboard_cfg["candidate_limit"],
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

            market_ids = self._market_sample_ids(leaderboard, activities, config)
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
                rows, truncated = activities.get(address, ([], False))
                metrics, normalized = build_address_metrics(
                    entry,
                    rows,
                    self.market_cache,
                    config["filter"]["lookback_hours"],
                    now_timestamp,
                    truncated,
                )
                metrics = apply_filter(metrics, config["filter"])
                if address in activity_errors:
                    metrics["error"] = activity_errors[address]
                normalized_by_address[address] = normalized
                addresses.append(metrics)
            addresses.sort(key=lambda item: item.get("rank") or 10 ** 9)
            filtered = [item for item in addresses if item["passes"]]

            self._set_progress(
                scan_id,
                "positions",
                0,
                len(filtered),
                "正在读取筛选地址的当前持仓",
            )
            position_errors = self._attach_positions(
                scan_id, filtered, sampling["max_workers"]
            )
            filtered_map = {item["address"]: item for item in filtered}
            addresses = [filtered_map.get(item["address"], item) for item in addresses]

            live_seed: List[Dict[str, Any]] = []
            names = {item["address"]: item.get("user_name") or "" for item in filtered}
            for address in filtered_map:
                for trade in normalized_by_address.get(address, []):
                    row = dict(trade)
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
                if self.state["status"] != "ready":
                    continue
                config = copy.deepcopy(self.state["config"])
                scan_id = self.state["scan_id"]
                addresses = [
                    item["address"] for item in self.state["filtered_addresses"]
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

        normalized = []
        for raw in raw_rows:
            condition_id = str(raw.get("conditionId") or "").lower()
            row = normalize_trade(raw, self.market_cache.get(condition_id))
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
