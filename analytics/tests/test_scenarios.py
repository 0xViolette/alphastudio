import unittest
from datetime import datetime

from analytics.backtest import Bar, Fill
from analytics.scenarios import _standard_trade_log, make_scenario


class ScenarioTests(unittest.TestCase):
    def test_all_workflow_variants_map_to_the_expected_emitters(self):
        self.assertEqual(make_scenario("futures").emitter.name, "ohlc_csv_emitter.py")
        self.assertEqual(make_scenario("spot", ohlc="full").emitter.name, "spot_full_ohlc_emitter.py")
        self.assertEqual(make_scenario("spot", ohlc="close-only").emitter.name, "spot_close_only_emitter.py")
        self.assertEqual(make_scenario("synthetic", signals="futures").emitter.name, "ohlc_csv_emitter.py")
        self.assertEqual(make_scenario("synthetic", signals="options").emitter.name, "synthetic_ohlc_emitter.py")
        self.assertEqual(make_scenario("synthetic", signals="options", ohlc="extrema").emitter.name, "synthetic_ohlc_extrema_emitter.py")

    def test_output_slugs_include_material_variants(self):
        self.assertEqual(make_scenario("futures").slug, "futures")
        self.assertEqual(make_scenario("spot", ohlc="full").slug, "spot-full")
        self.assertEqual(make_scenario("spot", ohlc="close-only").slug, "spot-close-only")
        self.assertEqual(make_scenario("synthetic", signals="futures").slug, "synthetic-futures")
        self.assertEqual(make_scenario("synthetic", signals="options").slug, "synthetic-options")
        self.assertEqual(
            make_scenario("synthetic", signals="options", ohlc="extrema").slug,
            "synthetic-options-extrema",
        )

    def test_invalid_variant_combinations_are_rejected(self):
        with self.assertRaises(ValueError):
            make_scenario("spot", ohlc="extrema")
        with self.assertRaises(ValueError):
            make_scenario("synthetic", signals="futures", ohlc="extrema")

    def test_standard_trade_log_uses_actual_fills_and_splits_reversals(self):
        dt = datetime(2024, 1, 1, 10)
        bars = [Bar(dt, 100, (Fill(-1, 110, 150, 15, "SE"),))]
        entries = _standard_trade_log(bars)
        self.assertEqual([entry["action"] for entry in entries], ["OPEN"])
        bars.insert(0, Bar(dt, 100, (Fill(1, 100, 100, 10, "LE"),)))
        entries = _standard_trade_log(bars)
        self.assertEqual([entry["action"] for entry in entries], ["OPEN", "CLOSE", "OPEN"])
        self.assertEqual([entry["qty"] for entry in entries], [100, 100, 50])
        self.assertEqual([entry["fee"] for entry in entries], [10, 10, 5])
        self.assertEqual(sum(entry["fee"] for entry in entries), 25)


if __name__ == "__main__":
    unittest.main()
