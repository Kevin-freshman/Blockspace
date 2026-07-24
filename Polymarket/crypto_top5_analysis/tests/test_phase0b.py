import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import phase0b


class Phase0BTests(unittest.TestCase):
    def setUp(self):
        self.seeds = phase0b.load_seeds()

    def test_exact_fixed_seed_set(self):
        self.assertEqual([seed.seed_rank for seed in self.seeds], [1, 2, 3, 4, 5])
        self.assertEqual(self.seeds[0].username, "0x8dxd")
        self.assertEqual(self.seeds[-1].abbreviated_address, "0xeebd…ba30")

    @staticmethod
    def evidence(offset=0, suffix="a"):
        return {
            "source_request_id": f"request-{suffix}",
            "source_url": phase0b.leaderboard_url(offset),
            "requested_at_utc": "2026-07-24T00:00:00.000000Z",
            "offset": offset,
            "raw_path": f"data/raw/request-{suffix}.json.gz",
            "response_sha256": suffix * 64,
        }

    @staticmethod
    def row(seed, address, *, evidence=None, rank=1):
        return {
            "rank": rank,
            "proxyWallet": address,
            "userName": seed.username,
            "vol": 1,
            "pnl": 2,
            "_source": evidence or Phase0BTests.evidence(),
        }

    def audit_paths(self, root):
        report_root = root / "reports" / "phase0b"
        return (
            mock.patch.object(phase0b, "PROJECT_ROOT", root),
            mock.patch.object(phase0b, "REPORT_ROOT", report_root),
            mock.patch.object(
                phase0b, "REQUEST_LOG", report_root / "source_requests.jsonl"
            ),
        )

    def test_url_is_exact_and_offsets_are_restricted(self):
        expected = (
            "https://data-api.polymarket.com/v1/leaderboard?"
            "category=CRYPTO&timePeriod=ALL&orderBy=PNL&limit=50&offset=0"
        )
        self.assertEqual(phase0b.leaderboard_url(0), expected)
        self.assertEqual(phase0b.validate_url(expected), 0)
        for bad in (
            expected + "&user=0xabc",
            expected.replace("data-api.polymarket.com", "example.com"),
            expected.replace("offset=0", "offset=1"),
            expected.replace("limit=50", "limit=0"),
        ):
            with self.assertRaises(phase0b.Phase0BError):
                phase0b.validate_url(bad)

    def test_response_contract(self):
        row = {
            "rank": 1,
            "proxyWallet": "0x" + "ab" * 20,
            "userName": "someone",
            "vol": 1.25,
            "pnl": "2.5",
        }
        self.assertEqual(phase0b.parse_response(json.dumps([row]).encode()), [row])
        invalid = dict(row)
        invalid["proxyWallet"] = "0x1234"
        with self.assertRaises(phase0b.Phase0BError):
            phase0b.parse_response(json.dumps([invalid]).encode())

    def test_matching_is_strict_and_case_insensitive_only_for_address(self):
        seed = self.seeds[0]
        address = "0x63CE" + "1" * 32 + "BA9A"
        row = self.row(seed, address)
        self.assertEqual(phase0b.exact_matches(seed, [row]), [row])
        wrong_name = dict(row, userName=seed.username.upper())
        self.assertEqual(phase0b.exact_matches(seed, [wrong_name]), [])

    def test_checksum_is_not_required(self):
        report = phase0b.build_candidate_report(
            [self.seeds[0]],
            [self.row(self.seeds[0], "0x63CE" + "1" * 32 + "BA9A")],
            [],
            "test",
        )
        candidate = report["targets"][0]["candidates"][0]
        self.assertEqual(candidate["checksum_status"], "not_checked")
        self.assertEqual(
            report["targets"][0]["candidate_status"],
            "unique_candidate_pending_approval",
        )

    def test_deterministic_gzip_and_hash(self):
        body = b'[{"a":1}]'
        first = gzip.compress(body, compresslevel=9, mtime=0)
        second = gzip.compress(body, compresslevel=9, mtime=0)
        self.assertEqual(first, second)
        self.assertEqual(gzip.decompress(first), body)
        self.assertEqual(
            hashlib.sha256(first).hexdigest(),
            hashlib.sha256(second).hexdigest(),
        )

    def test_exclusive_write_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.txt"
            phase0b.exclusive_text(path, "first")
            with self.assertRaises(FileExistsError):
                phase0b.exclusive_text(path, "second")

    def test_only_five_seed_records_enter_candidate_report(self):
        rows = []
        for seed in self.seeds:
            middle = f"{seed.seed_rank:x}" * 32
            address = seed.prefix + middle + seed.suffix
            rows.append(
                self.row(
                    seed,
                    address,
                    evidence=self.evidence(suffix=str(seed.seed_rank)),
                    rank=seed.seed_rank,
                )
            )
        rows.append(
            {
                "rank": 99,
                "proxyWallet": "0x" + "9" * 40,
                "userName": "outside-scope",
                "vol": 99,
                "pnl": 99,
            }
        )
        report = phase0b.build_candidate_report(
            self.seeds, rows, [], "all_five_unique_candidates_found"
        )
        serialized = json.dumps(report)
        self.assertNotIn("outside-scope", serialized)
        self.assertEqual(len(report["targets"]), 5)

    def test_malformed_json_is_raw_saved_and_logged_failed_once(self):
        body = b'{"malformed":'
        requested_at = datetime(2026, 7, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patches = self.audit_paths(root)
            with patches[0], patches[1], patches[2]:
                with self.assertRaisesRegex(
                    phase0b.Phase0BError, "not valid UTF-8 JSON"
                ):
                    phase0b.process_http_response(
                        offset=0,
                        status=200,
                        response_headers=[("Content-Type", "application/json")],
                        body=body,
                        requested_at=requested_at,
                    )
                lines = phase0b.REQUEST_LOG.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["validation_status"], "failed")
                self.assertIn("not valid UTF-8 JSON", entry["validation_error"])
                raw = root / entry["raw_path"]
                self.assertEqual(gzip.decompress(raw.read_bytes()), body)
                self.assertEqual(
                    hashlib.sha256(body).hexdigest(), entry["response_sha256"]
                )

    def test_contract_failure_is_raw_saved_and_logged_failed_once(self):
        body = json.dumps([{"rank": 1}]).encode()
        requested_at = datetime(2026, 7, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patches = self.audit_paths(root)
            with patches[0], patches[1], patches[2]:
                with self.assertRaisesRegex(phase0b.Phase0BError, "missing fields"):
                    phase0b.process_http_response(
                        offset=0,
                        status=200,
                        response_headers=[
                            ("Content-Type", "application/json"),
                            ("X-Audit-Test", "preserved"),
                        ],
                        body=body,
                        requested_at=requested_at,
                    )
                lines = phase0b.REQUEST_LOG.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["validation_status"], "failed")
                self.assertIn("missing fields", entry["validation_error"])
                self.assertIn(
                    {"name": "X-Audit-Test", "value": "preserved"},
                    entry["response_headers"],
                )
                self.assertTrue((root / entry["raw_path"]).is_file())

    def test_success_response_is_logged_once_with_passed_validation(self):
        row = {
            "rank": 1,
            "proxyWallet": "0x" + "ab" * 20,
            "userName": "someone",
            "vol": 1,
            "pnl": 2,
        }
        body = json.dumps([row]).encode()
        requested_at = datetime(2026, 7, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patches = self.audit_paths(root)
            with patches[0], patches[1], patches[2]:
                records, metadata = phase0b.process_http_response(
                    offset=0,
                    status=200,
                    response_headers=[
                        ("Content-Type", "application/json"),
                        ("Content-Encoding", "identity"),
                    ],
                    body=body,
                    requested_at=requested_at,
                )
                self.assertEqual(records, [row])
                lines = phase0b.REQUEST_LOG.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry, metadata)
                self.assertEqual(entry["validation_status"], "passed")
                self.assertIsNone(entry["validation_error"])
                self.assertEqual(entry["record_count"], 1)
                self.assertEqual(entry["collector_version"], "phase0b-2")

    def test_http_and_header_failures_each_write_exactly_one_failed_log(self):
        cases = (
            (0, 503, [("Content-Type", "application/json")], b"[]", "HTTP status"),
            (50, 200, [("Content-Type", "text/html")], b"<html>", "non-JSON"),
            (
                100,
                200,
                [
                    ("Content-Type", "application/json"),
                    ("Content-Encoding", "gzip"),
                ],
                b"[]",
                "unexpected content encoding",
            ),
        )
        requested_at = datetime(2026, 7, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patches = self.audit_paths(root)
            with patches[0], patches[1], patches[2]:
                for offset, status, headers, body, error in cases:
                    with self.assertRaisesRegex(phase0b.Phase0BError, error):
                        phase0b.process_http_response(
                            offset=offset,
                            status=status,
                            response_headers=headers,
                            body=body,
                            requested_at=requested_at,
                        )
                entries = [
                    json.loads(line)
                    for line in phase0b.REQUEST_LOG.read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]
                self.assertEqual(len(entries), len(cases))
                self.assertEqual(
                    [entry["offset"] for entry in entries], [0, 50, 100]
                )
                self.assertTrue(
                    all(entry["validation_status"] == "failed" for entry in entries)
                )
                self.assertTrue(
                    all(entry["validation_error"] for entry in entries)
                )
                self.assertTrue(
                    all((root / entry["raw_path"]).is_file() for entry in entries)
                )

    def test_network_failure_before_response_writes_no_raw_or_request_log(self):
        connection = mock.Mock()
        connection.request.side_effect = OSError("DNS unavailable")
        connection_type = mock.Mock(return_value=connection)
        with mock.patch.object(
            phase0b.http.client, "HTTPSConnection", connection_type
        ), mock.patch.object(phase0b, "save_raw") as save_raw, mock.patch.object(
            phase0b, "append_request_log"
        ) as append_log:
            with self.assertRaisesRegex(
                phase0b.Phase0BError, "network failure before HTTP response"
            ):
                phase0b.request_page(0)
        save_raw.assert_not_called()
        append_log.assert_not_called()
        connection.close.assert_called_once_with()

    def test_success_candidate_contains_complete_source_evidence(self):
        seed = self.seeds[0]
        evidence = self.evidence(offset=50, suffix="b")
        record = self.row(
            seed, "0x63ce" + "2" * 32 + "ba9a", evidence=evidence
        )
        report = phase0b.build_candidate_report([seed], [record], [], "test")
        candidate = report["targets"][0]["candidates"][0]
        for field, value in evidence.items():
            self.assertEqual(candidate[field], value)
        self.assertEqual(len(candidate["source_observations"]), 1)

    def test_same_address_across_pages_is_one_candidate_with_all_observations(self):
        seed = self.seeds[0]
        lower = "0x63ce" + "3" * 32 + "ba9a"
        mixed = "0x63CE" + "3" * 32 + "BA9A"
        rows = [
            self.row(seed, lower, evidence=self.evidence(offset=0, suffix="c")),
            self.row(seed, mixed, evidence=self.evidence(offset=50, suffix="d")),
        ]
        report = phase0b.build_candidate_report([seed], rows, [], "test")
        target = report["targets"][0]
        self.assertEqual(target["candidate_count"], 1)
        self.assertEqual(
            target["candidate_status"], "unique_candidate_pending_approval"
        )
        self.assertEqual(len(target["candidates"][0]["source_observations"]), 2)

    def test_two_distinct_addresses_are_multiple_candidates(self):
        seed = self.seeds[0]
        rows = [
            self.row(
                seed,
                "0x63ce" + "4" * 32 + "ba9a",
                evidence=self.evidence(offset=0, suffix="e"),
            ),
            self.row(
                seed,
                "0x63ce" + "5" * 32 + "ba9a",
                evidence=self.evidence(offset=50, suffix="f"),
            ),
        ]
        report = phase0b.build_candidate_report([seed], rows, [], "test")
        target = report["targets"][0]
        self.assertEqual(target["candidate_count"], 2)
        self.assertEqual(target["candidate_status"], "multiple_candidates_blocked")


if __name__ == "__main__":
    unittest.main()
