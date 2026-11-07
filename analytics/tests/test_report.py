import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from analytics.ledger import replay
from analytics.metrics import analyze
from analytics.report import (
    build_report,
    chart_payload,
    print_report,
    write_html,
    write_trade_log,
)

from .fixtures import account_bars


def report_result():
    bars = [
        replace(bar, open=bar.close - 1, high=bar.close + 2, low=bar.close - 2)
        for bar in account_bars()
    ]
    performance = analyze(
        bars,
        initial=10_000,
        strategy="fixture",
        data="fixture.csv",
        cost_model="fixture costs",
    )
    return SimpleNamespace(
        chart_bars=bars,
        account=replay(bars, 10_000),
        performance=performance,
    )


def decode_chart(output):
    encoded = output.split(
        '<script id="chart-data" type="application/json">', 1
    )[1].split("</script>", 1)[0]
    return json.loads(encoded)


class ReportTests(unittest.TestCase):
    def test_terminal_uses_color_on_a_tty(self):
        content = build_report(report_result().performance)
        plain = io.StringIO()
        with redirect_stdout(plain):
            print_report(content)
        self.assertNotIn("\033[32m", plain.getvalue())
        self.assertIn("CLOSED-TRADE STATISTICS", plain.getvalue())
        self.assertIn("Maximum drawdown %", plain.getvalue())
        self.assertIn("-12.61%", plain.getvalue())

        colored = io.StringIO()
        with (
            redirect_stdout(colored),
            patch("sys.stdout.isatty", return_value=True),
            patch("analytics.report.os.environ.get", return_value=None),
        ):
            print_report(content)
        self.assertIn("\033[32m", colored.getvalue())
        self.assertIn("\033[1;36m", colored.getvalue())

    def test_html_always_contains_account_curves_without_candles(self):
        result = report_result()
        payload = chart_payload(result)
        self.assertFalse(payload["showCandles"])
        self.assertEqual(payload["candles"], [])
        self.assertEqual(len(payload["equity"]), len(result.chart_bars))
        self.assertEqual(len(payload["drawdown"]), len(result.chart_bars))

        content = build_report(result.performance)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.html"
            write_html(path, content, chart=payload)
            output = path.read_text()
        decoded = decode_chart(output)
        self.assertFalse(decoded["showCandles"])
        self.assertIn("LightweightCharts", output)
        self.assertIn("Zoom in", output)
        self.assertIn("Fullscreen", output)
        self.assertIn("summary-cards", output)
        self.assertNotIn("data:image", output)
        self.assertNotIn("https://unpkg.com", output)

    def test_optional_candles_add_price_and_fill_data(self):
        result = report_result()
        payload = chart_payload(result, include_candles=True)
        self.assertTrue(payload["showCandles"])
        self.assertEqual(len(payload["candles"]), len(result.chart_bars))
        self.assertEqual(len(payload["markers"]), 4)
        self.assertTrue(payload["fills"])

    def test_trade_log_uses_actual_fee_without_recalculation(self):
        from datetime import datetime

        entries = [{
            "timestamp": datetime(2024, 1, 1),
            "action": "OPEN",
            "tag": "LE",
            "side": "BUY",
            "qty": 65,
            "price": 100.5,
            "fee": 123.45,
        }]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            write_trade_log(path, entries)
            text = path.read_text()
        self.assertIn("123.45", text)
        self.assertIn("LE", text)
        self.assertEqual(
            text.splitlines()[0],
            "timestamp,action,order_id,side,quantity,price,fee",
        )
        self.assertIn("2024-01-01 00:00:00+05:30", text)


if __name__ == "__main__":
    unittest.main()
