#!/usr/bin/env python3
"""Synthetic-futures tearsheet using option-extrema high/low envelopes."""

from config import ROOT
from synthetic import main as run_synthetic

EMITTER = ROOT / "research" / "emitters" / "synthetic_ohlc_extrema_emitter.py"
SCRIPT = "research/analysis/synthetic_tearsheet_options_extrema.py"


def main():
    run_synthetic(
        "options",
        options_emitter=EMITTER,
        stem="synthetic_tearsheet_options_extrema",
        script_label=SCRIPT,
    )


if __name__ == "__main__":
    main()
