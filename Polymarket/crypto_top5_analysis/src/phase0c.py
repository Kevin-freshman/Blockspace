#!/usr/bin/env python3
"""Restricted, auditable Phase 0C Polymarket behavior collector."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import gzip
import hashlib
import http.client
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports" / "phase0c"
REQUEST_LOG = REPORT_ROOT / "source_requests.jsonl"
RAW_ROOT = ROOT / "data" / "raw" / "phase0c"
NORMALIZED_ROOT = ROOT / "data" / "normalized" / "phase0c_v1"
TARGETS_PATH = ROOT / "config" / "targets.yaml"
COLLECTOR_VERSION = "phase0c-2-bounded"
ADDRESS_RE = re.compile(r"^0x[a-f0-9]{40}$")
CONDITION_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
ALLOWED_HOSTS = {"data-api.polymarket.com", "gamma-api.polymarket.com"}
ENDPOINTS = {
    "trades": ("data-api.polymarket.com", "/trades"),
    "activity": ("data-api.polymarket.com", "/activity"),
    "positions": ("data-api.polymarket.com", "/positions"),
    "closed_positions": ("data-api.polymarket.com", "/closed-positions"),
    "markets": ("gamma-api.polymarket.com", "/markets"),
}
PAGE_LIMITS = {"trades": 10000, "activity": 500, "positions": 500, "closed_positions": 50}
OFFSET_CAPS = {"trades": 10000, "activity": 5000, "positions": 10000, "closed_positions": 100000}
REQUIRED = {
    "trades": {"proxyWallet", "conditionId", "size", "price", "timestamp", "side"},
    "activity": {"proxyWallet", "timestamp", "type"},
    "positions": {"proxyWallet", "conditionId"},
    "closed_positions": {"proxyWallet", "conditionId"},
    "markets": {"conditionId"},
}
last_request_monotonic = 0.0
_request_index: RequestIndex | None = None
_run_context: RunContext | None = None


class Phase0CError(RuntimeError):
    pass


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decode_rows(body: bytes) -> list[dict[str, Any]]:
    value = json.loads(body.decode("utf-8"), parse_float=Decimal)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise Phase0CError("top-level JSON must be an array of objects")
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def json_line(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=_json_default,
    )


class RequestIndex:
    """One startup scan; subsequent lookups never reread the append-only log."""

    def __init__(self, path: Path):
        self.path = path
        self.by_key: dict[str, dict[str, Any]] = {}
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if item.get("validation_status") == "passed":
                        self.by_key[item["request_key"]] = item

    def success(self, key: str) -> dict[str, Any] | None:
        return self.by_key.get(key)

    def add(self, item: dict[str, Any]) -> None:
        if item.get("validation_status") == "passed":
            self.by_key[item["request_key"]] = item


@dataclass(frozen=True)
class RunContext:
    run_id: str
    snapshot_cutoff_utc: str
    snapshot_cutoff_epoch: int

    @classmethod
    def create(cls, cutoff_epoch: int | None = None) -> "RunContext":
        epoch = int(utc_now().timestamp()) if cutoff_epoch is None else int(cutoff_epoch)
        cutoff = datetime.fromtimestamp(epoch, timezone.utc)
        return cls(
            run_id=f"phase0c-mvp-{cutoff.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
            snapshot_cutoff_utc=iso_utc(cutoff),
            snapshot_cutoff_epoch=epoch,
        )


def multiset_merge(pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One-to-one overlap removal: retain maximum multiplicity seen in any page."""
    maxima: Counter[str] = Counter()
    examples: dict[str, dict[str, Any]] = {}
    for page in pages:
        counts: Counter[str] = Counter()
        for row in page:
            key = fingerprint(row)
            counts[key] += 1
            examples.setdefault(key, row)
        maxima |= counts
    result: list[dict[str, Any]] = []
    for key in sorted(maxima):
        result.extend([examples[key]] * maxima[key])
    return result


