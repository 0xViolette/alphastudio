import copy
import unittest
from datetime import datetime

import costs
import synthetic
import synthetic_viz
from engine import Bar, Fill


class SyntheticTests(unittest.TestCase):
    def test_filtered_schema_does_not_mutate_shared_schema(self):
        original = costs.schema()
        before = copy.deepcopy(original)
        filtered = synthetic.cost_schema()
        self.assertEqual(original, before)
        self.assertEqual(
            [preset["name"] for preset in filtered["presets"]], ["none", "costs"]
        )
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
        lookup = synthetic.make_option_lookup(
            times, [1, 2, 3], [100, 100, 100], [10, 20, 30], [5, 6, 7]
        )
        self.assertEqual(
            lookup(datetime(2026, 8, 25, 9, 16)), (2, 100, 20, 6)
        )
        self.assertIsNone(lookup(datetime(2026, 8, 25, 9, 14)))
        self.assertIsNone(lookup(datetime(2026, 8, 26, 9, 14)))

    def test_option_costs_use_actual_leg_prices(self):
        meta = synthetic.cost_schema()
        model = synthetic.OptionCostModel(meta)
        rates = meta["rates"]
        cost = model.futures_cost(1, 100, 80, 2, 65)
        self.assertAlmostEqual(cost["call_price"], 100.55)
        self.assertAlmostEqual(cost["put_price"], 79.55)
        self.assertAlmostEqual(cost["slippage"], 1.0)

        call_turnover = 100.55 * 2 * 65
        call_brokerage = 20.0
        call_txn = rates["nse_options_txn_bps"] * 1e-4 * call_turnover
        call_sebi = rates["sebi_bps"] * 1e-4 * call_turnover
        call_fee = (
            call_brokerage + call_txn + call_sebi
            + rates["stamp_options_bps"] * 1e-4 * call_turnover
            + rates["gst"] * (call_brokerage + call_txn + call_sebi)
        )
        put_turnover = 79.55 * 2 * 65
        put_brokerage = 20.0
        put_txn = rates["nse_options_txn_bps"] * 1e-4 * put_turnover
        put_sebi = rates["sebi_bps"] * 1e-4 * put_turnover
        put_fee = (
            put_brokerage + put_txn + put_sebi
            + rates["stt_options_bps"] * 1e-4 * put_turnover
            + rates["gst"] * (put_brokerage + put_txn + put_sebi)
        )
        self.assertAlmostEqual(cost["fee"], call_fee + put_fee)
        self.assertAlmostEqual(cost["breakup"]["slippage_call"], 0.55)
        self.assertAlmostEqual(cost["breakup"]["slippage_put"], 0.45)

    def test_expiry_rules_and_holiday_adjustment(self):
        self.assertEqual(
            synthetic.nominal_monthly_expiry(2025, 8, datetime(2025, 8, 1).date()),
            datetime(2025, 8, 28).date(),
        )
        self.assertEqual(
            synthetic.nominal_monthly_expiry(2026, 8, datetime(2026, 8, 1).date()),
            datetime(2026, 8, 25).date(),
        )
        trading_dates = {
            datetime(2026, 8, 24).date(),
            datetime(2026, 8, 26).date(),
        }
        expiries = synthetic.build_expiry_calendar(
            trading_dates,
            datetime(2026, 8, 1).date(),
            datetime(2026, 8, 1).date(),
        )
        self.assertEqual(expiries[0], datetime(2026, 8, 24).date())

    def test_positioned_rollover_and_benchmark_return_suppression(self):
        expiry = datetime(2026, 8, 25).date()
        bars = [
            Bar(
                datetime(2026, 8, 25, 15, 0), 9990,
                (Fill(1, 9990, 2, 0, "LE"),),
            ),
            Bar(datetime(2026, 8, 25, 15, 30), 10000),
        ]
        front = lambda _: (0, 10000, 100, 80)
        next_month = lambda _: (0, 10100, 110, 90)
        out, closes, events, trade_log = synthetic.synthetic_bars_with_rollover(
            bars,
            front,
            datetime(2026, 8, 25, 9, 15),
            datetime(2026, 8, 25, 15, 30),
            [expiry],
            None,
            65,
            next_month,
        )
        self.assertEqual(
            [bar.dt.strftime("%H:%M") for bar in out],
            ["15:00", "15:20", "15:21", "15:30"],
        )
        self.assertEqual(closes, [10000, 10000, 10100, 10100])
        self.assertEqual(out[0].fills[0].price, 10000)
        self.assertEqual(out[1].fills[0].side, -1)
        self.assertEqual(out[1].fills[0].price, 10000)
        self.assertEqual(out[2].fills[0].side, 1)
        self.assertEqual(out[2].fills[0].price, 10100)
        self.assertEqual(
            [event["action"] for event in events], ["square_off", "rollover"]
        )
        self.assertEqual(len(trade_log), 3)
        self.assertEqual(
            [entry["action"] for entry in trade_log],
            ["LE", "ROLLOVER_SQUAREOFF", "ROLLOVER_REENTRY"],
        )
        self.assertEqual(
            [entry["strike_price"] for entry in trade_log],
            [10000, 10000, 10100],
        )
        self.assertEqual(
            synthetic.buy_and_hold(out, closes, 10000, events),
            [10000, 10000, 10000, 10000],
        )

    def test_trade_log_csv_includes_strike_price_and_ocaml_actions(self):
        import csv
        import tempfile
        from pathlib import Path

        dt = datetime(2026, 8, 3, 10, 0)
        _, _, _, trade_log = synthetic.synthetic_bars_with_rollover(
            [Bar(dt, 100, (
                Fill(1, 100, 1, 0, "LE"),
                Fill(-1, 100, 1, 0, "2NL"),
            ))],
            lambda _: (0, 10000, 100, 80),
            dt,
            dt,
            [],
            None,
            65,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            synthetic.write_synthetic_trade_log(path, trade_log)
            with path.open(newline="") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual([row["action"] for row in rows], ["LE", "2NL"])
        self.assertEqual(
            [row["strike_price"] for row in rows], ["10000.00", "10000.00"]
        )

    def test_flat_contract_switch_has_no_fills(self):
        expiry = datetime(2026, 8, 25).date()
        bars = [Bar(datetime(2026, 8, 25, 15, 30), 10000)]
        out, _, events, trade_log = synthetic.synthetic_bars_with_rollover(
            bars,
            lambda _: (0, 10000, 100, 80),
            datetime(2026, 8, 25, 9, 15),
            datetime(2026, 8, 25, 15, 30),
            [expiry],
            None,
            65,
            lambda _: (0, 10100, 110, 90),
        )
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0].fills, ())
        self.assertEqual(out[1].fills, ())
        self.assertEqual([event["action"] for event in events], ["contract_switch"])
        self.assertEqual(trade_log, [])

    def test_expiry_stop_is_retimed_to_first_minute_crossing(self):
        expiry = datetime(2026, 8, 25).date()
        bar_end = datetime(2026, 8, 25, 15, 30)
        bars = [Bar(bar_end, 100, (Fill(-1, 105, 1, 0, "SL"),))]
        minute_bars = [
            (datetime(2026, 8, 25, 15, 1), 104),
            (bar_end, 100),
        ]
        out = synthetic._retime_expiry_stop_bars(bars, [expiry], minute_bars)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].dt, datetime(2026, 8, 25, 15, 1))
        self.assertEqual(out[0].close, 104)
        self.assertEqual(out[0].fills[0].tag, "SL")
        self.assertEqual(out[1].dt, bar_end)
        self.assertEqual(out[1].fills, ())

    def test_chart_close_adjustment_does_not_select_month2(self):
        dt = datetime(2026, 8, 10, 10, 0)
        source = Bar(
            dt, 105, open=100, high=110, low=90,
            probes={"le": 110.05, "se": 89.95},
        )
        synth = Bar(dt, 106)
        bars = synthetic_viz.make_chart_bars(
            [source], [synth], [], [(dt, 57000.0)]
        )
        bar = bars[0]
        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (101, 111, 91, 106))
        self.assertEqual(bar.probes, source.probes)
        self.assertLessEqual(bar.low, min(bar.open, bar.close))
        self.assertGreaterEqual(bar.high, max(bar.open, bar.close))

    def test_chart_uses_month2_only_after_contract_switch(self):
        dt = datetime(2026, 8, 25, 15, 30)
        source = Bar(dt, 21700, open=21680, high=21720, low=21660)
        synth = Bar(dt, 22030)
        events = [{
            "date": datetime(2026, 8, 25, 15, 21),
            "action": "contract_switch",
        }]
        minutes = [
            (datetime(2026, 8, 25, 15, 1), 21950),
            (datetime(2026, 8, 25, 15, 29), 22050),
            (dt, 22030),
        ]
        bar = synthetic_viz.make_chart_bars([source], [synth], events, minutes)[0]
        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (21950, 22050, 21950, 22030))
        self.assertLessEqual(bar.low, min(bar.open, bar.close))
        self.assertGreaterEqual(bar.high, max(bar.open, bar.close))

    def test_option_execution_uses_actual_bar_close_minute(self):
        previous = datetime(2026, 8, 3, 9, 59)
        dt = datetime(2026, 8, 3, 10, 0)
        minutes = [(previous, 100.0), (dt, 101.0)]
        lookup = synthetic.make_option_lookup(
            [previous, dt], [0, 0], [100, 101], [10, 11], [5, 6]
        )
        out, _, _, _ = synthetic.synthetic_bars_with_rollover(
            [Bar(dt, 100, open=99, high=102, low=98)],
            lookup,
            previous,
            dt,
            [],
            None,
            65,
            None,
            minutes,
        )
        self.assertEqual(out[0].close, 100)

    def test_chart_disambiguates_rollover_times_and_preserves_strategy_tags(self):
        first = datetime(2026, 8, 25, 15, 20)
        second = datetime(2026, 8, 25, 15, 21)
        end = datetime(2026, 8, 25, 15, 30)
        source = [Bar(end, 100, open=99, high=102, low=98)]
        synth = [
            Bar(first, 104, (Fill(-1, 105, 1, 0, "2NL"),)),
            Bar(first, 100, (Fill(-1, 100, 1, 0),)),
            Bar(second, 101, (Fill(-1, 101, 1, 0),)),
            Bar(end, 101),
        ]
        events = [
            {"date": first, "action": "square_off", "side": -1},
            {"date": second, "action": "rollover", "side": -1},
        ]
        bars = synthetic_viz.make_chart_bars(
            source, synth, events, [(end, 101.0)]
        )
        times = [bar.dt for bar in bars]
        self.assertTrue(all(a < b for a, b in zip(times, times[1:])))
        self.assertEqual(bars[0].fills[0].tag, "2NL")
        self.assertEqual(bars[1].fills[0].tag, "Short rollover exit")
        self.assertEqual(bars[2].fills[0].tag, "Short rollover entry")


if __name__ == "__main__":
    unittest.main()
