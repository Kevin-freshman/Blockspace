import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from backtest_engine import (  # noqa: E402
    break_even_slippage,
    build_observations,
    normalize_hourly_market,
    portfolio_comparison,
    price_at_or_before,
    simulate_trade,
    summarize_trades,
)
from scripts.run_six_month_backtest import PublicMarketDataClient  # noqa: E402


class BacktestEngineTests(unittest.TestCase):
    def test_event_pagination_follows_cursor_after_short_page(self):
        client = PublicMarketDataClient()
        responses = [
            {"events": [{"id": "1"}], "next_cursor": "next"},
            {"events": [{"id": "2"}]},
        ]

        def fake_request(_method, _url, params=None, json_body=None):
            self.assertEqual(bool(params.get("after_cursor")), len(responses) == 1)
            return responses.pop(0)

        client._request = fake_request
        rows = client.resolved_hourly_events(1, "2026-01-01", "2026-02-01")
        self.assertEqual([row["id"] for row in rows], ["1", "2"])

    def test_normalize_requires_unambiguous_binary_resolution(self):
        event = {
            "id": "event-1",
            "markets": [
                {
                    "id": "market-1",
                    "conditionId": "0xabc",
                    "slug": "btc-hour",
                    "endDate": "2026-03-10T00:00:00Z",
                    "outcomes": '["Up", "Down"]',
                    "outcomePrices": '["0", "1"]',
                    "clobTokenIds": '["123", "456"]',
                    "feesEnabled": True,
                }
            ],
        }
        market = normalize_hourly_market(event, "BTC")
        self.assertEqual(market["winner_index"], 1)
        self.assertEqual(market["token_id"], "123")
        event["markets"][0]["outcomePrices"] = '["0.5", "0.5"]'
        self.assertIsNone(normalize_hourly_market(event, "BTC"))

    def test_price_lookup_never_looks_forward(self):
        history = [{"t": 100, "p": 0.6}, {"t": 161, "p": 0.9}]
        self.assertEqual(price_at_or_before(history, 160), (0.6, 60))
        self.assertIsNone(price_at_or_before(history, 250, max_staleness_seconds=80))

    def test_observation_uses_binary_complement_for_favorite(self):
        market = {
            "asset": "BTC",
            "market_id": "1",
            "condition_id": "0x1",
            "slug": "btc",
            "end_at": "1970-01-01T00:33:20+00:00",
            "end_timestamp": 2000,
            "token_id": "123",
            "winner_index": 1,
        }
        rows = build_observations(
            [market], {"123": [{"t": 1935, "p": 0.1}]}, [1]
        )
        self.assertEqual(rows[0]["favorite_index"], 1)
        self.assertAlmostEqual(rows[0]["favorite_price"], 0.9)
        self.assertTrue(rows[0]["won"])

    def test_taker_cost_and_slippage_are_charged(self):
        observation = {
            "favorite_price": 0.9,
            "won": True,
            "end_timestamp": 1,
            "asset": "BTC",
            "date": "2026-01-01",
            "price_age_seconds": 1,
        }
        trade = simulate_trade(observation, 0.9, fee_rate=0.07, slippage=0.01)
        self.assertAlmostEqual(trade["entry_price"], 0.91)
        self.assertAlmostEqual(trade["fee"], 0.0063)
        self.assertLess(trade["pnl"], 1 / 0.91 - 1)
        self.assertIsNone(simulate_trade(observation, 0.95, 0.07, 0.01))

    def test_break_even_slippage_finds_cost_boundary(self):
        observations = []
        for index in range(100):
            observations.append(
                {
                    "favorite_price": 0.9,
                    "won": index < 93,
                    "end_timestamp": index,
                    "asset": "BTC",
                    "date": "2026-01-%02d" % (index % 28 + 1),
                    "window_minutes": 5,
                    "price_age_seconds": 1,
                }
            )
        boundary = break_even_slippage(observations, 5, 0.9, 0.0)
        self.assertGreater(boundary, 0)
        self.assertLess(boundary, 0.05)

    def test_tail_loss_reports_wins_needed(self):
        base = {
            "end_timestamp": 1,
            "asset": "BTC",
            "date": "2026-01-01",
            "price_age_seconds": 1,
        }
        observations = []
        for index in range(20):
            row = dict(base, favorite_price=0.95, won=index != 19)
            row["end_timestamp"] = index
            observations.append(simulate_trade(row, 0.9, 0.07, 0.0))
        summary = summarize_trades(observations, bootstrap_samples=0)
        self.assertEqual(summary["trade_count"], 20)
        self.assertEqual(summary["losses"], 1)
        self.assertGreater(summary["wins_needed_per_average_loss"], 18)

    def test_portfolio_holds_total_hourly_notional_constant(self):
        observations = []
        for asset, won in (("BTC", False), ("ETH", True), ("SOL", True)):
            observations.append(
                {
                    "asset": asset,
                    "favorite_price": 0.9,
                    "won": won,
                    "end_timestamp": 100,
                    "date": "2026-01-01",
                    "window_minutes": 5,
                    "price_age_seconds": 1,
                }
            )
        result = portfolio_comparison(observations, 5, 0.9, 0.0, 0.0)
        self.assertEqual(result["diversified_equal_weight"]["active_hours"], 1)
        self.assertGreater(
            result["diversified_equal_weight"]["total_pnl_per_one_usd_hour"],
            result["concentrated_btc"]["total_pnl_per_one_usd_hour"],
        )
        self.assertEqual(
            result["pairwise_signal_pnl_correlations"][0]["shared_signal_hours"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