def normalized_window_ids(windows: list[dict[str, Any]]) -> set[str]:
    children: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        parent = window.get("parent_window_id")
        if parent:
            children.setdefault(parent, []).append(window)
    result = set()
    for window in windows:
        complete_children = children.get(window["window_id"], [])
        if window.get("saturated") and complete_children and all(
            child.get("complete") for child in complete_children
        ):
            continue
        if window.get("complete"):
            result.add(window["window_id"])
    return result


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def partition_path(root: Path, dataset: str, seed_username: str, timestamp: int | None) -> Path:
    if timestamp:
        dt = datetime.fromtimestamp(timestamp, timezone.utc)
        year, month = f"{dt.year:04d}", f"{dt.month:02d}"
    else:
        year, month = "snapshot", "snapshot"
    return (
        root / f"dataset={dataset}" / f"seed_username={_slug(seed_username)}"
        / f"year={year}" / f"month={month}" / "records.jsonl.gz"
    )


def market_query_batches(condition_ids: list[str], batch_size: int = 50) -> list[dict[str, Any]]:
    result = []
    values = sorted(set(condition_ids))
    for start in range(0, len(values), batch_size):
        batch = values[start:start + batch_size]
        for closed in (False, True):
            result.append({"condition_ids": batch, "closed": closed})
    return result


