#!/usr/bin/env python3
"""NIFTY spot tearsheet using 30-minute OHLC derived from full one-minute OHLC."""

from config import ROOT
from tearsheet import main as run_tearsheet

EMITTER = ROOT / "research" / "emitters" / "spot_full_ohlc_emitter.py"
DATA = ROOT / "data" / "raw" / "nifty50_1min.csv"
SCRIPT = "research/analysis/spot_tearsheet_full_ohlc.py"


def main():
    run_tearsheet(
        emitter=EMITTER,
        data_path=DATA,
        data_label="NIFTY spot 30-minute full OHLC",
        stem="spot_tearsheet_full_ohlc",
        script_label=SCRIPT,
    )


if __name__ == "__main__":
    main()
