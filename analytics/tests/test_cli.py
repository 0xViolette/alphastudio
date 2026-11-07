import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analytics.cli import build_parser, main
from analytics.metrics import analyze
from analytics.scenarios import ScenarioResult, make_scenario
from analytics.ledger import replay

from .fixtures import account_bars


class CliTests(unittest.TestCase):
    def test_report_subcommands_and_variants(self):
        parser = build_parser()
        args = parser.parse_args(["report", "spot", "--ohlc", "close-only"])
        self.assertEqual((args.command, args.kind, args.ohlc), ("report", "spot", "close-only"))
        args = parser.parse_args(["report", "synthetic", "--signals", "options", "--ohlc", "extrema"])
        self.assertEqual((args.signals, args.ohlc), ("options", "extrema"))

    def test_noninteractive_report_requires_strategy(self):
        with patch("sys.stdin.isatty", return_value=False), patch("sys.stdout.isatty", return_value=False):
            with self.assertRaises(SystemExit) as raised:
                main(["report", "futures"])
        self.assertEqual(raised.exception.code, 1)

    def test_chart_requires_html_output(self):
        with patch("sys.stdin.isatty", return_value=False), patch("sys.stdout.isatty", return_value=False):
            with self.assertRaises(SystemExit) as raised:
                main(["report", "futures", "--strategy", "chandelier", "--chart"])
        self.assertEqual(raised.exception.code, 1)

    def test_report_command_runs_scenario_and_writes_html(self):
        bars = account_bars()
        perf = analyze(
            bars,
            initial=10_000,
            strategy="fixture",
            data="fixture.csv",
            cost_model="fixture costs",
        )
        result = ScenarioResult(
            scenario=make_scenario("futures"),
            strategy="chandelier",
            bars=bars,
            chart_bars=bars,
            account=replay(bars, 10_000),
            performance=perf,
            trade_log=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.html"
            with (
                patch("analytics.cli.run_scenario", return_value=result) as run,
                patch("sys.stdin.isatty", return_value=False),
                patch("sys.stdout.isatty", return_value=False),
            ):
                main([
                    "report", "futures", "--strategy", "chandelier",
                    "--costs", "none", "--output", str(output),
                ])
            self.assertTrue(output.exists())
            self.assertEqual(run.call_args.kwargs["strategy"], "chandelier")


    def test_wizard_leaves_advanced_settings_at_their_defaults(self):
        from analytics.cli import _wizard
        from analytics.costs import schema

        args = build_parser().parse_args(["report", "futures"])
        answers = ["1"] + [""] * 8
        with patch("builtins.input", side_effect=answers):
            _wizard(args, schema())
        self.assertEqual(args.strategy, "baseline")
        self.assertEqual(args.costs, "futures")
        self.assertEqual((args.rf, args.mar, args.trials), (None, None, None))
        self.assertIsNone(args.lot_size)
        self.assertIsNone(args.initial_balance)
        self.assertIsNone(args.output)
        self.assertIsNone(args.trade_log)

    def test_bare_invocation_enters_report_wizard(self):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("analytics.cli._run_report") as run,
        ):
            main([])
        args = run.call_args.args[0]
        self.assertEqual(args.command, "report")
        self.assertIsNone(args.kind)

    def test_bare_noninteractive_invocation_prints_help(self):
        import io
        from contextlib import redirect_stdout

        output = io.StringIO()
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
            redirect_stdout(output),
        ):
            main([])
        self.assertIn("usage: analytics", output.getvalue())


if __name__ == "__main__":
    unittest.main()
