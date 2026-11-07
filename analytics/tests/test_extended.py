import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from analytics.metrics import (
    analyze,
    benchmark_daily_returns,
    distribution_analysis,
    eoy_comparison,
    rolling_analysis,
    worst_drawdowns,
)

from .fixtures import account_bars


def extended_fixture():
    dates = [datetime(2024, 1, 1) + timedelta(days=index) for index in range(130)]
    days = [dt.date() for dt in dates]
    returns = [0.01 if index % 3 else -0.004 for index in range(130)]
    benchmark_returns = [0.006 if index % 4 else -0.003 for index in range(130)]
    initial = 10_000

    def equity_curve(values):
        equity = []
        current = initial
        for value in values:
            current *= 1 + value
            equity.append(current)
        return equity

    return SimpleNamespace(
        metrics={"initial": initial, "ppy": 252, "rf": 0.0, "mar": 0.0,
                 "annual": {2024: 0.25}, "monthly": {(2024, 1): 0.05}},
        days=days,
        returns=returns,
        dates=dates,
        equity=equity_curve(returns),
        benchmark_equity=equity_curve(benchmark_returns),
    )


class ExtendedAnalyticsTests(unittest.TestCase):
    def test_rolling_metrics_have_warmup_and_aligned_benchmark(self):
        performance = extended_fixture()
        rolling = rolling_analysis(performance)
        self.assertIsNone(rolling["sharpe"][124])
        self.assertIsNotNone(rolling["sharpe"][125])
        self.assertGreater(rolling["volatility"][125], 0)
        self.assertIsNotNone(rolling["beta"][125])
        benchmark = benchmark_daily_returns(performance)
        self.assertEqual(len(benchmark), len(performance.days))

    def test_eoy_comparison_and_worst_drawdown_rows(self):
        rows = eoy_comparison(extended_fixture())
        self.assertEqual(rows[0]["year"], 2024)
        self.assertIsNotNone(rows[0]["benchmark"])
        self.assertEqual(
            rows[0]["won"], rows[0]["strategy"] > rows[0]["benchmark"]
        )

        bars = account_bars()
        performance = analyze(
            bars,
            initial=10_000,
            strategy="fixture",
            data="fixture",
            cost_model="fixture",
        )
        drawdowns = worst_drawdowns(performance)
        self.assertEqual(len(drawdowns), 1)
        self.assertEqual(drawdowns[0]["start"], "2024-01-01")
        self.assertEqual(drawdowns[0]["recovered"], "Open")

    def test_daily_and_monthly_histograms_preserve_counts(self):
        distribution = distribution_analysis(extended_fixture())
        self.assertEqual(sum(bin_["count"] for bin_ in distribution["daily"]), 130)
        self.assertTrue(all(bin_["density"] >= 0 for bin_ in distribution["daily"]))
        self.assertIn("monthly", distribution)


if __name__ == "__main__":
    unittest.main()
