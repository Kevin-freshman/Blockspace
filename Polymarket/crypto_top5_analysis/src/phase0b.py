#!/usr/bin/env python3
"""Restricted Phase 0B Polymarket leaderboard identity resolver."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urlencode, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEEDS_PATH = PROJECT_ROOT / "config" / "target_seeds.yaml"
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "polymarket_leaderboard"
REPORT_ROOT = PROJECT_ROOT / "reports" / "phase0b"
REQUEST_LOG = REPORT_ROOT / "source_requests.jsonl"
CANDIDATE_REPORT = REPORT_ROOT / "identity_candidates.json"
APPROVAL_REPORT = REPORT_ROOT / "identity_approval.md"

DOC_URL = (
    "https://docs.polymarket.com/api-reference/core/"
    "get-trader-leaderboard-rankings"
)
API_HOST = "data-api.polymarket.com"
API_PATH = "/v1/leaderboard"
OFFSETS = tuple(range(0, 1001, 50))
LIMIT = 50
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
REQUIRED_FIELDS = ("rank", "proxyWallet", "userName", "vol", "pnl")


class Phase0BError(RuntimeError):
    """A fail-closed Phase 0B contract or security error."""


@dataclass(frozen=True)
class Seed:
    seed_rank: int
    username: str
    abbreviated_address: str
    seed_pnl: int
    seed_volume: int
    seed_ratio: str

    @property
    def prefix(self) -> str:
        return self.abbreviated_address.split("…", 1)[0].lower()

    @property
    def suffix(self) -> str:
        return self.abbreviated_address.split("…", 1)[1].lower()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_seeds(path: Path = SEEDS_PATH) -> list[Seed]:
    """Parse the deliberately small, fixed seed YAML without dependencies."""
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?=^  - seed_rank:)", text, flags=re.MULTILINE)[1:]
    seeds: list[Seed] = []
    patterns = {
        "seed_rank": r'^  - seed_rank:\s*(\d+)\s*$',
        "username": r'^    username:\s*"([^"]+)"\s*$',
        "abbreviated_address": r'^    abbreviated_address:\s*"([^"]+)"\s*$',
        "seed_pnl": r"^    official_pnl_usd:\s*(\d+)\s*$",
        "seed_volume": r"^    official_volume_usd:\s*(\d+)\s*$",
        "seed_ratio": r'^    pnl_to_volume:\s*"([^"]+)"\s*$',
    }
    for block in blocks:
        values: dict[str, Any] = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, block, flags=re.MULTILINE)
            if not match:
                raise Phase0BError(f"seed field missing: {key}")
            values[key] = match.group(1)
        seeds.append(
            Seed(
                seed_rank=int(values["seed_rank"]),
                username=values["username"],
                abbreviated_address=values["abbreviated_address"],
                seed_pnl=int(values["seed_pnl"]),
                seed_volume=int(values["seed_volume"]),
                seed_ratio=values["seed_ratio"],
            )
        )
    if [seed.seed_rank for seed in seeds] != [1, 2, 3, 4, 5]:
        raise Phase0BError("seed ranks must be exactly 1 through 5")
    if len({seed.abbreviated_address.lower() for seed in seeds}) != 5:
        raise Phase0BError("seed abbreviated addresses must be unique")
    return seeds


def leaderboard_url(offset: int) -> str:
    if offset not in OFFSETS:
        raise Phase0BError(f"offset not authorized: {offset}")
    query = urlencode(
        [
            ("category", "CRYPTO"),
            ("timePeriod", "ALL"),
            ("orderBy", "PNL"),
            ("limit", str(LIMIT)),
            ("offset", str(offset)),
        ]
    )
    return f"https://{API_HOST}{API_PATH}?{query}"


def validate_url(url: str) -> int:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != API_HOST:
        raise Phase0BError("unauthorized scheme or host")
    if parsed.port is not None or parsed.path != API_PATH or parsed.fragment:
        raise Phase0BError("unauthorized port, path, or fragment")
    params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    expected_keys = {"category", "timePeriod", "orderBy", "limit", "offset"}
    if set(params) != expected_keys or any(len(value) != 1 for value in params.values()):
        raise Phase0BError("unauthorized or duplicate query parameter")
    if params["category"] != ["CRYPTO"] or params["timePeriod"] != ["ALL"]:
        raise Phase0BError("unauthorized leaderboard filter")
    if params["orderBy"] != ["PNL"] or params["limit"] != [str(LIMIT)]:
        raise Phase0BError("unauthorized order or limit")
    try:
        offset = int(params["offset"][0])
    except ValueError as exc:
        raise Phase0BError("offset is not an integer") from exc
    if offset not in OFFSETS:
        raise Phase0BError(f"offset not authorized: {offset}")
    return offset


def parse_response(body: bytes) -> list[dict[str, Any]]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase0BError("response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, list):
        raise Phase0BError("leaderboard response must be a JSON array")
    for index, record in enumerate(payload):
        if not isinstance(record, dict):
            raise Phase0BError(f"record {index} is not an object")
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise Phase0BError(f"record {index} missing fields: {missing}")
        if not isinstance(record["userName"], str):
            raise Phase0BError(f"record {index} userName is not a string")
        if not isinstance(record["proxyWallet"], str):
            raise Phase0BError(f"record {index} proxyWallet is not a string")
        if not ADDRESS_RE.fullmatch(record["proxyWallet"]):
            raise Phase0BError(f"record {index} proxyWallet has invalid format")
        for field in ("rank", "vol", "pnl"):
            value = record[field]
            if value is None or isinstance(value, (dict, list, bool)):
                raise Phase0BError(f"record {index} {field} is not scalar")
    return payload


def exact_matches(seed: Seed, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for record in records:
        address = record["proxyWallet"]
        lowered = address.lower()
        if (
            record["userName"] == seed.username
            and ADDRESS_RE.fullmatch(address)
            and lowered.startswith(seed.prefix)
            and lowered.endswith(seed.suffix)
        ):
            matches.append(record)
    return matches


def grouped_matches(
    seed: Seed, records: Iterable[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Group strict matches by normalized full address across all observations."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in exact_matches(seed, records):
        grouped.setdefault(record["proxyWallet"].lower(), []).append(record)
    return grouped


def candidates_complete(
    seeds: Iterable[Seed], records: Iterable[dict[str, Any]]
) -> bool:
    rows = list(records)
    matches = [grouped_matches(seed, rows) for seed in seeds]
    if any(len(items) != 1 for items in matches):
        return False
    addresses = [next(iter(items)) for items in matches]
    return len(set(addresses)) == len(addresses)


def save_raw(
    body: bytes,
    offset: int,
    requested_at: datetime,
) -> tuple[str, str, str, str]:
    body_sha = hashlib.sha256(body).hexdigest()
    source_request_id = (
        f"{requested_at.strftime('%Y%m%dT%H%M%S.%fZ')}"
        f"_offset-{offset:04d}_{body_sha[:16]}"
    )
    relative = (
        Path("data")
        / "raw"
        / "polymarket_leaderboard"
        / requested_at.strftime("%Y")
        / requested_at.strftime("%m")
        / requested_at.strftime("%d")
        / f"{source_request_id}.json.gz"
    )
    path = PROJECT_ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(body, compresslevel=9, mtime=0)
    gzip_sha = hashlib.sha256(compressed).hexdigest()
    with path.open("xb") as handle:
        handle.write(compressed)
    return source_request_id, relative.as_posix(), body_sha, gzip_sha


def response_header_value(
    response_headers: list[tuple[str, str]], name: str
) -> Optional[str]:
    values = [value for key, value in response_headers if key.lower() == name.lower()]
    return ", ".join(values) if values else None


def process_http_response(
    *,
    offset: int,
    status: int,
    response_headers: list[tuple[str, str]],
    body: bytes,
    requested_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Persist and log one received HTTP response exactly once, pass or fail."""
    url = leaderboard_url(offset)
    source_request_id, raw_path, body_sha, gzip_sha = save_raw(
        body, offset, requested_at
    )
    content_type_value = response_header_value(response_headers, "content-type")
    content_encoding_value = response_header_value(
        response_headers, "content-encoding"
    )
    metadata = {
        "source_request_id": source_request_id,
        "method": "GET",
        "url": url,
        "normalized_params": {
            "category": "CRYPTO",
            "timePeriod": "ALL",
            "orderBy": "PNL",
            "limit": LIMIT,
            "offset": offset,
        },
        "requested_at_utc": utc_text(requested_at),
        "http_status": status,
        "response_headers": [
            {"name": name, "value": value} for name, value in response_headers
        ],
        "content_type": content_type_value,
        "content_encoding": content_encoding_value,
        "response_bytes": len(body),
        "record_count": None,
        "offset": offset,
        "response_sha256": body_sha,
        "gzip_sha256": gzip_sha,
        "raw_path": raw_path,
        "collector_version": "phase0b-2",
        "git_commit": "unavailable_permission_boundary",
        "validation_status": "failed",
        "validation_error": None,
    }
    records: Optional[list[dict[str, Any]]] = None
    validation_error: Optional[Phase0BError] = None
    try:
        if status != 200:
            raise Phase0BError(f"HTTP status {status} at offset {offset}")
        content_type = (content_type_value or "").lower()
        if "application/json" not in content_type:
            raise Phase0BError(f"non-JSON content type at offset {offset}")
        content_encoding = (content_encoding_value or "identity").lower()
        if content_encoding not in ("", "identity"):
            raise Phase0BError(f"unexpected content encoding: {content_encoding}")
        records = parse_response(body)
    except Phase0BError as exc:
        validation_error = exc
        metadata["validation_error"] = str(exc)
    else:
        metadata["record_count"] = len(records)
        metadata["validation_status"] = "passed"
    append_request_log(metadata)
    if validation_error is not None:
        raise validation_error
    assert records is not None
    return records, metadata


