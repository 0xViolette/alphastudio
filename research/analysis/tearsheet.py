"""Performance report and drawdown plots for a strategy.

Usage from the repository root:
    python research/analysis/tearsheet.py [STRATEGY] [--out tearsheet.png]
        [--rf 0.065] [--mar 0.0] [--trials 100]
        [--from 2024-01-01] [--till 2024-12-31]
        [--export [report.html]]

Dates run a fresh backtest over the selected inclusive IST window. The report
uses the configured account balance, replays fills through the lot ledger,
and marks every open lot to each bar's close.

The terminal report is one aligned block. HTML export includes the figure,
colour-coded metrics, and monthly heatmaps. The figure contains equity versus
buy-and-hold, an underwater curve, and rolling 126-session Sharpe. `--trials N`
deflates Sharpe for multiple strategy variants (Bailey & Lopez de Prado 2014).
"""

import argparse
import base64
import csv
import html
import io
import os
import sys
from datetime import date, datetime
from math import exp, sqrt
from pathlib import Path
from statistics import NormalDist

import costs as cost_cli
from plot_pnl import (
    DEFAULT_STRATEGY,
    EMITTER,
    SIDE_BY_TAG,
    STRATEGIES,
    is_zero,
    resolve_strategy,
    run_backtest,
    sgn,
)

from metrics import (
    NORMAL, moments, quantile, downside_deviation, cvar,
    sharpe_var, psr, min_track_record, expected_max_sharpe,
    ulcer_index, drawdowns, math_prod,
)
from terminal import (
    W, _COLOR, tone, dim, bold, rule, kv, banner,
    polarity, signed_text, row, chunks_row, kv_row,
)
from html_report import (
    _esc, _html_chunks, _html_rows, _html_cards,
    _html_monthly, _html_monthly_points, write_html_report,
)
from trade_log import (
    _date_arg, _prompt_date_window, _prompt_output_files,
    _select_date_window, _generate_trade_log, _write_trade_log_csv,
)
from backtest import (
    TAGS, side_for, parse_tags, parse_bars, ledger,
)
from config import ROOT, OUT_PNG, W, _COLOR, MONTHS
from cli import main
from report import build_report, print_report





# ---------------------------------------------------------------- bar stream




# ---------------------------------------------------------------- statistics


# ------------------------------------------------------------------ report


# -------------------------------------------------------------------- HTML

