import copy
import unittest
from datetime import datetime

from analytics.backtest import Bar, Fill
from analytics.costs import schema, synthetic_schema
from analytics.synthetic import (
    OptionCostModel,
    _fill_cost,
    _retime_expiry_stop_bars,
    build_expiry_calendar,
    buy_and_hold,
    make_option_lookup,
    nominal_monthly_expiry,
    synthetic_bars_with_rollover,
)


class SyntheticCharacterizationTests(unittest.TestCase):
    def test_filtered_schema_does_not_mutate_shared_schema(self):
        original = schema()
        before = copy.deepcopy(original)
        filtered = synthetic_schema()
        self.assertEqual(original, before)
        self.assertEqual([p["name"] for p in filtered["presets"]], ["none", "costs"])
        self.assertEqual(filtered["default_preset"], "costs")
        self.assertEqual(
            {knob["key"] for knob in filtered["knobs"]},
            {"fee_per_order", "opt_slip_ticks", "opt_slip_pct"},
        )

    def test_option_lookup_is_same_session_and_latest(self):
        times = [
            datetime(2026, 8, 25, 9, 15),
            datetime(2026, 8, 25, 9, 16),
            datetime(2026, 8, 26, 9, 15),
        ]
        lookup = make_option_lookup(times, [1, 2, 3], [100, 100, 100], [10, 20, 30], [5, 6, 7])
        self.assertEqual(lookup(datetime(2026, 8, 25, 9, 16)), (2, 100, 20, 6))
        self.assertIsNone(lookup(datetime(2026, 8, 25, 9, 14)))
        self.assertIsNone(lookup(datetime(2026, 8, 26, 9, 14)))

    def test_option_costs_use_actual_leg_prices_and_unit_quantities(self):
        meta = synthetic_schema()
        model = OptionCostModel(meta)
        rates = meta["rates"]
        cost = model.futures_cost(1, 100, 80, 2, 65)
        self.assertAlmostEqual(cost["call_price"], 100.55)
        self.assertAlmostEqual(cost["put_price"], 79.55)
        self.assertAlmostEqual(cost["slippage"], 1.0)
        call_turnover = 100.55 * 2 * 65
        call_fixed = 20.0 + rates["nse_options_txn_bps"] * 1e-4 * call_turnover + rates["sebi_bps"] * 1e-4 * call_turnover
        call_fee = call_fixed + rates["stamp_options_bps"] * 1e-4 * call_turnover + rates["gst"] * call_fixed
        put_turnover = 79.55 * 2 * 65
        put_fixed = 20.0 + rates["nse_options_txn_bps"] * 1e-4 * put_turnover + rates["sebi_bps"] * 1e-4 * put_turnover
        put_fee = put_fixed + rates["stt_options_bps"] * 1e-4 * put_turnover + rates["gst"] * put_fixed
        self.assertAlmostEqual(cost["fee"], call_fee + put_fee)
        _, fee, _ = _fill_cost(model, 1, (0, 10000, 100, 80), 130, 65)
        self.assertAlmostEqual(fee, cost["fee"])

    def test_expiry_rules_and_holiday_adjustment(self):
        self.assertEqual(
            nominal_monthly_expiry(2025, 8, datetime(2025, 8, 1).date()),
            datetime(2025, 8, 28).date(),
        )
        self.assertEqual(
            nominal_monthly_expiry(2026, 8, datetime(2026, 8, 1).date()),
            datetime(2026, 8, 25).date(),
        )
        trading_dates = {datetime(2026, 8, 24).date(), datetime(2026, 8, 26).date()}
        expiries = build_expiry_calendar(
            trading_dates, datetime(2026, 8, 1).date(), datetime(2026, 8, 1).date()
        )
        self.assertEqual(expiries[0], datetime(2026, 8, 24).date())

    def test_positioned_rollover_and_benchmark_return_suppression(self):
        expiry = datetime(2026, 8, 25).date()
        bars = [
            Bar(datetime(2026, 8, 25, 15, 0), 9990, (Fill(1, 9990, 2, 0, "LE"),)),
            Bar(datetime(2026, 8, 25, 15, 30), 10000),
        ]
        out, closes, events, trade_log = synthetic_bars_with_rollover(
            bars,
            lambda _: (0, 10000, 100, 80),
            datetime(2026, 8, 25, 9, 15),
            datetime(2026, 8, 25, 15, 30),
            [expiry],
            None,
            65,
            lambda _: (0, 10100, 110, 90),
        )
        self.assertEqual([bar.dt.strftime("%H:%M") for bar in out], ["15:00", "15:20", "15:21", "15:30"])
        self.assertEqual(closes, [10000, 10000, 10100, 10100])
        self.assertEqual([out[1].fills[0].side, out[2].fills[0].side], [-1, 1])
        self.assertEqual([event["action"] for event in events], ["square_off", "rollover"])
        self.assertEqual([entry["action"] for entry in trade_log], ["LE", "ROLLOVER_SQUAREOFF", "ROLLOVER_REENTRY"])
        self.assertEqual(buy_and_hold(out, closes, 10000, events), [10000] * 4)

    def test_flat_contract_switch_has_no_fills(self):
        expiry = datetime(2026, 8, 25).date()
        out, _, events, trade_log = synthetic_bars_with_rollover(
            [Bar(datetime(2026, 8, 25, 15, 30), 10000)],
            lambda _: (0, 10000, 100, 80),
            datetime(2026, 8, 25, 9, 15),
            datetime(2026, 8, 25, 15, 30),
            [expiry],
            None,
            65,
            lambda _: (0, 10100, 110, 90),
        )
        self.assertEqual(len(out), 3)
        self.assertTrue(all(not bar.fills for bar in out))
        self.assertEqual([event["action"] for event in events], ["contract_switch"])
        self.assertEqual(trade_log, [])

    def test_expiry_stop_is_retimed_to_first_minute_crossing(self):
        expiry = datetime(2026, 8, 25).date()
        end = datetime(2026, 8, 25, 15, 30)
        bars = [Bar(end, 100, (Fill(-1, 105, 1, 0, "SL"),))]
        minutes = [(datetime(2026, 8, 25, 15, 1), 104), (end, 100)]
        out = _retime_expiry_stop_bars(bars, [expiry], minutes)
        self.assertEqual(out[0].dt, datetime(2026, 8, 25, 15, 1))
        self.assertEqual(out[0].close, 104)
        self.assertEqual(out[1].fills, ())

    def test_option_execution_uses_actual_bar_close_minute(self):
        previous = datetime(2026, 8, 3, 9, 59)
        dt = datetime(2026, 8, 3, 10, 0)
        lookup = make_option_lookup(
            [previous, dt], [0, 0], [100, 101], [10, 11], [5, 6]
        )
        out, _, _, _ = synthetic_bars_with_rollover(
            [Bar(dt, 100, open=99, high=102, low=98)],
            lookup,
            previous,
            dt,
            [],
            None,
            65,
            None,
            [(previous, 100.0), (dt, 101.0)],
        )
        self.assertEqual(out[0].close, 100)


