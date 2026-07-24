import hashlib
import json
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
PHASE0C = ROOT / "reports" / "phase0c"
ACCOUNT_ORDER = ["0x8dxd", "Anon", "justdance", "k9Q2mX4L8A7ZP3R", "Bonereaper"]
SLUGS = {name: name.lower().replace("0x", "0x-") for name in ACCOUNT_ORDER}


class AuditParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chart_audits = 0
        self.external_urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-chart-audit") == "complete":
            self.chart_audits += 1
        for key in ("href", "src"):
            value = attrs.get(key, "")
            if value.startswith(("http://", "https://", "//")):
                self.external_urls.append(value)


class SiteBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["python3", "src/build_site.py"], cwd=ROOT, check=True)
        cls.site = json.loads((PUBLIC / "data/site-data.json").read_text())
        cls.metrics = json.loads((PHASE0C / "research_metrics.json").read_text())
        cls.market = json.loads((PHASE0C / "market_coverage_audit.json").read_text())
        cls.failures = json.loads((PHASE0C / "failed_requests_audit.json").read_text())
        cls.coverage = json.loads((PHASE0C / "coverage_audit.json").read_text())
        cls.raw = json.loads((PHASE0C / "raw_integrity_audit.json").read_text())

    def test_expected_pages_and_preliminary_label(self):
        pages = [
            PUBLIC / "index.html",
            PUBLIC / "cross-account/index.html",
            PUBLIC / "data-coverage/index.html",
            PUBLIC / "glossary/index.html",
            *(PUBLIC / "accounts" / SLUGS[name] / "index.html" for name in ACCOUNT_ORDER),
        ]
        for page in pages:
            self.assertTrue(page.is_file(), page)
            text = page.read_text()
            self.assertIn("PRELIMINARY PARTIAL-COVERAGE MVP", text)
            self.assertIn("This is not full account history.", text)

    def test_account_metrics_are_exact_source_values(self):
        def normalized(value):
            if isinstance(value, float):
                return str(value)
            if isinstance(value, list):
                return [normalized(item) for item in value]
            if isinstance(value, dict):
                return {key: normalized(item) for key, item in value.items()}
            return value

        keys = (
            "trade_count", "buy_count", "sell_count", "observed_notional",
            "size_p25", "size_median", "size_p75", "notional_p25",
            "notional_median", "notional_p75", "median_interval_seconds",
            "hhi", "top_10_markets", "current_position_count",
            "bounded_closed_position_count", "burst_trade_count_5m",
            "burst_ratio_5m", "reversal_churn_10m_count",
            "classification_trade_counts", "metadata_resolved_trade_ratio",
        )
        for name in ACCOUNT_ORDER:
            for key in keys:
                self.assertEqual(
                    normalized(self.site["accounts"][name][key]),
                    normalized(self.metrics["accounts"][name][key]),
                )

    def test_cross_account_metrics_are_exact(self):
        self.assertEqual(self.site["account_pairs"], self.metrics["account_pairs"])
        self.assertEqual(
            self.site["all_five_common_condition_ids"],
            self.metrics["all_five_common_condition_ids"],
        )

    def test_market_identity_and_counts_are_exact(self):
        public = self.site["market_coverage"]
        for key in (
            "requested_unique_condition_ids", "response_market_rows",
            "resolved_unique_requested_ids", "unresolved_unique_ids",
            "duplicate_response_rows", "unexpected_response_ids", "identity_holds",
        ):
            self.assertEqual(public[key], self.market[key])
        self.assertEqual(
            public["requested_unique_condition_ids"],
            public["resolved_unique_requested_ids"] + public["unresolved_unique_ids"],
        )
        self.assertIn("40 total", public["legacy_summary_reconciliation"]["difference_of_40_reason"])

    def test_failure_and_raw_integrity_summaries_are_exact(self):
        f = self.site["failed_requests"]
        self.assertEqual(f["failed_request_count"], 16)
        self.assertEqual(f["failed_request_count"], self.failures["failed_request_count"])
        self.assertEqual(f["transient_recovered_count"], self.failures["transient_recovered_count"])
        self.assertEqual(f["terminal_gap_count"], self.failures["terminal_gap_count"])
        self.assertEqual(len(f["requests"]), len(self.failures["requests"]))
        self.assertTrue(all(row["classification"] == "transient_recovered" for row in f["requests"]))
        for key in (
            "passed_requests", "failed_requests", "referenced_raw_files",
            "verified_passed_raw_files", "integrity_error_count", "orphan_raw_count",
        ):
            self.assertEqual(self.site["raw_integrity"][key], self.raw[key])

    def test_coverage_is_exact_and_unknown_is_not_non_crypto(self):
        self.assertEqual(self.site["coverage"], self.coverage["accounts"])
        for name, metrics in self.site["accounts"].items():
            classes = metrics["classification_trade_counts"]
            self.assertEqual(
                sum(classes.get(k, 0) for k in ("crypto", "non_crypto", "unknown")),
                metrics["trade_count"],
            )
        bonereaper = self.site["bonereaper_closed_positions_reproduction"]
        self.assertEqual(bonereaper["source_rows"], 500)
        self.assertEqual(bonereaper["duplicate_source_observations"], 4)
        self.assertEqual(bonereaper["normalized_rows"], 496)

    def test_charts_have_full_research_annotations_and_no_external_assets(self):
        chart_count = 0
        for page in PUBLIC.rglob("*.html"):
            parser = AuditParser()
            text = page.read_text()
            parser.feed(text)
            chart_count += parser.chart_audits
            self.assertFalse(parser.external_urls, (page, parser.external_urls))
            for audit in text.split('data-chart-audit="complete"')[1:]:
                fragment = audit[:2400]
                for label in ("Question", "Axes", "Unit", "Formula", "Timezone",
                              "Sample", "Coverage", "Source", "Read as", "Limitation"):
                    self.assertIn(label, fragment)
        self.assertGreaterEqual(chart_count, 11)

    def test_public_tree_contains_only_whitelisted_static_files(self):
        allowed = {".html", ".css", ".js", ".json"}
        for path in PUBLIC.rglob("*"):
            self.assertFalse(path.is_symlink(), path)
            if path.is_file():
                self.assertIn(path.suffix, allowed, path)
                self.assertNotIn(path.suffix, {".gz", ".db", ".sqlite", ".parquet", ".env"})
        public_json = list((PUBLIC / "data").glob("*.json"))
        self.assertEqual(public_json, [PUBLIC / "data/site-data.json"])

    def test_build_is_deterministic(self):
        before = {
            path.relative_to(PUBLIC).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in PUBLIC.rglob("*") if path.is_file()
        }
        subprocess.run(["python3", "src/build_site.py"], cwd=ROOT, check=True)
        after = {
            path.relative_to(PUBLIC).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in PUBLIC.rglob("*") if path.is_file()
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
