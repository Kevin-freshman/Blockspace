import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import phase0c_qa


class Phase0CQualityAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = phase0c_qa.build_all(write=True)

    def test_condition_id_identity_and_response_statistics(self):
        audit = self.result["market"]
        self.assertTrue(audit["identity_holds"])
        self.assertEqual(
            audit["requested_unique_condition_ids"],
            audit["resolved_unique_requested_ids"] + audit["unresolved_unique_ids"],
        )
        self.assertGreaterEqual(audit["duplicate_response_rows"], 0)
        self.assertGreaterEqual(audit["unexpected_response_ids"], 0)
        self.assertEqual(
            audit["duplicate_response_rows"],
            audit["response_market_rows"] - audit["response_unique_condition_ids"],
        )

    def test_failed_request_classification_is_complete(self):
        audit = self.result["failed"]
        self.assertEqual(audit["failed_request_count"], 16)
        for item in audit["requests"]:
            self.assertIn(item["classification"], {"transient_recovered", "terminal_gap"})
            self.assertIn("coverage_impact", item)
            self.assertIn("retry_replacement_request_id", item)

    def test_terminal_failure_forces_partial(self):
        self.assertEqual(
            phase0c_qa.completeness_label("complete", False, True),
            "partial within the configured bounded window",
        )

    def test_caps_and_stop_reasons_are_consistent(self):
        for endpoints in self.result["coverage"]["accounts"].values():
            for state in endpoints.values():
                if state["cap_reached"]:
                    self.assertEqual(state["stop_reason"], "record_cap_reached")

    def test_bonereaper_496_is_reproducible(self):
        item = self.result["bonereaper"]
        self.assertEqual(item["source_rows"], 500)
        self.assertEqual(item["duplicate_source_observations"], 4)
        self.assertEqual(item["normalized_rows"], 496)
        self.assertTrue(item["cap_reached"])

    def test_reports_are_deterministic_for_same_inputs(self):
        paths = sorted((ROOT / "reports/mvp").rglob("*.md"))
        before = {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }
        phase0c_qa.build_all(write=True)
        after = {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
