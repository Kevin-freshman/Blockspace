import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from research_engine import (  # noqa: E402
    analyze_leaderboard_transition,
    build_hypothesis_ledger,
    build_research_snapshot,
    build_strategy_fingerprint,
    choose_research_cohorts,
    compare_enriched_cohorts,
    research_slot_epoch,
)
from research_store import ResearchStore  # noqa: E402
from service import FilterService  # noqa: E402


def board_row(rank, address_number, pnl, volume):
    return {
        "rank": str(rank),
        "proxyWallet": "0x%040x" % address_number,
        "userName": "user-%d" % address_number,
        "pnl": pnl,
        "vol": volume,
    }


class ResearchEngineTests(unittest.TestCase):
    def test_slot_is_floored_to_six_hour_boundary(self):
        self.assertEqual(research_slot_epoch(7 * 3600 + 12, 6), 6 * 3600)

    def test_transition_distinguishes_drop_from_missing_metric(self):
        previous = build_research_snapshot(
            0,
            1,
            "DAY",
            [board_row(1, 1, 10, 100), board_row(2, 2, 9, 90)],
            [board_row(1, 1, 10, 100), board_row(2, 2, 9, 90)],
        )
        current = build_research_snapshot(
            86400,
            86401,
            "DAY",
            [board_row(1, 2, 12, 110), board_row(2, 3, 8, 80)],
            [board_row(1, 1, -2, 120), board_row(2, 2, 12, 110)],
        )
        current["rank_checks"]["0x%040x" % 1] = {
            "address": "0x%040x" % 1,
            "rank": 5000,
            "pnl": -2.0,
            "volume": 120.0,
        }
        transition = analyze_leaderboard_transition(previous, current, 100)
        self.assertEqual(transition["entered_count"], 1)
        self.assertEqual(transition["retained_count"], 1)
        self.assertEqual(transition["dropped_count"], 1)
        dropped = next(row for row in transition["rows"] if row["state"] == "dropped")
        self.assertEqual(dropped["current_rank"], 5000)
        self.assertTrue(dropped["current_rank_exact"])
        self.assertEqual(dropped["visible_reason"], "current_pnl_non_positive")

    def test_volume_board_creates_matched_winner_loser_cohorts(self):
        previous = build_research_snapshot(
            0,
            1,
            "DAY",
            [board_row(index, index, 100 - index, 1000 - index) for index in range(1, 4)],
            [board_row(index, index, 100 - index, 1000 - index) for index in range(1, 4)],
        )
        volume = [
            board_row(1, 10, -5, 1000),
            board_row(2, 11, 5, 990),
            board_row(3, 12, -3, 500),
            board_row(4, 13, 3, 510),
        ]
        current = build_research_snapshot(
            86400,
            86401,
            "DAY",
            [board_row(1, 2, 10, 100), board_row(2, 4, 8, 80)],
            volume,
        )
        cohorts = choose_research_cohorts(previous, current, 100, 30)
        self.assertEqual(len(cohorts["volume_losers"]), 2)
        self.assertEqual(len(cohorts["volume_winners"]), 2)
        self.assertEqual(cohorts["volume_losers"][0], "0x%040x" % 10)
        self.assertEqual(cohorts["volume_winners"][0], "0x%040x" % 11)

    def test_strategy_fingerprint_keeps_fill_and_hash_counts_separate(self):
        transaction_hash = "0x" + "1" * 64
        trades = [
            {
                "timestamp": 1700000240,
                "conditionId": "0x" + "a" * 64,
                "transactionHash": transaction_hash,
                "eventSlug": "btc-updown-5m-1700000000",
                "side": "BUY",
                "price": 0.95,
                "usdcSize": 20,
            },
            {
                "timestamp": 1700000200,
                "conditionId": "0x" + "b" * 64,
                "transactionHash": transaction_hash,
                "eventSlug": "btc-updown-5m-1700000000",
                "side": "SELL",
                "price": 0.4,
                "usdcSize": 10,
            },
        ]
        result = build_strategy_fingerprint(
            trades, 1700000000, 1700000300, truncated=False
        )
        self.assertEqual(result["trade_count"], 2)
        self.assertEqual(result["transaction_count"], 1)
        self.assertEqual(result["records_per_transaction"], 2.0)
        self.assertEqual(result["market_count"], 2)
        self.assertEqual(result["high_price_buy_share"], 1.0)
        self.assertEqual(result["tail_60m_share"], 1.0)

    def test_finding_requires_seven_days_and_promotes_with_stable_effect(self):
        analyses = []
        first = ["0x%040x" % value for value in range(1, 31)]
        second = ["0x%040x" % value for value in range(101, 131)]
        for day in range(7):
            analyses.append(
                {
                    "comparisons": [
                        {
                            "id": "volume_loser_vs_winner",
                            "first_addresses": first,
                            "second_addresses": second,
                            "metrics": [
                                {
                                    "field": "trades_per_hour",
                                    "difference": 2.0 + day / 10.0,
                                },
                                {
                                    "field": "high_price_buy_share",
                                    "difference": 0.1,
                                },
                                {
                                    "field": "market_concentration",
                                    "difference": 0.1,
                                },
                            ],
                        }
                    ]
                }
            )
        ledger = build_hypothesis_ledger(analyses, 7, 30, 0.75)
        frequency = next(item for item in ledger if item["id"] == "frequency_tax")
        self.assertEqual(frequency["status"], "insight")
        self.assertGreater(frequency["bootstrap_95_low"], 0)

    def test_comparison_reports_group_medians(self):
        cohorts = {
            "volume_losers": ["a", "b"],
            "volume_winners": ["c", "d"],
            "dropped": [],
            "retained_control": [],
        }
        fingerprints = {
            "a": {"trades_per_hour": 4},
            "b": {"trades_per_hour": 6},
            "c": {"trades_per_hour": 1},
            "d": {"trades_per_hour": 3},
        }
        comparison = compare_enriched_cohorts(cohorts, fingerprints)[0]
        metric = next(item for item in comparison["metrics"] if item["field"] == "trades_per_hour")
        self.assertEqual(metric["first_median"], 5.0)
        self.assertEqual(metric["second_median"], 2.0)
        self.assertEqual(metric["difference"], 3.0)


class ResearchStoreAndServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temporary.name)
        self.config = json.loads(
            (PROJECT_DIR / "config.json").read_text(encoding="utf-8")
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_gzip_store_round_trip(self):
        store = ResearchStore(self.base_dir / "data", retention_days=90)
        snapshot = build_research_snapshot(
            0, 1, "DAY", [board_row(1, 1, 1, 2)], [board_row(1, 1, 1, 2)]
        )
        snapshot["analysis"] = {"formal": True, "comparisons": []}
        store.save(snapshot)
        self.assertEqual(store.load_epoch(0)["boards"]["PNL"][0]["pnl"], 1.0)
        self.assertTrue(store.load_latest_analyses(1)[0]["analysis"]["formal"])
        self.assertEqual(store.count(), 1)

    def test_public_summary_waits_for_exact_24_hour_pair(self):
        service = FilterService(
            self.base_dir,
            self.config,
            start_background=False,
        )
        current = build_research_snapshot(
            86400,
            86401,
            "DAY",
            [board_row(1, 1, 5, 50)],
            [board_row(1, 2, -1, 100)],
        )
        service.research_store.save(current)
        self.assertEqual(service.public_research_summary(100)["status"], "collecting")
        previous = build_research_snapshot(
            0,
            1,
            "DAY",
            [board_row(1, 2, 5, 50)],
            [board_row(1, 2, 5, 50)],
        )
        service.research_store.save(previous)
        summary = service.public_research_summary(100)
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["transition"]["entered_count"], 1)
        self.assertEqual(summary["transition"]["dropped_count"], 1)
        service.shutdown()


if __name__ == "__main__":
    unittest.main()
