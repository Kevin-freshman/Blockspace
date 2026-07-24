import gzip
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src import phase0c


class Phase0CBoundedTests(unittest.TestCase):
    def test_request_index_is_loaded_once_and_reuses_exact_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "requests.jsonl"
            raw = Path(tmp) / "raw.json.gz"
            body = b"[]"
            raw.write_bytes(gzip.compress(body, mtime=0))
            item = {
                "request_key": "key", "validation_status": "passed",
                "raw_path": str(raw), "response_sha256": phase0c.sha256(body),
                "gzip_sha256": phase0c.sha256(raw.read_bytes()),
            }
            log.write_text(json.dumps(item) + "\n", encoding="utf-8")
            index = phase0c.RequestIndex(log)
            self.assertEqual(index.success("key"), item)
            log.write_text("", encoding="utf-8")
            self.assertEqual(index.success("key"), item)

    def test_run_context_has_one_fixed_cutoff(self):
        run = phase0c.RunContext.create(1_800_000_000)
        self.assertEqual(run.snapshot_cutoff_epoch, 1_800_000_000)
        self.assertIn("Z", run.snapshot_cutoff_utc)
        self.assertTrue(run.run_id)

    def test_decimal_json_does_not_round_trip_through_float(self):
        rows = phase0c.decode_rows(b'[{"price":0.1234567890123456789}]')
        self.assertEqual(rows[0]["price"], Decimal("0.1234567890123456789"))
        encoded = phase0c.json_line(rows[0])
        self.assertIn('"0.1234567890123456789"', encoded)

    def test_multiset_overlap_preserves_duplicates_inside_response(self):
        pages = [
            [{"id": "a"}, {"id": "a"}, {"id": "b"}],
            [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        ]
        kept = phase0c.multiset_merge(pages)
        self.assertEqual(kept.count({"id": "a"}), 2)
        self.assertEqual(kept.count({"id": "b"}), 1)
        self.assertEqual(kept.count({"id": "c"}), 1)

    def test_complete_children_exclude_saturated_parent(self):
        windows = [
            {"window_id": "p", "parent_window_id": None, "saturated": True, "complete": False},
            {"window_id": "l", "parent_window_id": "p", "saturated": False, "complete": True},
            {"window_id": "r", "parent_window_id": "p", "saturated": False, "complete": True},
        ]
        self.assertEqual(phase0c.normalized_window_ids(windows), {"l", "r"})

    def test_partition_path_includes_dataset_seed_year_month(self):
        path = phase0c.partition_path(
            Path("normalized"), "trades", "Anon", 1_800_000_000
        )
        self.assertEqual(
            path.as_posix(),
            "normalized/dataset=trades/seed_username=anon/year=2027/month=01/records.jsonl.gz",
        )

    def test_market_batches_cover_both_closed_states(self):
        batches = phase0c.market_query_batches(["b", "a"], batch_size=1)
        self.assertEqual(
            batches,
            [
                {"condition_ids": ["a"], "closed": False},
                {"condition_ids": ["a"], "closed": True},
                {"condition_ids": ["b"], "closed": False},
                {"condition_ids": ["b"], "closed": True},
            ],
        )

    def test_caps_are_explicitly_labeled(self):
        state = phase0c.coverage_state(20_000, 20_000, False)
        self.assertEqual(state["coverage_status"], "partial")
        self.assertTrue(state["record_cap_reached"])
        self.assertFalse(state["full_history"])

    def test_activity_allows_empty_optional_condition_id(self):
        body = (
            b'[{"proxyWallet":"0x1111111111111111111111111111111111111111",'
            b'"timestamp":1800000000,"type":"CASH","conditionId":""}]'
        )
        rows = phase0c.validate_records(
            "activity", body, "0x1111111111111111111111111111111111111111"
        )
        self.assertEqual(rows[0]["conditionId"], "")


if __name__ == "__main__":
    unittest.main()
