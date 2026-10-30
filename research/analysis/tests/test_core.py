import io
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import engine
import ledger
import performance
import plot_pnl
import viz
from html_report import write_html_report
from report import build_report
from terminal import print_report

ROOT = Path(__file__).resolve().parents[3]


def account_bars():
    def bar(stamp, close, fills=()):
        return engine.Bar(datetime.fromisoformat(stamp), close, tuple(fills))

    return [
        bar("2024-01-01 10:00", 105, [engine.Fill(1, 100, 2, 20, "LE")]),
        bar("2024-01-01 11:00", 110, [engine.Fill(-1, 110, 1, 5, "2NL")]),
        bar("2024-01-02 10:00", 95, [engine.Fill(-1, 95, 2, 10, "SE")]),
        bar("2024-01-02 11:00", 90),
        bar("2024-01-02 12:00", 85, [engine.Fill(1, 85, 1, 5, "2NS")]),
    ]


def result_without_benchmark():
    bars = account_bars()
    return performance.analyze(
        bars,
        lot_size=100,
        initial=10_000,
        strategy="fixture",
        data="fixture.csv",
        cost_model="fixture costs",
    )


class EngineTests(unittest.TestCase):
    def test_stream_retains_ohlc_probes_manifest_and_multiple_fills(self):
        text = (
            "# tags: LE=+1 2NL=-1\n"
            "Date,Time,Open,High,Low,Close,atr,event,fill_price,fill_qty,fill_cost\n"
            "01/01/24,09:15 AM,99,102,98,101,12.5,"
            "LE filled | 2NL filled,100|101,2|1,3|4\n"
            "01/01/24,09:30 AM,101,103,100,102,,,,,\n"
        )
        stream = engine.parse_stream(text)
        self.assertEqual(stream.probe_names, ["atr"])
        self.assertEqual(stream.tags, {"LE": 1, "2NL": -1})
        self.assertEqual(len(stream.bars), 2)
        first = stream.bars[0]
        self.assertEqual((first.open, first.high, first.low, first.close), (99, 102, 98, 101))
        self.assertEqual(first.probes, {"atr": 12.5})
        self.assertEqual(
            [(f.side, f.price, f.qty, f.fee, f.tag) for f in first.fills],
            [(1, 100, 2, 3, "LE"), (-1, 101, 1, 4, "2NL")],
        )
        self.assertTrue(math.isnan(stream.bars[1].probes["atr"]))

    def test_explicit_fill_columns_replace_manifest(self):
        text = (
            "Date,Time,Open,High,Low,Close,event,fill_id,fill_side,"
            "fill_price,fill_qty,fill_cost\n"
            "01/01/24,09:15 AM,99,102,98,101,"
            "new entry filled | new exit filled,new entry|new exit,+1|-1,"
            "100|101,2|1,3|4\n"
        )
        stream = engine.parse_stream(text, side_for=lambda _: 99)
        self.assertEqual(stream.tags, {})
        self.assertEqual(
            [(f.side, f.price, f.qty, f.fee, f.tag) for f in stream.bars[0].fills],
            [
                (1, 100, 2, 3, "new entry"),
                (-1, 101, 1, 4, "new exit"),
            ],
        )
        self.assertEqual(
            plot_pnl.parse_rows(text),
            [
                (1, 100, 2, 3, datetime(2024, 1, 1, 9, 15)),
                (-1, 101, 1, 4, datetime(2024, 1, 1, 9, 15)),
            ],
        )

    def test_fallback_side_and_custom_side_resolver(self):
        text = (
            "Date,Time,Open,High,Low,Close,event,fill_price,fill_qty,fill_cost\n"
            "01/01/24,09:15 AM,99,102,98,101,LE filled,100,2,3\n"
        )
        self.assertEqual(engine.parse_stream(text).bars[0].fills[0].side, 1)
        custom = engine.parse_stream(text, side_for=lambda _: -7)
        self.assertEqual(custom.bars[0].fills[0].side, -7)
        with self.assertRaisesRegex(ValueError, "unknown order tag"):
            engine.parse_stream(text.replace("LE filled", "UNKNOWN filled"))

    def test_mismatched_fill_columns_are_rejected(self):
        text = (
            "# tags: LE=+1\n"
            "Date,Time,Open,High,Low,Close,event,fill_price,fill_qty,fill_cost\n"
            "01/01/24,09:15 AM,99,102,98,101,LE filled,,2,3\n"
        )
        with self.assertRaisesRegex(ValueError, "event/fill columns disagree"):
            engine.parse_stream(text)

    def test_emitter_failure_is_not_hidden(self):
        emitter = Mock()
        emitter.returncode = 1
        strategy = Mock()
        strategy.returncode = 0
        strategy.communicate.return_value = ("", "")
        with patch("engine.subprocess.Popen", side_effect=[emitter, strategy]):
            with self.assertRaisesRegex(SystemExit, "data emitter failed"):
                engine.run_backtest("unused")


