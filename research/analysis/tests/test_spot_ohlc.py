import importlib.util
import unittest
from datetime import datetime
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "emitters" / "_spot_ohlc.py"
SPEC = importlib.util.spec_from_file_location("spot_ohlc", MODULE_PATH)
spot_ohlc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spot_ohlc)


class SpotOhlcTests(unittest.TestCase):
    def test_close_only_and_full_ohlc_share_boundaries_and_closes(self):
        minutes = [
            (datetime(2024, 1, 2, 9, 15), 100, 108, 99, 100),
            (datetime(2024, 1, 2, 9, 16), 102, 110, 101, 104),
            (datetime(2024, 1, 2, 9, 29), 104, 109, 100, 103),
            (datetime(2024, 1, 2, 9, 30), 103, 106, 102, 105),
            (datetime(2024, 1, 2, 9, 31), 105, 107, 104, 106),
        ]
        close_bars, close_corrections = spot_ohlc.aggregate(minutes, close_only=True)
        full_bars, full_corrections = spot_ohlc.aggregate(minutes, close_only=False)

        self.assertEqual(close_bars[datetime(2024, 1, 2, 9, 30)], (100, 104, 100, 103))
        self.assertEqual(full_bars[datetime(2024, 1, 2, 9, 30)], (100, 110, 99, 103))
        self.assertEqual(close_bars[datetime(2024, 1, 2, 10, 0)], (105, 106, 105, 106))
        self.assertEqual(full_bars[datetime(2024, 1, 2, 10, 0)], (103, 107, 102, 106))
        self.assertEqual((close_corrections, full_corrections), (0, 0))

    def test_full_ohlc_normalizes_inconsistent_extrema(self):
        minutes = [(datetime(2024, 1, 2, 9, 15), 100, 99, 98, 99)]
        close_bars, close_corrections = spot_ohlc.aggregate(minutes, close_only=True)
        full_bars, full_corrections = spot_ohlc.aggregate(minutes, close_only=False)
        dt = datetime(2024, 1, 2, 9, 30)

        self.assertEqual(close_bars[dt], (99, 99, 99, 99))
        self.assertEqual(full_bars[dt], (100, 100, 98, 99))
        self.assertEqual((close_corrections, full_corrections), (0, 1))

    def test_regular_close_belongs_to_final_bar(self):
        minutes = [
            (datetime(2024, 1, 2, 15, 0), 100, 103, 99, 100),
            (datetime(2024, 1, 2, 15, 29), 101, 104, 100, 101),
            (datetime(2024, 1, 2, 15, 30), 102, 105, 101, 102),
        ]
        bars, _ = spot_ohlc.aggregate(minutes, close_only=True)

        self.assertEqual(list(bars), [datetime(2024, 1, 2, 15, 30)])
        self.assertEqual(bars[datetime(2024, 1, 2, 15, 30)], (100, 102, 100, 102))


if __name__ == "__main__":
    unittest.main()
