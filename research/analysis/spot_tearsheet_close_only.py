#!/usr/bin/env python3
"""NIFTY spot tearsheet using 30-minute OHLC derived from one-minute closes."""

from config import ROOT
from tearsheet import main as run_tearsheet

EMITTER = ROOT / "research" / "emitters" / "spot_close_only_emitter.py"
DATA = ROOT / "data" / "raw" / "nifty50_1min.csv"
SCRIPT = "research/analysis/spot_tearsheet_close_only.py"


def main():
    run_tearsheet(
        emitter=EMITTER,
        data_path=DATA,
        data_label="NIFTY spot 30-minute close-only OHLC",
        stem="spot_tearsheet_close_only",
        script_label=SCRIPT,
    )


if __name__ == "__main__":
    main()
