import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from filter_engine import (  # noqa: E402
    apply_chain_filter,
    apply_filter,
    build_address_metrics,
    enrich_trade_onchain,
    normalize_trade,
    summarize_chain_receipts,
    summarize_positions,
    trade_key,
)
from polygon_client import normalize_receipt  # noqa: E402
from polymarket_client import PolymarketClient  # noqa: E402
from service import validate_scan_config  # noqa: E402


ADDRESS = "0x1111111111111111111111111111111111111111"
CONDITION_A = "0x" + "a" * 64
CONDITION_B = "0x" + "b" * 64


class FilterEngineTests(unittest.TestCase):
    def test_metrics_count_frequency_and_settlement_distance(self):
        now = 2_000_000
        trades = [
            self._trade(now - 600, CONDITION_A, "0x" + "1" * 64),
            self._trade(now - 1200, CONDITION_B, "0x" + "2" * 64),
            self._trade(now - 100_000, CONDITION_A, "0x" + "3" * 64),
        ]
        markets = {
            CONDITION_A: {"end_timestamp": now - 600 + 3600},
            CONDITION_B: {"end_timestamp": now - 1200 + 7200},
        }
        metrics, normalized = build_address_metrics(
            {
                "rank": "1",
                "proxyWallet": ADDRESS,
                "userName": "alpha",
                "pnl": 12,
                "vol": 100,
            },
            trades,
            markets,
            lookback_hours=24,
            now_timestamp=now,
        )
        self.assertEqual(metrics["trade_count"], 2)
        self.assertEqual(metrics["transaction_count"], 2)
        self.assertEqual(metrics["trades_per_day"], 2.0)
        self.assertEqual(metrics["return_efficiency"], 0.12)
        self.assertEqual(metrics["estimated_pnl_per_activity"], 6.0)
        self.assertEqual(metrics["median_hours_to_settlement"], 1.5)
        self.assertEqual(metrics["settlement_coverage"], 1.0)
        self.assertEqual(len(normalized), 2)

    def test_filter_requires_known_distance_when_distance_is_enabled(self):
        base = {
            "trade_count": 20,
            "trades_per_day": 10,
            "median_hours_to_settlement": None,
        }
        result = apply_filter(
            base,
            {
                "min_trade_count": 1,
                "min_trades_per_day": 1,
                "min_median_hours_to_settlement": 0,
                "max_median_hours_to_settlement": 24,
            },
        )
        self.assertFalse(result["passes"])
        self.assertIn("settlement_distance_unknown", result["filter_reasons"])

    def test_filter_can_skip_distance(self):
        result = apply_filter(
            {
                "trade_count": 20,
                "trades_per_day": 10,
                "median_hours_to_settlement": None,
            },
            {
                "min_trade_count": 1,
                "min_trades_per_day": 1,
                "min_median_hours_to_settlement": None,
                "max_median_hours_to_settlement": None,
            },
        )
        self.assertTrue(result["passes"])

    def test_trade_key_keeps_multiple_fills_in_one_transaction(self):
        first = self._trade(100, CONDITION_A, "0x" + "1" * 64)
        second = dict(first, asset="other", size=12)
        self.assertNotEqual(trade_key(first), trade_key(second))

    def test_optional_return_efficiency_filter(self):
        result = apply_filter(
            {
                "trade_count": 5,
                "trades_per_day": 5,
                "return_efficiency": 0.08,
                "median_hours_to_settlement": None,
            },
            {
                "min_trade_count": 0,
                "min_trades_per_day": 1,
                "min_return_efficiency": 0.1,
                "min_median_hours_to_settlement": None,
                "max_median_hours_to_settlement": None,
            },
        )
        self.assertFalse(result["passes"])
        self.assertIn("return_efficiency", result["filter_reasons"])

    def test_estimated_pnl_per_activity_is_unknown_when_activity_is_capped(self):
        metrics, _ = build_address_metrics(
            {
                "rank": "1",
                "proxyWallet": ADDRESS,
                "pnl": 12,
                "vol": 100,
            },
            [self._trade(2_000_000 - 600, CONDITION_A, "0x" + "1" * 64)],
            {},
            lookback_hours=24,
            now_timestamp=2_000_000,
            truncated=True,
        )
        self.assertIsNone(metrics["estimated_pnl_per_activity"])

    def test_optional_estimated_pnl_per_activity_filter(self):
        result = apply_filter(
            {
                "trade_count": 5,
                "trades_per_day": 5,
                "return_efficiency": 0.08,
                "estimated_pnl_per_activity": 1.25,
                "median_hours_to_settlement": None,
            },
            {
                "min_trade_count": 0,
                "min_trades_per_day": 1,
                "min_return_efficiency": None,
                "min_estimated_pnl_per_activity": 2,
                "min_median_hours_to_settlement": None,
                "max_median_hours_to_settlement": None,
            },
        )
        self.assertFalse(result["passes"])
        self.assertIn("estimated_pnl_per_activity", result["filter_reasons"])

    def test_normalize_trade_preserves_signed_distance(self):
        trade = self._trade(1000, CONDITION_A, "0x" + "1" * 64)
        row = normalize_trade(trade, {"end_timestamp": 700})
        self.assertEqual(row["minutes_to_settlement"], -5.0)

    def test_position_summary(self):
        result = summarize_positions(
            [
                {"currentValue": "10.5", "cashPnl": 2},
                {"currentValue": 5, "cashPnl": "-1"},
            ]
        )
        self.assertEqual(result["open_position_count"], 2)
        self.assertEqual(result["open_position_value"], 15.5)
        self.assertEqual(result["open_position_cash_pnl"], 1.0)

    def test_chain_receipt_metrics_and_filter(self):
        first_hash = "0x" + "1" * 64
        second_hash = "0x" + "2" * 64
        contract = "0x" + "c" * 40
        metrics = summarize_chain_receipts(
            [first_hash, second_hash],
            {
                first_hash: {
                    "status": 1,
                    "block_number": 123,
                    "to": "0x" + "d" * 40,
                    "log_count": 3,
                    "log_addresses": [contract],
                },
                second_hash: {"missing": True},
            },
            [contract],
        )
        self.assertEqual(metrics["chain_confirmed_count"], 1)
        self.assertEqual(metrics["chain_verification_rate"], 0.5)
        self.assertEqual(metrics["polymarket_contract_hit_count"], 1)
        result = apply_chain_filter(
            dict(metrics, filter_reasons=[]),
            {
                "enabled": True,
                "min_confirmed_transactions": 1,
                "min_verification_rate": 0.5,
                "min_polymarket_contract_rate": 0.5,
            },
        )
        self.assertTrue(result["passes"])

    def test_trade_receipt_enrichment(self):
        contract = "0x" + "c" * 40
        enriched = enrich_trade_onchain(
            {"transaction_hash": "0x" + "1" * 64},
            {
                "status": 1,
                "block_number": 123,
                "to": contract,
                "log_addresses": [],
            },
            [contract],
        )
        self.assertEqual(enriched["onchain_status"], "confirmed")
        self.assertTrue(enriched["onchain_polymarket_contract"])

    @staticmethod
    def _trade(timestamp, condition_id, transaction_hash):
        return {
            "proxyWallet": ADDRESS,
            "timestamp": timestamp,
            "conditionId": condition_id,
            "transactionHash": transaction_hash,
            "asset": "asset",
            "side": "BUY",
            "size": 10,
            "price": 0.5,
            "title": "Market",
        }


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        import json

        self.base = json.loads((PROJECT_DIR / "config.json").read_text(encoding="utf-8"))

    def test_browser_overrides_are_bounded_and_normalized(self):
        result = validate_scan_config(
            {
                "time_period": "month",
                "order_by": "vol",
                "candidate_limit": 12,
                "max_median_hours_to_settlement": "",
                "poll_seconds": 20,
            },
            self.base,
        )
        self.assertEqual(result["leaderboard"]["time_period"], "MONTH")
        self.assertEqual(result["leaderboard"]["order_by"], "VOL")
        self.assertEqual(result["leaderboard"]["candidate_limit"], 12)
        self.assertIsNone(result["filter"]["max_median_hours_to_settlement"])

    def test_return_filter_and_sort_are_normalized(self):
        result = validate_scan_config(
            {
                "min_return_efficiency": "0.125",
                "min_estimated_pnl_per_activity": "2.5",
                "result_sort": "avg_pnl",
            },
            self.base,
        )
        self.assertEqual(result["filter"]["min_return_efficiency"], 0.125)
        self.assertEqual(result["filter"]["min_estimated_pnl_per_activity"], 2.5)
        self.assertEqual(result["filter"]["result_sort"], "AVG_PNL")

    def test_large_candidate_pool_and_chain_controls(self):
        result = validate_scan_config(
            {
                "candidate_limit": 1000,
                "chain_receipts_per_address": 12,
                "min_chain_confirmed_transactions": 2,
                "min_chain_verification_rate": 0.75,
                "min_polymarket_contract_rate": 0.25,
            },
            self.base,
        )
        self.assertEqual(result["leaderboard"]["candidate_limit"], 1000)
        self.assertEqual(result["chain"]["receipts_per_address"], 12)
        self.assertEqual(result["chain"]["min_verification_rate"], 0.75)

    def test_invalid_distance_range_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_scan_config(
                {
                    "min_median_hours_to_settlement": 10,
                    "max_median_hours_to_settlement": 1,
                },
                self.base,
            )