def request_page(offset: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = leaderboard_url(offset)
    validate_url(url)
    requested_at = utc_now()
    connection = http.client.HTTPSConnection(
        API_HOST, port=443, timeout=30, context=ssl.create_default_context()
    )
    try:
        try:
            connection.request(
                "GET",
                urlsplit(url).path + "?" + urlsplit(url).query,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            )
            response = connection.getresponse()
            body = response.read()
            response_headers = response.getheaders()
        except OSError as exc:
            raise Phase0BError(
                f"network failure before HTTP response at offset {offset}: {exc}"
            ) from exc
    finally:
        connection.close()

    return process_http_response(
        offset=offset,
        status=response.status,
        response_headers=response_headers,
        body=body,
        requested_at=requested_at,
    )


def append_request_log(metadata: dict[str, Any]) -> None:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    with REQUEST_LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n")


def build_candidate_report(
    seeds: list[Seed],
    records: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    stop_reason: str,
) -> dict[str, Any]:
    targets = []
    per_seed_matches = [grouped_matches(seed, records) for seed in seeds]
    singleton_addresses = [
        next(iter(items))
        for items in per_seed_matches
        if len(items) == 1
    ]
    duplicate_singletons = {
        address
        for address in singleton_addresses
        if singleton_addresses.count(address) > 1
    }

    for seed, matches_by_address in zip(seeds, per_seed_matches):
        if not matches_by_address:
            status = "zero_candidates_blocked"
        elif len(matches_by_address) > 1:
            status = "multiple_candidates_blocked"
        elif next(iter(matches_by_address)) in duplicate_singletons:
            status = "identity_conflict_blocked"
        else:
            status = "unique_candidate_pending_approval"

        candidate_rows = []
        for normalized_address, observations in matches_by_address.items():
            record = observations[0]
            source_observations = []
            for observation in observations:
                evidence = observation.get("_source")
                if not isinstance(evidence, dict):
                    raise Phase0BError(
                        "matched record lacks required source evidence"
                    )
                source_observations.append(
                    {
                        "source_request_id": evidence["source_request_id"],
                        "source_url": evidence["source_url"],
                        "requested_at_utc": evidence["requested_at_utc"],
                        "offset": evidence["offset"],
                        "raw_path": evidence["raw_path"],
                        "response_sha256": evidence["response_sha256"],
                        "proxy_wallet_original": observation["proxyWallet"],
                        "observed_rank": observation["rank"],
                        "observed_pnl_raw": observation["pnl"],
                        "observed_vol_raw": observation["vol"],
                    }
                )
            first_evidence = source_observations[0]
            candidate_rows.append(
                {
                    "proxy_wallet_original": record["proxyWallet"],
                    "proxy_wallet_lowercase": normalized_address,
                    "checksum_status": "not_checked",
                    "observed_user_name": record["userName"],
                    "observed_rank": record["rank"],
                    "observed_pnl_raw": record["pnl"],
                    "observed_vol_raw": record["vol"],
                    "pnl_unit": "unverified",
                    "pnl_calculation_basis": "unverified",
                    "vol_unit": "unverified",
                    "vol_calculation_basis": "unverified",
                    "username_match": record["userName"] == seed.username,
                    "address_format_match": bool(
                        ADDRESS_RE.fullmatch(record["proxyWallet"])
                    ),
                    "prefix_match": record["proxyWallet"]
                    .lower()
                    .startswith(seed.prefix),
                    "suffix_match": record["proxyWallet"].lower().endswith(seed.suffix),
                    "source_request_id": first_evidence["source_request_id"],
                    "source_url": first_evidence["source_url"],
                    "requested_at_utc": first_evidence["requested_at_utc"],
                    "offset": first_evidence["offset"],
                    "raw_path": first_evidence["raw_path"],
                    "response_sha256": first_evidence["response_sha256"],
                    "source_observations": source_observations,
                }
            )
        targets.append(
            {
                "seed_rank": seed.seed_rank,
                "seed_username": seed.username,
                "seed_abbreviated_address": seed.abbreviated_address,
                "seed_pnl_usd": seed.seed_pnl,
                "seed_volume_usd": seed.seed_volume,
                "seed_pnl_to_volume": seed.seed_ratio,
                "seed_snapshot_as_of_utc": None,
                "candidate_status": status,
                "candidate_count": len(candidate_rows),
                "candidates": candidate_rows,
                "comparison_note": (
                    "API pnl/vol units and calculation bases are unverified; "
                    "no arithmetic difference from the USD-labelled seed values "
                    "is asserted."
                ),
                "human_decision": "pending",
            }
        )

    return {
        "schema_version": 1,
        "phase": "0B",
        "generated_at_utc": utc_text(utc_now()),
        "official_documentation_url": DOC_URL,
        "filters": {
            "category": "CRYPTO",
            "timePeriod": "ALL",
            "orderBy": "PNL",
            "sorting_direction": "unverified",
            "limit": LIMIT,
        },
        "coverage_statement": (
            "Current leaderboard pages requested within the official offset range; "
            "this is not complete coverage of any historical leaderboard."
        ),
        "stop_reason": stop_reason,
        "source_requests": [
            {
                "source_request_id": item["source_request_id"],
                "url": item["url"],
                "requested_at_utc": item["requested_at_utc"],
                "response_sha256": item["response_sha256"],
                "raw_path": item["raw_path"],
            }
            for item in requests
        ],
        "targets": targets,
    }


def approval_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 0B 五账户身份候选人工审批",
        "",
        "请对每个账户填写：`批准`、`拒绝` 或 `需要补充证据`。",
        "",
        "| 种子排名 | 种子用户名 | 种子地址缩写 | 候选 User Profile Address | 当前 rank | 当前 pnl（原始；单位未核验） | 当前 vol（原始；单位未核验） | 状态 | 人工决定 | 备注 |",
        "|---:|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for target in report["targets"]:
        candidates = target["candidates"]
        if len(candidates) == 1:
            candidate = candidates[0]
            address = candidate["proxy_wallet_original"]
            rank = candidate["observed_rank"]
            pnl = candidate["observed_pnl_raw"]
            vol = candidate["observed_vol_raw"]
        else:
            address = f"{len(candidates)} 个候选"
            rank = "—"
            pnl = "—"
            vol = "—"
        lines.append(
            f"| {target['seed_rank']} | {target['seed_username']} | "
            f"`{target['seed_abbreviated_address']}` | `{address}` | {rank} | "
            f"{pnl} | {vol} | `{target['candidate_status']}` |  |  |"
        )
    lines.extend(
        [
            "",
            "说明：`proxyWallet` 在本阶段仅称为 Polymarket User Profile Address。",
            "checksum 未作为必要门槛；当前值与种子 USD 值不作未经证实的单位比较。",
            "人工批准后仍需单独授权，才可在后续阶段创建或锁定 `targets.yaml`。",
            "",
        ]
    )
    return "\n".join(lines)


def exclusive_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def run() -> dict[str, Any]:
    if CANDIDATE_REPORT.exists() or APPROVAL_REPORT.exists():
        raise Phase0BError("candidate output already exists; refusing overwrite")
    seeds = load_seeds()
    all_records: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    stop_reason = "offset_limit_reached"

    for offset in OFFSETS:
        page, metadata = request_page(offset)
        requests.append(metadata)
        evidence = {
            "source_request_id": metadata["source_request_id"],
            "source_url": metadata["url"],
            "requested_at_utc": metadata["requested_at_utc"],
            "offset": metadata["offset"],
            "raw_path": metadata["raw_path"],
            "response_sha256": metadata["response_sha256"],
        }
        all_records.extend(
            dict(record, _source=dict(evidence)) for record in page
        )
        if candidates_complete(seeds, all_records):
            stop_reason = "all_five_unique_candidates_found"
            break
        if not page:
            stop_reason = "empty_page"
            break
        if len(page) < LIMIT:
            stop_reason = "short_page"
            break
        if offset == 1000:
            stop_reason = "offset_limit_reached"
            break

    report = build_candidate_report(seeds, all_records, requests, stop_reason)
    exclusive_text(
        CANDIDATE_REPORT,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    exclusive_text(APPROVAL_REPORT, approval_markdown(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the explicitly authorized live Phase 0B requests",
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required")
    try:
        report = run()
    except Phase0BError as exc:
        print(f"Phase 0B stopped: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