class LedgerTests(unittest.TestCase):
    def test_fifo_partial_close_reversal_shorts_fees_and_mtm(self):
        account = ledger.replay(account_bars(), 100, 10_000)
        self.assertEqual(account.equity, [10980, 11975, 10465, 10965, 11460])
        self.assertEqual(account.net_lots, [2, 1, -1, -1, 0])
        self.assertEqual(account.realized_pnl, [-20, 975, 465, 465, 1460])
        self.assertEqual(account.fees, 40)
        self.assertEqual([trade.pnl for trade in account.trades], [985, -515, 990])
        self.assertEqual([trade.points for trade in account.trades], [10, -5, 10])
        self.assertEqual([trade.bars for trade in account.trades], [1, 2, 2])
        self.assertEqual(
            [trade.direction for trade in account.trades],
            ["long", "long", "short"],
        )
        self.assertEqual(
            [trade.exit_tag for trade in account.trades], ["2NL", "SE", "2NS"]
        )


class PerformanceTests(unittest.TestCase):
    def test_performance_uses_first_session_and_source_benchmark(self):
        bars = account_bars()
        benchmark = [10_000 * bar.close / bars[0].close for bar in bars]
        result = performance.analyze(
            bars,
            lot_size=100,
            initial=10_000,
            strategy="fixture",
            data="fixture.csv",
            cost_model="fixture costs",
            benchmark_equity=benchmark,
        )
        kw = result.metrics
        self.assertEqual(result.day_equity, [11975, 11460])
        self.assertEqual(result.returns[0], 0.1975)
        self.assertAlmostEqual(result.returns[1], 11460 / 11975 - 1)
        self.assertEqual(kw["final"], 11460)
        self.assertAlmostEqual(kw["total"], 0.146)
        self.assertEqual(kw["total_fees"], 40)
        self.assertEqual(kw["n_trades"], 3)
        self.assertEqual(kw["exposure"], 0.8)
        self.assertEqual(kw["maxdd"], -1510)
        self.assertAlmostEqual(kw["maxdd_pct"], -1510 / 11975)
        self.assertEqual(result.worst_i, 2)
        self.assertAlmostEqual(kw["monthly"][(2024, 1)], 0.146)
        self.assertAlmostEqual(kw["annual"][2024], 0.146)
        self.assertEqual(kw["monthly_points"][(2024, 1)], 15)
        self.assertEqual(result.benchmark_equity, benchmark)
        self.assertIsNotNone(kw["bench"])

    def test_viz_footer_shares_tearsheet_definitions(self):
        bars = account_bars()
        account = ledger.replay(bars, 100, 10_000)
        ctx = {
            "dates": [bar.dt for bar in bars],
            "bars": bars,
            "account": account,
            "lot_size": 100,
            "initial": 10_000,
            "strategy": "fixture",
            "data": "fixture.csv",
            "costs": "fixture costs",
            "trades": account.trades,
        }
        kw = result_without_benchmark().metrics
        expected = (f"CAGR {kw['cagr']:+.1%}   Sharpe {kw['sharpe']:.2f}   "
                    f"max DD {kw['maxdd_pct']:.1%}   trades 3")
        self.assertEqual(viz.stats_footer(ctx), expected)

    def test_terminal_and_html_render_the_same_content(self):
        result = result_without_benchmark()
        content = build_report(result.metrics)
        output = io.StringIO()
        with redirect_stdout(output):
            print_report(content)
        self.assertIn("PERFORMANCE REPORT", output.getvalue())
        self.assertIn("MONTHLY RETURNS (%)", output.getvalue())

        with tempfile.TemporaryDirectory(dir=ROOT / "_build") as tmp:
            path = Path(tmp) / "report.html"
            write_html_report(
                path, result.metrics, content, b"fake-png",
                "research/analysis/tearsheet.py",
            )
            html = path.read_text()
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("Generated by research/analysis/tearsheet.py", html)
        self.assertNotIn("__TITLE__", html)
        self.assertNotIn("__PNG__", html)
        self.assertNotIn("__SCRIPT__", html)


if __name__ == "__main__":
    unittest.main()