class SyntheticChartTests(unittest.TestCase):
    def test_close_adjustment_and_month2_boundaries(self):
        from analytics.synthetic import make_chart_bars

        dt = datetime(2026, 8, 10, 10, 0)
        source = Bar(
            dt, 105, open=100, high=110, low=90,
            probes={"le": 110.05, "se": 89.95},
        )
        bar = make_chart_bars([source], [Bar(dt, 106)], [], [(dt, 57000.0)])[0]
        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (101, 111, 91, 106))
        self.assertEqual(bar.probes, source.probes)

        expiry = datetime(2026, 8, 25, 15, 30)
        source = Bar(expiry, 21700, open=21680, high=21720, low=21660)
        events = [{"date": datetime(2026, 8, 25, 15, 21), "action": "contract_switch"}]
        minutes = [
            (datetime(2026, 8, 25, 15, 1), 21950),
            (datetime(2026, 8, 25, 15, 29), 22050),
            (expiry, 22030),
        ]
        bar = make_chart_bars([source], [Bar(expiry, 22030)], events, minutes)[0]
        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (21950, 22050, 21950, 22030))

    def test_rollover_times_are_unique_and_strategy_tags_survive(self):
        from analytics.synthetic import make_chart_bars

        first = datetime(2026, 8, 25, 15, 20)
        second = datetime(2026, 8, 25, 15, 21)
        end = datetime(2026, 8, 25, 15, 30)
        synthetic = [
            Bar(first, 104, (Fill(-1, 105, 1, 0, "2NL"),)),
            Bar(first, 100, (Fill(-1, 100, 1, 0),)),
            Bar(second, 101, (Fill(-1, 101, 1, 0),)),
            Bar(end, 101),
        ]
        events = [
            {"date": first, "action": "square_off", "side": -1},
            {"date": second, "action": "rollover", "side": -1},
        ]
        bars = make_chart_bars(
            [Bar(end, 100, open=99, high=102, low=98)],
            synthetic,
            events,
            [(end, 101.0)],
        )
        times = [bar.dt for bar in bars]
        self.assertTrue(all(a < b for a, b in zip(times, times[1:])))
        self.assertEqual(bars[0].fills[0].tag, "2NL")
        self.assertEqual(bars[1].fills[0].tag, "Short rollover exit")
        self.assertEqual(bars[2].fills[0].tag, "Short rollover entry")


if __name__ == "__main__":
    unittest.main()
