import math
import subprocess
import unittest

from analytics.backtest import ROOT, BacktestError, parse_stream, run_backtest
from analytics.ledger import replay

from .fixtures import account_bars


class ParserTests(unittest.TestCase):
    def test_current_schema_multiple_fills_probes_and_month_first_dates(self):
        text = (
            "Date,Time,Open,High,Low,Close,atr,event,fill_id,fill_side,"
            "fill_price,fill_qty,fill_fee\n"
            "01/31/24,09:15 AM,99,102,98,101,12.5,"
            "LE filled | 2NL filled,LE|2NL,+1|-1,100|101,2|1,3|4\n"
            "01/31/24,09:30 AM,101,103,100,102,,,,,,\n"
        )
        result = parse_stream(text)
        self.assertEqual(result.probe_names, ["atr"])
        self.assertEqual(result.bars[0].dt.day, 31)
        self.assertEqual(result.bars[0].probes, {"atr": 12.5})
        self.assertTrue(math.isnan(result.bars[1].probes["atr"]))
        self.assertEqual(
            [(f.side, f.price, f.qty, f.fee, f.tag) for f in result.bars[0].fills],
            [(1, 100, 2, 3, "LE"), (-1, 101, 1, 4, "2NL")],
        )

    def test_legacy_schema_is_rejected(self):
        text = "Date,Time,Open,High,Low,Close,event,fill_price,fill_qty,fill_cost\n"
        with self.assertRaisesRegex(BacktestError, "current schema"):
            parse_stream(text)

    def test_mismatched_fill_columns_are_rejected(self):
        text = (
            "Date,Time,Open,High,Low,Close,event,fill_id,fill_side,"
            "fill_price,fill_qty,fill_fee\n"
            "01/01/24,09:15 AM,99,102,98,101,LE filled,LE,,100,2,3\n"
        )
        with self.assertRaisesRegex(BacktestError, "different numbers"):
            parse_stream(text)

    def test_ocaml_csv_units_and_fees_match_ledger(self):
        subprocess.run(
            ["dune", "build", "rheo-backtest/test/test_backtest.exe"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        process = subprocess.run(
            [str(ROOT / "_build/default/rheo-backtest/test/test_backtest.exe"), "--emit-csv"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        result = parse_stream(process.stdout)
        fills = [fill for bar in result.bars for fill in bar.fills]
        self.assertEqual([fill.qty for fill in fills], [14, 14])
        self.assertEqual([fill.fee for fill in fills], [20, 20])
        account = replay(result.bars, 1000)
        self.assertEqual(account.realized_pnl[-1], -96)
        self.assertEqual(account.equity[-1], 904)


    def test_sigpipe_after_successful_bounded_run_is_not_a_failure(self):
        from unittest.mock import Mock, patch

        emitter = Mock()
        emitter.wait.return_value = -13
        strategy = Mock()
        strategy.returncode = 0
        strategy.communicate.return_value = ("", "")
        with (
            patch("analytics.backtest.available_strategies", return_value=["fixture"]),
            patch("analytics.backtest.subprocess.Popen", side_effect=[emitter, strategy]),
        ):
            result = run_backtest(
                "fixture", [], emitter=ROOT / "emitter.py", parse_output=False
            )
        self.assertIsNone(result)


class LedgerTests(unittest.TestCase):
    def test_fifo_partial_close_reversal_shorts_fees_and_mark_to_market(self):
        account = replay(account_bars(), 10_000)
        self.assertEqual(account.equity, [10980, 11975, 10465, 10965, 11460])
        self.assertEqual(account.net_qty, [200, 100, -100, -100, 0])
        self.assertEqual(account.realized_pnl, [-20, 975, 465, 465, 1460])
        self.assertEqual(account.fees, 40)
        self.assertEqual([trade.pnl for trade in account.trades], [985, -515, 990])
        self.assertEqual([trade.points for trade in account.trades], [10, -5, 10])
        self.assertEqual([trade.bars for trade in account.trades], [1, 2, 2])
        self.assertEqual(
            [trade.direction for trade in account.trades],
            ["long", "long", "short"],
        )


if __name__ == "__main__":
    unittest.main()
