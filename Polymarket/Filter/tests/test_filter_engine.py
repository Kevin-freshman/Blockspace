import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from filter_engine import (  # noqa: E402
    apply_filter,
    build_address_metrics,
    normalize_trade,
    summarize_positions,
    trade_key,
)
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
            {"rank": "1", "proxyWallet": ADDRESS, "userName": "alpha", "pnl": 12},
            trades,
            markets,
            lookback_hours=24,
            now_timestamp=now,
        )
        self.assertEqual(metrics["trade_count"], 2)
        self.assertEqual(metrics["transaction_count"], 2)
        self.assertEqual(metrics["trades_per_day"], 2.0)
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

    def test_invalid_distance_range_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_scan_config(
                {
                    "min_median_hours_to_settlement": 10,
                    "max_median_hours_to_settlement": 1,
                },
                self.base,
            )


if __name__ == "__main__":
    unittest.main()
