import re
import json
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import mvp_report


class MVPReportTests(unittest.TestCase):
    def test_targets_schema_and_invariants(self):
        config = yaml.safe_load((ROOT / "config" / "targets.yaml").read_text())
        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(config["config_version"], "phase0b-approved-v1")
        self.assertEqual(len(config["targets"]), 5)
        addresses = [target["proxy_wallet"] for target in config["targets"]]
        self.assertEqual(len(set(addresses)), 5)
        self.assertTrue(
            all(re.fullmatch(r"0x[a-f0-9]{40}", value) for value in addresses)
        )
        self.assertTrue(
            all(
                target["approval"]["decision"] == "APPROVE"
                and target["approval"]["scope"]
                == "polymarket_user_profile_address"
                for target in config["targets"]
            )
        )

    def test_anon_automatic_facts_are_preserved(self):
        config = yaml.safe_load((ROOT / "config" / "targets.yaml").read_text())
        anon = next(
            target for target in config["targets"] if target["seed_username"] == "Anon"
        )
        diagnostic = anon["automated_diagnostics"]
        self.assertEqual(diagnostic["original_status"], "zero_candidates_blocked")
        self.assertEqual(diagnostic["api_username"], "")
        self.assertFalse(diagnostic["strict_username_match"])
        self.assertEqual(
            diagnostic["flags"],
            [
                "unique_abbreviated_address_match",
                "unique_address_candidate_username_semantics_unresolved",
            ],
        )
        self.assertEqual(
            anon["supplemental_evidence"][0]["evidence_type"],
            "official_profile_manual_review",
        )

    def test_reports_are_deterministic_and_complete(self):
        paths = [ROOT / "reports/mvp/overview.md", ROOT / "reports/mvp/data_coverage.md"]
        paths += [ROOT / f"reports/mvp/accounts/{slug}.md" for slug in mvp_report.SLUGS.values()]
        self.assertTrue(all(path.is_file() for path in paths))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# PRELIMINARY PARTIAL-COVERAGE MVP\n"))
            self.assertIn("This is not full account history.", text)
        metrics = json.loads((ROOT / "reports/phase0c/mvp_metrics.json").read_text())
        self.assertEqual(len(metrics["accounts"]), 5)
        self.assertFalse(metrics["full_history"])
        self.assertTrue(all(v["trade_count"] > 0 for v in metrics["accounts"].values()))

    def test_reports_do_not_overstate_phase0c(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "reports/mvp").rglob("*.md")
        )
        self.assertIn("不是账户全历史", combined)
        self.assertNotIn("insider trading 结论", combined)
        self.assertNotIn("wash trading 结论", combined)


if __name__ == "__main__":
    unittest.main()
