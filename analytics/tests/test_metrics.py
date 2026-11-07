import unittest

from analytics.ledger import replay
from analytics.metrics import analyze

from .fixtures import account_bars


class PerformanceCharacterizationTests(unittest.TestCase):
    def test_first_session_accounting_and_source_benchmark(self):
        bars = account_bars()
        benchmark = [10_000 * bar.close / bars[0].close for bar in bars]
        result = analyze(
            bars,
            initial=10_000,
            strategy="fixture",
            data="fixture.csv",
            cost_model="fixture costs",
            benchmark_equity=benchmark,
        )
        values = result.metrics
        self.assertEqual(result.day_equity, [11975, 11460])
        self.assertEqual(result.returns[0], 0.1975)
        self.assertAlmostEqual(result.returns[1], 11460 / 11975 - 1)
        self.assertEqual(values["final"], 11460)
        self.assertAlmostEqual(values["total"], 0.146)
        self.assertEqual(values["total_fees"], 40)
        self.assertEqual(values["n_trades"], 3)
        self.assertEqual(values["exposure"], 0.8)
        self.assertEqual(values["maxdd"], -1510)
        self.assertAlmostEqual(values["maxdd_pct"], -1510 / 11975)
        self.assertEqual(result.worst_i, 2)
        self.assertAlmostEqual(values["monthly"][(2024, 1)], 0.146)
        self.assertAlmostEqual(values["annual"][2024], 0.146)
        self.assertEqual(values["monthly_points"][(2024, 1)], 15)
        self.assertEqual(result.benchmark_equity, benchmark)
        self.assertIsNotNone(values["bench"])

    def test_trade_and_period_statistics_remain_distinct(self):
        bars = account_bars()
        result = analyze(
            bars,
            account=replay(bars, 10_000),
            initial=10_000,
            strategy="fixture",
            data="fixture.csv",
            cost_model="fixture costs",
        )
        self.assertEqual(result.metrics["trades"]["n"], 3)
        self.assertAlmostEqual(result.metrics["trades"]["win_rate"], 2 / 3)
        self.assertEqual(result.metrics["pos_days"], 1)
        self.assertEqual(result.metrics["n_days"], 2)


if __name__ == "__main__":
    unittest.main()
