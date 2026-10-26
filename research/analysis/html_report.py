"""HTML report helpers for tearsheets."""

import base64
import html
import io
from datetime import datetime
from pathlib import Path

from config import MONTHS
from terminal import polarity


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

def write_html_report(path, kw, content, png_bytes):
    """Write the self-contained, styled HTML report."""
    from tearsheet import HTML_CSS, HTML_DOC, HTML_JS

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
    doc = doc.replace("__STAMP__", f"{datetime.now():%d %b %Y %H:%M}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")