def coverage_state(record_count: int, cap: int, saturated: bool) -> dict[str, Any]:
    cap_reached = record_count >= cap
    return {
        "coverage_status": "partial" if cap_reached or saturated else "complete",
        "record_cap_reached": cap_reached,
        "saturated": saturated,
        "full_history": False,
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_targets() -> list[dict[str, Any]]:
    data = yaml.safe_load(TARGETS_PATH.read_text(encoding="utf-8"))
    targets = data["targets"]
    addresses = [item["proxy_wallet"] for item in targets]
    if len(targets) != 5 or len(set(addresses)) != 5:
        raise Phase0CError("targets must contain exactly five unique addresses")
    if not all(ADDRESS_RE.fullmatch(value) for value in addresses):
        raise Phase0CError("target address is not lowercase 0x + 40 hex")
    return targets


def canonical_params(params: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for key in sorted(params):
        values = params[key] if isinstance(params[key], (list, tuple)) else [params[key]]
        for value in sorted(values, key=str):
            result.append({
                "name": key,
                "value": str(value).lower() if isinstance(value, bool) else str(value),
            })
    return result


def request_url(endpoint: str, params: dict[str, Any]) -> str:
    host, path = ENDPOINTS[endpoint]
    return f"https://{host}{path}?{urlencode([(x['name'], x['value']) for x in canonical_params(params)])}"


def request_key(endpoint: str, params: dict[str, Any], purpose: str) -> str:
    material = json.dumps(
        {"endpoint": endpoint, "params": canonical_params(params), "purpose": purpose},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def existing_successes() -> dict[str, dict[str, Any]]:
    global _request_index
    if _request_index is None:
        _request_index = RequestIndex(REQUEST_LOG)
    return _request_index.by_key


def append_log(item: dict[str, Any]) -> None:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    with REQUEST_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    if _request_index is not None:
        _request_index.add(item)


def save_raw(endpoint: str, requested_at: datetime, body: bytes) -> tuple[str, str, str]:
    response_sha = hashlib.sha256(body).hexdigest()
    stamp = requested_at.strftime("%Y%m%dT%H%M%S.%fZ")
    request_id = f"{stamp}_{endpoint}_{response_sha[:16]}"
    relative = Path("data/raw/phase0c") / requested_at.strftime("%Y/%m/%d") / f"{request_id}.json.gz"
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(body, compresslevel=9, mtime=0)
    with path.open("xb") as handle:
        handle.write(compressed)
    return request_id, relative.as_posix(), hashlib.sha256(compressed).hexdigest()


def validate_records(endpoint: str, body: bytes, target: str | None) -> list[dict[str, Any]]:
    try:
        value = decode_rows(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase0CError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, list):
        raise Phase0CError(f"{endpoint}: top-level JSON must be an array")
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise Phase0CError(f"{endpoint}[{index}]: record must be an object")
        missing = REQUIRED[endpoint] - row.keys()
        if missing:
            raise Phase0CError(f"{endpoint}[{index}]: missing fields {sorted(missing)}")
        if endpoint != "markets" and target is not None:
            wallet = row.get("proxyWallet")
            if not isinstance(wallet, str) or wallet.lower() != target:
                raise Phase0CError(f"{endpoint}[{index}]: proxyWallet mismatch")
        condition = row.get("conditionId")
        if (
            condition not in (None, "")
            and (not isinstance(condition, str) or not CONDITION_RE.fullmatch(condition))
        ):
            raise Phase0CError(f"{endpoint}[{index}]: invalid conditionId")
        if endpoint in {"trades", "activity"}:
            timestamp = row.get("timestamp")
            if not isinstance(timestamp, int) or timestamp < 1 or timestamp > 20_000_000_000:
                raise Phase0CError(f"{endpoint}[{index}]: timestamp is not epoch seconds")
    return value


def rate_limit() -> None:
    global last_request_monotonic
    gap = time.monotonic() - last_request_monotonic
    if gap < 0.2:
        time.sleep(0.2 - gap)
    last_request_monotonic = time.monotonic()


def get(endpoint: str, params: dict[str, Any], purpose: str, target: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = request_key(endpoint, params, purpose)
    cached = existing_successes().get(key)
    if cached:
        raw_bytes = (ROOT / cached["raw_path"]).read_bytes()
        if sha256(raw_bytes) != cached["gzip_sha256"]:
            raise Phase0CError(f"cached gzip hash mismatch: {cached['raw_path']}")
        body = gzip.decompress(raw_bytes)
        if sha256(body) != cached["response_sha256"]:
            raise Phase0CError(f"cached response hash mismatch: {cached['raw_path']}")
        return validate_records(endpoint, body, target), cached
    host, path = ENDPOINTS[endpoint]
    if host not in ALLOWED_HOSTS:
        raise Phase0CError("host not allowed")
    url = request_url(endpoint, params)
    query = urlsplit(url).query
    retries = 0
    while True:
        rate_limit()
        requested_at = utc_now()
        connection = http.client.HTTPSConnection(host, timeout=45)
        try:
            connection.request("GET", f"{path}?{query}", headers={"Accept": "application/json", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            status = response.status
            headers = list(response.getheaders())
            body = response.read()
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            connection.close()
            if retries < 3:
                time.sleep(2 ** retries)
                retries += 1
                continue
            raise Phase0CError(f"network failure before HTTP response: {exc}") from exc
        finally:
            connection.close()
        request_id, raw_path, gzip_sha = save_raw(endpoint, requested_at, body)
        content_type = next((v for k, v in headers if k.lower() == "content-type"), "")
        error = None
        records: list[dict[str, Any]] = []
        try:
            if status != 200:
                raise Phase0CError(f"HTTP status {status}")
            if "json" not in content_type.lower():
                raise Phase0CError(f"non-JSON content-type {content_type!r}")
            records = validate_records(endpoint, body, target)
        except Phase0CError as exc:
            error = str(exc)
        log = {
            "source_request_id": request_id,
            "request_key": key,
            "source": endpoint,
            "purpose": purpose,
            "method": "GET",
            "source_url": url,
            "normalized_params": canonical_params(params),
            "requested_at_utc": iso_utc(requested_at),
            "http_status": status,
            "response_headers": [{"name": k, "value": v} for k, v in headers],
            "content_type": content_type,
            "record_count": len(records) if error is None else None,
            "raw_path": raw_path,
            "response_sha256": hashlib.sha256(body).hexdigest(),
            "gzip_sha256": gzip_sha,
            "validation_status": "passed" if error is None else "failed",
            "validation_error": error,
            "collector_version": COLLECTOR_VERSION,
            "target": target,
            "run_id": _run_context.run_id if _run_context else None,
            "snapshot_cutoff_utc": _run_context.snapshot_cutoff_utc if _run_context else None,
            "snapshot_cutoff_epoch": _run_context.snapshot_cutoff_epoch if _run_context else None,
        }
        append_log(log)
        if error is None:
            return records, log
        if status == 429 or 500 <= status <= 599:
            if retries < 3:
                time.sleep(2 ** retries)
                retries += 1
                continue
        raise Phase0CError(error)


def smoke(target: str) -> dict[str, Any]:
    specs = {
        "trades": {"user": target, "takerOnly": False, "start": 1, "limit": 1, "offset": 0},
        "activity": {"user": target, "start": 1, "sortBy": "TIMESTAMP", "sortDirection": "ASC", "limit": 1, "offset": 0},
        "positions": {"user": target, "sizeThreshold": 0, "limit": 1, "offset": 0},
        "closed_positions": {"user": target, "limit": 1, "sortBy": "TIMESTAMP", "sortDirection": "ASC", "offset": 0},
    }
    result = {}
    for endpoint, params in specs.items():
        try:
            rows, log = get(endpoint, params, "contract_smoke", target)
            result[endpoint] = {"status": "passed", "record_count": len(rows), "source_request_id": log["source_request_id"], "observed_fields": sorted(rows[0]) if rows else []}
        except Phase0CError as exc:
            result[endpoint] = {"status": "failed", "error": str(exc)}
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "contract_results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def paged(
    endpoint: str,
    target: str,
    base: dict[str, Any],
    purpose: str,
    depth: int = 0,
) -> dict[str, Any]:
    limit = PAGE_LIMITS[endpoint]
    cap = OFFSET_CAPS[endpoint]
    stats = {"page_count": 0, "raw_record_count": 0, "stop_reason": None}
    offset = 0
    while offset <= cap:
        params = dict(base, limit=limit, offset=offset)
        rows, log = get(endpoint, params, purpose, target)
        stats["page_count"] += 1
        stats["raw_record_count"] += len(rows)
        if len(rows) < limit:
            stats["stop_reason"] = "short_page"
            return stats
        if offset + limit > cap:
            if endpoint in {"trades", "activity"}:
                start = int(base.get("start", 1))
                end = int(base.get("end", utc_now().timestamp()))
                if end - start > 1:
                    midpoint = (start + end) // 2
                    left = dict(base, start=start, end=midpoint)
                    right = dict(base, start=midpoint, end=end)
                    left_stats = paged(endpoint, target, left, f"{purpose}_window", depth + 1)
                    right_stats = paged(endpoint, target, right, f"{purpose}_window", depth + 1)
                    return {
                        "page_count": left_stats["page_count"] + right_stats["page_count"],
                        "raw_record_count": left_stats["raw_record_count"] + right_stats["raw_record_count"],
                        "stop_reason": "split_window_complete",
                    }
            raise Phase0CError(
                f"{endpoint}: saturation at offset cap {cap}; "
                f"minimum window={base.get('start')}..{base.get('end')}"
            )
        offset += limit
    raise Phase0CError(f"{endpoint}: unexplained pagination termination")


def collect_target(target: dict[str, Any], passed: set[str]) -> dict[str, Any]:
    wallet = target["proxy_wallet"]
    specs = {
        "trades": {"user": wallet, "takerOnly": False, "start": 1},
        "activity": {"user": wallet, "start": 1, "sortBy": "TIMESTAMP", "sortDirection": "ASC"},
        "positions": {"user": wallet, "sizeThreshold": 0},
        "closed_positions": {"user": wallet, "sortBy": "TIMESTAMP", "sortDirection": "ASC"},
    }
    result: dict[str, Any] = {"seed_username": target["seed_username"], "proxy_wallet": wallet, "datasets": {}}
    for endpoint, params in specs.items():
        if endpoint not in passed:
            result["datasets"][endpoint] = {"status": "blocked_contract", "records": []}
            continue
        try:
            stats = paged(endpoint, wallet, params, "full_collection")
            result["datasets"][endpoint] = {"status": "complete", **stats}
        except Phase0CError as exc:
            result["datasets"][endpoint] = {"status": "partial_or_blocked", "error": str(exc)}
    return result


def fingerprint(row: dict[str, Any]) -> str:
    return hashlib.sha256(json_line(row).encode()).hexdigest()


def normalized_row(endpoint: str, target: dict[str, Any], row: dict[str, Any], log: dict[str, Any], index: int) -> dict[str, Any]:
    timestamp = row.get("timestamp")
    return {
        "schema_version": 1,
        "dataset": endpoint,
        "seed_username": target["seed_username"],
        "proxy_wallet": target["proxy_wallet"],
        "timestamp_epoch_seconds": timestamp,
        "timestamp_utc": datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z") if isinstance(timestamp, int) else None,
        "condition_id": row.get("conditionId"),
        "source_request_id": log["source_request_id"],
        "source_row_index": index,
        "record_fingerprint": fingerprint(row),
        "raw_record": row,
    }


def write_normalized(collected: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    target_names = {item["proxy_wallet"]: item["seed_username"] for item in collected}
    datasets: dict[str, dict[str, Any]] = {}
    NORMALIZED_ROOT.mkdir(parents=True, exist_ok=True)
    logs = [
        json.loads(line) for line in REQUEST_LOG.read_text(encoding="utf-8").splitlines()
        if line
    ]
    for endpoint in ("trades", "activity", "positions", "closed_positions"):
        seen: set[tuple[str, str]] = set()
        raw_count = duplicate_count = 0
        path = NORMALIZED_ROOT / f"{endpoint}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            relevant = [
                log for log in logs
                if log["source"] == endpoint
                and log["validation_status"] == "passed"
                and log["purpose"].startswith("full_collection")
            ]
            for log in relevant:
                rows = json.loads(gzip.decompress((ROOT / log["raw_path"]).read_bytes()))
                for index, row in enumerate(rows):
                    raw_count += 1
                    fp = fingerprint(row)
                    key = (log["target"], fp)
                    if key in seen:
                        duplicate_count += 1
                        continue
                    seen.add(key)
                    target = {"seed_username": target_names[log["target"]], "proxy_wallet": log["target"]}
                    item = normalized_row(endpoint, target, row, log, index)
                    handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        datasets[endpoint] = {
            "path": path.relative_to(ROOT).as_posix(),
            "raw_record_count": raw_count,
            "unique_record_count": len(seen),
            "overlap_duplicate_count": duplicate_count,
        }
    (REPORT_ROOT / "normalization_summary.json").write_text(
        json.dumps(datasets, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return datasets


def collect_markets(datasets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    conditions: set[str] = set()
    for state in datasets.values():
        with (ROOT / state["path"]).open(encoding="utf-8") as handle:
            for line in handle:
                condition = json.loads(line)["condition_id"]
                if condition:
                    conditions.add(condition)
    output = []
    for condition in sorted(conditions):
        rows, log = get("markets", {"condition_ids": condition}, "market_metadata", None)
        for index, row in enumerate(rows):
            if row["conditionId"].lower() == condition.lower():
                output.append({
                    "schema_version": 1, "dataset": "markets", "condition_id": condition.lower(),
                    "source_request_id": log["source_request_id"], "source_row_index": index,
                    "record_fingerprint": fingerprint(row), "observed_at_utc": log["requested_at_utc"],
                    "raw_record": row,
                })
    (NORMALIZED_ROOT / "markets.json").write_text(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return output


class PartitionWriter:
    """Append gzip JSONL observations immediately, without retaining account history."""

    def __init__(self, root: Path, dataset: str, target: dict[str, Any]):
        self.root = root
        self.dataset = dataset
        self.target = target
        self.max_multiplicity: Counter[str] = Counter()
        self.unique_count = 0
        self.observation_count = 0
        self.condition_ids: set[str] = set()

    def _append(self, path: Path, item: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "at", encoding="utf-8") as handle:
            handle.write(json_line(item) + "\n")

    def consume_page(
        self, rows: list[dict[str, Any]], log: dict[str, Any],
        *, write_staging: bool = True, write_normalized: bool = True,
        normalized_cap: int | None = None,
    ) -> None:
        page_counts: Counter[str] = Counter()
        page_examples: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, row in enumerate(rows):
            fp = fingerprint(row)
            page_counts[fp] += 1
            page_examples.setdefault(fp, (index, row))
            timestamp = row.get("timestamp")
            observation = normalized_row(self.dataset, self.target, row, log, index)
            observation["fingerprint"] = observation.pop("record_fingerprint")
            observation["run_id"] = _run_context.run_id if _run_context else None
            observation["snapshot_cutoff_utc"] = (
                _run_context.snapshot_cutoff_utc if _run_context else None
            )
            if write_staging:
                self._append(
                    partition_path(
                        self.root / "staging", self.dataset,
                        self.target["seed_username"], timestamp if isinstance(timestamp, int) else None,
                    ),
                    observation,
                )
                self.observation_count += 1
            condition = row.get("conditionId")
            if isinstance(condition, str):
                self.condition_ids.add(condition.lower())
        if not write_normalized:
            return
        for fp, count in page_counts.items():
            extra = count - self.max_multiplicity[fp]
            if normalized_cap is not None:
                extra = min(extra, normalized_cap - self.unique_count)
            if extra <= 0:
                continue
            index, row = page_examples[fp]
            timestamp = row.get("timestamp")
            for occurrence in range(extra):
                item = normalized_row(self.dataset, self.target, row, log, index)
                item["fingerprint"] = item.pop("record_fingerprint")
                item["source_observations"] = [{
                    "source_request_id": log["source_request_id"],
                    "source_row_index": index + occurrence,
                }]
                item["run_id"] = _run_context.run_id if _run_context else None
                self._append(
                    partition_path(
                        self.root / "normalized", self.dataset,
                        self.target["seed_username"],
                        timestamp if isinstance(timestamp, int) else None,
                    ),
                    item,
                )
                self.unique_count += 1
            self.max_multiplicity[fp] = count
            if normalized_cap is not None and self.unique_count >= normalized_cap:
                return


def bounded_specs(wallet: str, cutoff: int, days: int = 30) -> dict[str, dict[str, Any]]:
    start = cutoff - days * 86_400
    return {
        "trades": {
            "base": {"user": wallet, "takerOnly": False, "start": start, "end": cutoff},
            "limit": 1000, "cap": 20_000, "api_offset_cap": 10_000,
        },
        "activity": {
            "base": {
                "user": wallet, "start": start, "end": cutoff,
                "sortBy": "TIMESTAMP", "sortDirection": "DESC",
            },
            "limit": 500, "cap": 5_000, "api_offset_cap": 5_000,
        },
        "positions": {
            "base": {"user": wallet, "sizeThreshold": 0},
            "limit": 500, "cap": 10_000, "api_offset_cap": 10_000,
        },
        "closed_positions": {
            "base": {"user": wallet, "sortBy": "TIMESTAMP", "sortDirection": "DESC"},
            "limit": 50, "cap": 500, "api_offset_cap": 100_000,
        },
    }


def collect_bounded_dataset(
    endpoint: str,
    target: dict[str, Any],
    spec: dict[str, Any],
    writer: PartitionWriter,
    window_days: int | None,
) -> dict[str, Any]:
    raw_count = 0
    pages = 0
    saturated_windows = 0
    completed_windows = 0

    def replay(logs: list[dict[str, Any]]) -> None:
        for log in logs:
            body = gzip.decompress((ROOT / log["raw_path"]).read_bytes())
            rows = validate_records(endpoint, body, target["proxy_wallet"])
            writer.consume_page(
                rows, log, write_staging=False, write_normalized=True,
                normalized_cap=spec["cap"],
            )

    def collect_window(base: dict[str, Any], depth: int = 0) -> None:
        nonlocal raw_count, pages, saturated_windows, completed_windows
        offset = 0
        window_logs: list[dict[str, Any]] = []
        window_record_count = 0
        while writer.unique_count < spec["cap"]:
            request_limit = spec["limit"]
            params = dict(base, limit=request_limit, offset=offset)
            try:
                rows, log = get(
                    endpoint, params, "bounded_mvp", target["proxy_wallet"]
                )
            except Phase0CError:
                start, end = base.get("start"), base.get("end")
                if (
                    endpoint in {"trades", "activity"}
                    and isinstance(start, int) and isinstance(end, int)
                    and end - start > 1
                ):
                    midpoint = (start + end) // 2
                    collect_window(dict(base, start=midpoint, end=end), depth + 1)
                    if writer.unique_count < spec["cap"]:
                        collect_window(dict(base, start=start, end=midpoint), depth + 1)
                    return
                raise
            writer.consume_page(
                rows, log, write_staging=True, write_normalized=False
            )
            window_logs.append(log)
            pages += 1
            raw_count += len(rows)
            window_record_count += len(rows)
            if (
                endpoint not in {"trades", "activity"}
                and window_record_count >= spec["cap"]
            ):
                replay(window_logs)
                return
            if len(rows) < request_limit:
                completed_windows += 1
                replay(window_logs)
                return
            if offset >= spec["api_offset_cap"]:
                saturated_windows += 1
                start, end = base.get("start"), base.get("end")
                if (
                    endpoint not in {"trades", "activity"}
                    or not isinstance(start, int) or not isinstance(end, int)
                    or end - start <= 1
                ):
                    replay(window_logs)
                    return
                midpoint = (start + end) // 2
                # For bounded MVP caps, prefer the most recent child first.
                collect_window(dict(base, start=midpoint, end=end), depth + 1)
                if writer.unique_count < spec["cap"]:
                    # Inclusive endpoint boundaries intentionally overlap at midpoint;
                    # multiset matching removes only one corresponding observation.
                    collect_window(dict(base, start=start, end=midpoint), depth + 1)
                return
            offset += request_limit

    collect_window(spec["base"])
    cap_reached = (
        writer.unique_count >= spec["cap"]
        or (
            endpoint not in {"trades", "activity"}
            and raw_count >= spec["cap"]
            and completed_windows == 0
        )
    )
    saturated = saturated_windows > 0 and completed_windows == 0
    state = coverage_state(writer.unique_count, spec["cap"], saturated)
    if cap_reached:
        state["coverage_status"] = "partial"
        state["record_cap_reached"] = True
    state.update({
        "record_count": writer.unique_count,
        "source_observation_count": writer.observation_count,
        "page_count": pages,
        "raw_record_count": raw_count,
        "saturated_window_count": saturated_windows,
        "completed_window_count": completed_windows,
        "window_days": window_days,
        "termination_reason": (
            "record_cap_reached" if cap_reached else
            "short_page" if completed_windows else "saturated"
        ),
        "earliest_expected_epoch": spec["base"].get("start"),
        "snapshot_cutoff_epoch": spec["base"].get("end"),
    })
    return state


def collect_bounded_target(target: dict[str, Any], cutoff: int) -> tuple[dict[str, Any], set[str]]:
    result = {
        "seed_username": target["seed_username"],
        "proxy_wallet": target["proxy_wallet"],
        "datasets": {},
    }
    all_conditions: set[str] = set()
    specs = bounded_specs(target["proxy_wallet"], cutoff, 30)
    for endpoint in ("trades", "activity", "positions", "closed_positions"):
        writer = PartitionWriter(NORMALIZED_ROOT, endpoint, target)
        window_days = 30 if endpoint in {"trades", "activity"} else None
        state = collect_bounded_dataset(endpoint, target, specs[endpoint], writer, window_days)
        if (
            endpoint in {"trades", "activity"}
            and state["record_count"] == 0
            and not state["record_cap_reached"]
        ):
            writer = PartitionWriter(NORMALIZED_ROOT, endpoint, target)
            expanded = bounded_specs(target["proxy_wallet"], cutoff, 180)[endpoint]
            state = collect_bounded_dataset(endpoint, target, expanded, writer, 180)
        result["datasets"][endpoint] = state
        all_conditions.update(writer.condition_ids)
    return result, all_conditions


def collect_bounded_markets(condition_ids: set[str]) -> dict[str, Any]:
    writer_target = {"seed_username": "all-targets", "proxy_wallet": None}
    writer = PartitionWriter(NORMALIZED_ROOT, "markets", writer_target)
    requests = 0
    for params in market_query_batches(sorted(condition_ids)):
        rows, log = get("markets", params, "bounded_mvp_market_metadata", None)
        writer.consume_page(rows, log)
        requests += 1
    return {
        "condition_id_count": len(condition_ids),
        "record_count": writer.unique_count,
        "source_observation_count": writer.observation_count,
        "request_count": requests,
        "closed_values_queried": [False, True],
        "coverage_status": "complete",
        "full_history": False,
    }


def run_bounded_mvp(cutoff_epoch: int | None = None) -> dict[str, Any]:
    global _request_index, _run_context
    _request_index = RequestIndex(REQUEST_LOG)
    _run_context = RunContext.create(cutoff_epoch)
    targets = load_targets()
    run_root = NORMALIZED_ROOT / f"run_id={_run_context.run_id}"
    results_by_wallet = {
        target["proxy_wallet"]: {
            "seed_username": target["seed_username"],
            "proxy_wallet": target["proxy_wallet"],
            "datasets": {},
        }
        for target in targets
    }
    conditions: set[str] = set()
    # Dataset-major traversal is deliberately breadth-first across all five targets.
    for endpoint in ("trades", "activity", "positions", "closed_positions"):
        for target in targets:
            specs = bounded_specs(target["proxy_wallet"], _run_context.snapshot_cutoff_epoch, 30)
            writer = PartitionWriter(run_root, endpoint, target)
            window_days = 30 if endpoint in {"trades", "activity"} else None
            state = collect_bounded_dataset(
                endpoint, target, specs[endpoint], writer, window_days
            )
            if (
                endpoint in {"trades", "activity"}
                and state["record_count"] == 0
                and not state["record_cap_reached"]
            ):
                writer = PartitionWriter(run_root, endpoint, target)
                expanded = bounded_specs(
                    target["proxy_wallet"], _run_context.snapshot_cutoff_epoch, 180
                )[endpoint]
                state = collect_bounded_dataset(
                    endpoint, target, expanded, writer, 180
                )
            results_by_wallet[target["proxy_wallet"]]["datasets"][endpoint] = state
            conditions.update(writer.condition_ids)
    market_writer_target = {"seed_username": "all-targets", "proxy_wallet": None}
    market_writer = PartitionWriter(run_root, "markets", market_writer_target)
    market_requests = 0
    for params in market_query_batches(sorted(conditions)):
        rows, log = get("markets", params, "bounded_mvp_market_metadata", None)
        market_writer.consume_page(rows, log)
        market_requests += 1
    markets = {
        "condition_id_count": len(conditions),
        "record_count": market_writer.unique_count,
        "source_observation_count": market_writer.observation_count,
        "request_count": market_requests,
        "closed_values_queried": [False, True],
        "missing_condition_id_count": len(conditions - market_writer.condition_ids),
        "coverage_status": (
            "complete" if conditions <= market_writer.condition_ids else "partial"
        ),
        "full_history": False,
    }
    summary = {
        "collector_version": COLLECTOR_VERSION,
        "run_id": _run_context.run_id,
        "snapshot_cutoff_utc": _run_context.snapshot_cutoff_utc,
        "snapshot_cutoff_epoch": _run_context.snapshot_cutoff_epoch,
        "coverage_label": "PRELIMINARY PARTIAL-COVERAGE MVP",
        "full_history": False,
        "normalized_root": run_root.relative_to(ROOT).as_posix(),
        "targets": [results_by_wallet[t["proxy_wallet"]] for t in targets],
        "markets": markets,
    }
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "bounded_mvp_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--bounded-mvp", action="store_true")
    parser.add_argument("--cutoff-epoch", type=int)
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("Phase 0C requires explicit --execute")
    if args.bounded_mvp:
        print(json.dumps(
            run_bounded_mvp(args.cutoff_epoch), indent=2, ensure_ascii=False
        ))
        return
    targets = load_targets()
    smoke_result = smoke(targets[0]["proxy_wallet"])
    if args.smoke_only:
        print(json.dumps(smoke_result, indent=2, ensure_ascii=False))
        return
    passed = {key for key, value in smoke_result.items() if value["status"] == "passed"}
    collected = []
    for target in targets:
        collected.append(collect_target(target, passed))
    datasets = write_normalized(collected)
    markets = collect_markets(datasets)
    summary = {
        "collector_version": COLLECTOR_VERSION,
        "generated_at_utc": iso_utc(utc_now()),
        "targets": [
            {
                "seed_username": item["seed_username"],
                "proxy_wallet": item["proxy_wallet"],
                "datasets": {
                    endpoint: {
                        "status": state["status"],
                        "raw_record_count": state.get("raw_record_count"),
                        "page_count": state.get("page_count"),
                        "stop_reason": state.get("stop_reason"),
                        "error": state.get("error"),
                    }
                    for endpoint, state in item["datasets"].items()
                },
            }
            for item in collected
        ],
        "market_count": len(markets),
    }
    (REPORT_ROOT / "collection_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
