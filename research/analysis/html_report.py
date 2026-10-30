"""HTML report helpers for tearsheets."""

import base64
import html
from datetime import datetime

from report import MONTHS, polarity

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
  <div class="stamp">Generated by __SCRIPT__ on __STAMP__.</div>
</footer>
</div>
<script>__JS__</script>
</body>
</html>
"""


def _esc(x):
    return html.escape(str(x))

def _html_chunks(r):
    parts = []
    for text, sign in r["chunks"]:
        body = _esc(text)
        if sign and sign > 0:
            parts.append(f'<span class="pos">{body}</span>')
        elif sign and sign < 0:
            parts.append(f'<span class="neg">{body}</span>')
        else:
            parts.append(body)
    body = "".join(parts)
    return f'<span class="muted">{body}</span>' if r["dim"] else body

def _html_rows(rows):
    out = []
    for r in rows:
        cls = ' class="sub"' if r["sub"] else ""
        out.append(
            f'<tr{cls}><td class="lbl">{_esc(r["label"])}</td>'
            f'<td class="val">{_html_chunks(r)}</td>'
            f'<td class="note">{_esc(r["note"])}</td></tr>'
        )
    return "\n".join(out)

def _html_cards(kw):
    t = kw["trades"]
    cards = [
        ("Final equity", f"{kw['final']:,.0f}", None,
         f"started at {kw['initial']:,.0f}"),
        ("Net P/L", f"{kw['pnl']:+,.0f}", polarity(kw["pnl"]),
         f"{kw['total']:+.2%} total return"),
        ("CAGR", f"{kw['cagr']:+.2%}", polarity(kw["cagr"]),
         f"over {kw['years']:.2f} years"),
        ("Sharpe ratio", f"{kw['sharpe']:.2f}", None, f"rf {kw['rf']:.2%}"),
        ("Max drawdown", f"{kw['maxdd_pct']:.2%}", polarity(kw["maxdd_pct"]),
         f"{kw['maxdd']:,.0f} on {kw['maxdd_trough']:%d %b %Y}"),
        ("Win rate", f"{t['win_rate']:.1%}", None,
         f"{t['wins']:,}W / {t['losses']:,}L"),
        ("Profit factor", f"{kw['profit_factor']:.2f}", None,
         "gross profit / gross loss"),
        ("Time underwater", f"{kw['underwater_pct']:.1%}", None,
         f"{kw['underwater_days']:,} of {kw['span_days']:,} days"),
    ]
    out = []
    for label, value, sign, note in cards:
        cls = "pos" if sign and sign > 0 else "neg" if sign and sign < 0 else ""
        out.append(
            f'<div class="card"><div class="k">{_esc(label)}</div>'
            f'<div class="v {cls}">{_esc(value)}</div>'
            f'<div class="d">{_esc(note)}</div></div>'
        )
    return '<div class="cards">\n' + "\n".join(out) + "\n</div>"

def _html_monthly(c):
    grid, annual = c["monthly"], c["annual"]
    present = [abs(v) for v in grid.values()] or [1e-9]
    scale = max(present) or 1e-9
    out = [
        '<section class="panel"><h2>Monthly returns</h2>',
        '<p class="caption">Monthly closes, percent. Green/red shading '
        'scales with the size of the move.</p>',
        '<table class="monthly"><thead><tr><th></th>',
    ]
    out += [f"<th>{m}</th>" for m in MONTHS]
    out.append("<th>Year</th></tr></thead><tbody>")
    for y in sorted(annual):
        out.append(f'<tr><td class="year">{y}</td>')
        for m in range(1, 13):
            v = grid.get((y, m))
            if v is None:
                out.append('<td class="miss">\u00b7</td>')
                continue
            style = ""
            if v != 0:
                alpha = 0.10 + 0.45 * min(abs(v) / scale, 1.0)
                rgb = "38, 166, 154" if v > 0 else "239, 83, 80"
                style = f' style="background:rgba({rgb},{alpha:.2f})"'
            out.append(
                f"<td{style}>"
                f'{v * 100:+.2f}</td>')
        a = annual[y]
        cls = "pos" if a > 0 else "neg" if a < 0 else ""
        out.append(f'<td class="annual {cls}">{a * 100:+.2f}</td></tr>')
    out.append("</tbody></table></section>")
    return "\n".join(out)

def _html_monthly_points(c):
    grid, annual = c.get("monthly_points", {}), c.get("annual_points", {})
    if not (grid or annual):
        return ""
    present = [abs(v) for v in grid.values()] or [1e-9]
    scale = max(present) or 1e-9
    out = [
        '<section class="panel"><h2>Monthly returns (points)</h2>',
        '<p class="caption">Monthly point P/L (direction \u00d7 (exit \u2212 entry) '
        'per trade; +1 long, \u22121 short). Green/red shading scales with '
        'the size of the move.</p>',
        '<table class="monthly"><thead><tr><th></th>',
    ]
    out += [f"<th>{m}</th>" for m in MONTHS]
    out.append("<th>Year</th></tr></thead><tbody>")
    for y in sorted(annual):
        out.append(f'<tr><td class="year">{y}</td>')
        for m in range(1, 13):
            v = grid.get((y, m))
            if v is None:
                out.append('<td class="miss">\u00b7</td>')
                continue
            style = ""
            if v != 0:
                alpha = 0.10 + 0.45 * min(abs(v) / scale, 1.0)
                rgb = "38, 166, 154" if v > 0 else "239, 83, 80"
                style = f' style="background:rgba({rgb},{alpha:.2f})"'
            out.append(
                f"<td{style}>"
                f'{v:+.2f}</td>')
        a = annual[y]
        cls = "pos" if a > 0 else "neg" if a < 0 else ""
        out.append(f'<td class="annual {cls}">{a:+.2f}</td></tr>')
    out.append("</tbody></table></section>")
    return "\n".join(out)

def write_html_report(
    path, kw, content, png_bytes, script_label="research/analysis/tearsheet.py"
):
    """Write the self-contained, styled HTML report."""
    cards = _html_cards(kw)
    sections = []
    for title, rows in content["sections"]:
        sections.append(
            f'<section class="panel"><h2>{_esc(title)}</h2>'
            f'<table class="stats"><tbody>{_html_rows(rows)}</tbody></table>'
            f'</section>'
        )
    bench = ""
    if content["bench"]:
        bench = (
            '<section class="panel"><h2>Versus buy &amp; hold</h2>'
            f'<table class="stats"><tbody>{_html_rows(content["bench"])}'
            '</tbody></table></section>'
        )
    doc = HTML_DOC
    doc = doc.replace("__CSS__", HTML_CSS)
    doc = doc.replace("__JS__", HTML_JS)
    doc = doc.replace("__TITLE__", _esc(content["title"]))
    doc = doc.replace("__HEADLINE__",
                      f"{_esc(kw['strategy'])} <span>on {_esc(kw['data'])}</span>")
    doc = doc.replace("__SUBTITLE__", _esc(content["subtitle"]))
    doc = doc.replace("__PERIOD__",
                      f"{kw['d0']:%d %b %Y} \u2192 {kw['d1']:%d %b %Y}")
    doc = doc.replace(
        "__CAPTION__",
        _esc("Account equity vs. buy & hold, the underwater curve from the "
             "high-water mark, and a rolling 126-session Sharpe ratio."))
    doc = doc.replace("__PNG__", base64.b64encode(png_bytes).decode("ascii"))
    doc = doc.replace("__CARDS__", cards)
    doc = doc.replace("__OVERVIEW__", _html_rows(content["overview"]))
    doc = doc.replace("__SECTIONS__", "\n".join(sections))
    doc = doc.replace("__MONTHLY__", _html_monthly(content) + "\n" + _html_monthly_points(content))
    doc = doc.replace("__BENCH__", bench)
    doc = doc.replace("__FOOTER__", _esc(content["footer"]))
    doc = doc.replace("__SCRIPT__", _esc(script_label))
    doc = doc.replace("__STAMP__", f"{datetime.now():%d %b %Y %H:%M}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")