HTML_CSS = """
:root {
  --bg: #f4f6fb; --panel: #ffffff; --ink: #1b2233; --muted: #6b7385;
  --line: #e3e7f0; --accent: #2f6bf6; --up: #0a8f6c; --down: #cf3d3d;
  --chip: #eef3ff; --shadow: 0 1px 2px rgba(20, 30, 60, .06),
  0 12px 32px rgba(20, 30, 60, .07);
}
[data-theme="dark"] {
  --bg: #0e121c; --panel: #161c29; --ink: #e6eaf3; --muted: #98a1b5;
  --line: #283041; --accent: #7aa2ff; --up: #34c49a; --down: #ff6b6b;
  --chip: #1d2740; --shadow: 0 1px 2px rgba(0, 0, 0, .35),
  0 12px 32px rgba(0, 0, 0, .35);
}
* { box-sizing: border-box; }
html { color-scheme: light; }
[data-theme="dark"] { color-scheme: dark; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.55 Inter, -apple-system, BlinkMacSystemFont, "Segoe UI",
  Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 34px 26px 54px; }
.hero { display: flex; align-items: flex-start; gap: 20px; margin-bottom: 22px; }
.hero .eyebrow {
  color: var(--accent); font-size: 11px; font-weight: 800;
  letter-spacing: .16em; text-transform: uppercase; margin-bottom: 6px;
}
.hero h1 { margin: 0; font-size: 29px; line-height: 1.15; letter-spacing: -.02em; }
.hero h1 span { color: var(--muted); font-weight: 500; }
.hero .sub { margin: 7px 0 0; color: var(--muted); font-size: 13px; }
.hero .meta { margin-left: auto; text-align: right; color: var(--muted); font-size: 12px; }
.hero .meta strong { display: block; color: var(--ink); font-size: 13px; }
#theme {
  margin-top: 8px; font: inherit; font-size: 11.5px; padding: 5px 11px;
  border-radius: 999px; border: 1px solid var(--line); background: var(--panel);
  color: var(--ink); cursor: pointer;
}
#theme:hover { border-color: var(--accent); color: var(--accent); }
.cards {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px; margin-bottom: 20px;
}
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
  padding: 14px 16px; box-shadow: var(--shadow);
}
.card .k {
  color: var(--muted); font-size: 10.5px; font-weight: 700;
  letter-spacing: .09em; text-transform: uppercase;
}
.card .v {
  margin-top: 6px; font-size: 23px; font-weight: 700;
  letter-spacing: -.02em; font-variant-numeric: tabular-nums;
}
.card .d { margin-top: 3px; color: var(--muted); font-size: 11.5px; }
.panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
  padding: 20px 22px; margin-bottom: 16px; box-shadow: var(--shadow);
  break-inside: avoid;
}
.panel > h2 {
  margin: 0 0 12px; font-size: 12px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--accent);
}
.panel .caption { color: var(--muted); font-size: 12px; margin: -6px 0 14px; }
.imgwrap { padding: 10px; background: #fff; border-radius: 10px; }
.imgwrap img { width: 100%; height: auto; display: block; }
table.stats { width: 100%; border-collapse: collapse; }
table.stats tr + tr td { border-top: 1px solid var(--line); }
table.stats td { padding: 7px 0; vertical-align: baseline; }
table.stats td.lbl { width: 36%; color: var(--muted); }
table.stats td.val {
  width: 20%; text-align: right; font-weight: 650;
  font-variant-numeric: tabular-nums; white-space: nowrap; padding-right: 22px;
}
table.stats td.note { width: 44%; color: var(--muted); font-size: 12.5px; }
table.stats tr.sub td { padding-top: 2px; padding-bottom: 2px; font-size: 12.5px; }
table.stats tr.sub td.lbl { padding-left: 18px; }
ul.warnings { margin: 0; padding-left: 18px; color: var(--ink); }
ul.warnings li { margin: 3px 0; font-size: 12.5px; }
.pos { color: var(--up); }
.neg { color: var(--down); }
.muted { color: var(--muted); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
table.monthly { width: 100%; border-collapse: separate; border-spacing: 3px; }
table.monthly th {
  color: var(--muted); font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: .05em; padding: 2px 0 6px;
}
table.monthly td {
  text-align: center; padding: 5px 2px; border-radius: 5px; font-size: 12px;
  font-variant-numeric: tabular-nums; background: var(--chip);
}
table.monthly td.year { background: transparent; color: var(--muted); font-weight: 650; }
table.monthly td.miss { background: transparent; color: var(--muted); }
table.monthly td.annual { background: transparent; font-weight: 700; }
footer {
  margin-top: 24px; color: var(--muted); font-size: 12px;
  border-top: 1px solid var(--line); padding-top: 14px;
}
footer .stamp { margin-top: 6px; font-size: 11px; }
@media (max-width: 900px) {
  .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .grid2 { grid-template-columns: 1fr; }
  .hero { flex-direction: column; }
  .hero .meta { margin-left: 0; text-align: left; }
  .wrap { padding: 22px 14px 34px; }
}
@media (max-width: 520px) {
  .cards { grid-template-columns: 1fr; }
}
@media print {
  .no-print { display: none !important; }
  body { background: #fff; }
  .panel, .card { box-shadow: none; break-inside: avoid; }
  .wrap { max-width: none; padding: 0; }
}
"""

HTML_JS = """
(function () {
  var root = document.documentElement;
  var btn = document.getElementById("theme");
  function apply(name) {
    root.dataset.theme = name;
    btn.textContent = name === "dark" ? "Light theme" : "Dark theme";
    try { localStorage.setItem("drawdown-theme", name); } catch (e) {}
  }
  btn.addEventListener("click", function () {
    apply(root.dataset.theme === "dark" ? "light" : "dark");
  });
  var saved = null;
  try { saved = localStorage.getItem("drawdown-theme"); } catch (e) {}
  apply(saved || (window.matchMedia &&
    matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
})();
"""

HTML_DOC = """<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<div class="wrap">
<header class="hero">
  <div>
    <div class="eyebrow">Performance report</div>
    <h1>__HEADLINE__</h1>
    <p class="sub">__SUBTITLE__</p>
  </div>
  <div class="meta">
    <strong>__PERIOD__</strong>
    <button id="theme" class="no-print" type="button">Dark theme</button>
  </div>
</header>
__CARDS__
<section class="panel">
  <h2>Equity, drawdown and rolling Sharpe</h2>
  <p class="caption">__CAPTION__</p>
  <div class="imgwrap"><img alt="Equity, drawdown and rolling Sharpe chart"
    src="data:image/png;base64,__PNG__"></div>
</section>
<section class="panel">
  <h2>Account</h2>
  <table class="stats"><tbody>__OVERVIEW__</tbody></table>
</section>
__SECTIONS__
__MONTHLY__
__BENCH__
<footer>
  __FOOTER__
  <div class="stamp">Generated by research/analysis/tearsheet.py on __STAMP__.</div>
</footer>
</div>
<script>__JS__</script>
</body>
</html>
"""




if __name__ == "__main__":
    main()
