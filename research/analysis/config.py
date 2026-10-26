"""Shared constants for tearsheet scripts."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PNG = ROOT / "reports" / "plots" / "tearsheet.png"
W = 84
_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