class ClientTests(unittest.TestCase):
    def test_leaderboard_paginates_in_pages_of_fifty(self):
        class FakeClient(PolymarketClient):
            def __init__(self):
                self.calls = []

            def _get(self, _url, params):
                self.calls.append(dict(params))
                return [
                    {
                        "rank": str(index + 1),
                        "proxyWallet": "0x%040x" % (index + 1),
                    }
                    for index in range(params["offset"], params["offset"] + params["limit"])
                ]

        client = FakeClient()
        rows = client.leaderboard("WEEK", "PNL", 125)
        self.assertEqual(len(rows), 125)
        self.assertEqual([call["offset"] for call in client.calls], [0, 50, 100])
        self.assertEqual([call["limit"] for call in client.calls], [50, 50, 25])

    def test_polygon_receipt_normalization(self):
        transaction_hash = "0x" + "1" * 64
        receipt = normalize_receipt(
            {
                "transactionHash": transaction_hash,
                "status": "0x1",
                "blockNumber": "0x10",
                "to": "0x" + "a" * 40,
                "logs": [{"address": "0x" + "b" * 40}],
            }
        )
        self.assertEqual(receipt["status"], 1)
        self.assertEqual(receipt["block_number"], 16)
        self.assertEqual(receipt["log_count"], 1)


if __name__ == "__main__":
    unittest.main()
