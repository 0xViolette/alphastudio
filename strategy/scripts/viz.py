"""Master visualization script for backtest results (TradingView-style).

Renders professional-grade interactive charts of a strategy backtest —
price with trades marked, timeless chart types (Renko, Kagi, Point & Figure,
Line Break, Heikin-Ashi) and analytics tear-sheet panels — with plotly.

Usage (from the strategy/ project root):
    python3 scripts/viz.py                     # interactive menu
    python3 scripts/viz.py candles             # one chart, run the backtest
    python3 scripts/viz.py renko --csv out.csv --brick 15 --png

Data source: by default the emitter | dune exec pipeline of
scripts/plot_pnl.py is run; --csv PATH loads a saved CSV stream instead
(out.csv, scratch/out.csv, ...). Charts are written as self-contained HTML
(dark theme with an in-chart Dark/Light toggle); --png additionally writes a
static PNG via kaleido. --style light renders the PNG/light default.

Chart types: candles, ohlc, heikin-ashi, renko, kagi, pnf, linebreak,
equity, heatmap, trades, exposure. (No volume charts: the feed is OHLC only.)
"""

import argparse
import csv
import io
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from plot_pnl import DUNE_TARGETS, SIDE_BY_TAG, read_ocaml_literal, run_backtest

ROOT = Path(__file__).resolve().parent.parent

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Order specs are read from the strategy sources instead of being hardcoded
# per tag: every enter_*/exit_* declaration in bin/*.ml defines an order's
# action, long/short side and fill direction, and any (Stop <probe>) argument
# names the probe that carries the order's trigger level.
ORDER_SRC = re.compile(
    r'\b(enter_long|enter_short|exit_long|exit_short)\s+"([^"]+)"')
STOP_SRC = re.compile(r'\(\s*Stop\s+([A-Za-z_][A-Za-z0-9_]*)\b')
OCAML_COMMENT = re.compile(r"\(\*.*?\*\)", re.S)


def _parse_order_specs():
    specs, index = {}, 0
    for path in sorted((ROOT / "bin").glob("*.ml")):
        text = OCAML_COMMENT.sub(" ", path.read_text())
        for m in ORDER_SRC.finditer(text):
            action, tag = m.group(1), m.group(2)
            if tag in specs:
                continue
            entry = action.startswith("enter_")
            group = "long" if action.endswith("long") else "short"
            fill_side = 1 if action in ("enter_long", "exit_short") else -1
            end = text.find("\n", m.end())
            tail = text[m.end():] if end < 0 else text[m.end():end]
            stop = STOP_SRC.search(tail)
            specs[tag] = {
                "tag": tag, "action": action,
                "label": action.replace("_", " ").capitalize(),
                "entry": entry, "group": group,
                "fill_side": fill_side, "index": index,
                "stop": stop.group(1) if stop else None,
            }
            index += 1
    return specs


ORDER_SPECS = _parse_order_specs()


def fill_side(tag):
    """+1 for a buy fill, -1 for a sell fill, from bin/*.ml (then fallback)."""
    spec = ORDER_SPECS.get(tag)
    if spec is not None:
        return spec["fill_side"]
    if tag in SIDE_BY_TAG:
        return SIDE_BY_TAG[tag]
    sys.exit(f"unknown order tag {tag!r}; declare it with enter_long/"
             f"enter_short/exit_long/exit_short in bin/*.ml or add it to "
             f"SIDE_BY_TAG in scripts/plot_pnl.py")

# ---------------------------------------------------------------- themes
# TradingView palettes; gray #787b86 annotations stay readable on both.

THEMES = {
    "dark": {
        "bg": "#131722", "grid": "#1e222d", "axis": "#2a2e39",
        "font": "#d1d4dc", "up": "#26a69a", "down": "#ef5350",
        "line": "#2962ff", "hover_bg": "#1e222d", "hover_border": "#363c4e",
    },
    "light": {
        "bg": "#ffffff", "grid": "#f0f3fa", "axis": "#d6dae2",
        "font": "#131722", "up": "#089981", "down": "#f23645",
        "line": "#2962ff", "hover_bg": "#ffffff", "hover_border": "#d6dae2",
    },
}
NEUTRAL = "#787b86"  # annotation / watermark gray, readable on both themes
WIN, LOSS = "#26a69a", "#ef5350"
# fill-direction colors for markers; deliberately outside the candle palette
# (blue / magenta) so buys and sells never blend into green/red bars
BUY, SELL = "#2962ff", "#e040fb"

# probe overlay styling: conventional probe names keep their TradingView-ish
# colors, every other price-scale probe falls back to the cycle below
PROBE_STYLE = {
    "mov":     ("#f7a600", 1.4),
    "lx2n":    (NEUTRAL, 1),
    "sx2n":    (NEUTRAL, 1),
    "le":      ("#7986cb", 1),
    "se":      ("#5c6bc0", 1),
    "chand_l": ("#4db6ac", 1),
    "chand_s": ("#ff8a80", 1),
}
PROBE_FALLBACK = ("#42a5f5", "#ab47bc", "#26c6da", "#8d6e63",
                  "#ec407a", "#9ccc65", "#ffa726", "#5e35b1")


def probe_style(name, i=0):
    """(color, width) for a probe overlay, stable across a CSV's columns."""
    if name in PROBE_STYLE:
        return PROBE_STYLE[name]
    return PROBE_FALLBACK[i % len(PROBE_FALLBACK)], 1


def price_probes(ctx):
    """The probe columns that live on the price scale.

    Any probe whose values sit inside the candle range (moving averages, stop
    levels, trailing stops, ...) is an overlay; values on other scales (ATR,
    position size, ...) are left out. This is how viz overlays the probes of
    an arbitrary strategy without knowing any of its names.
    """
    lo, hi = min(ctx["low"]), max(ctx["high"])
    pad = 0.1 * (hi - lo)
    out = []
    for name, series in ctx["probes"].items():
        vals = [v for v in series if not math.isnan(v)]
        if vals and min(vals) >= lo - pad and max(vals) <= hi + pad:
            out.append(name)
    return out


def axis_names(fig):
    """Every xaxis/yaxis attribute name present on the figure's layout."""
    return [k for k in fig.layout if k.startswith(("xaxis", "yaxis"))]


def theme_relayout(theme, axes):
    """Flat relayout dict that re-skins a figure to `theme` (for the toggle)."""
    t = THEMES[theme]
    d = {
        "paper_bgcolor": t["bg"],
        "plot_bgcolor": t["bg"],
        "font.color": t["font"],
        "hoverlabel.bgcolor": t["hover_bg"],
        "hoverlabel.bordercolor": t["hover_border"],
        "hoverlabel.font.color": t["font"],
    }
    for ax in axes:
        d[f"{ax}.gridcolor"] = t["grid"]
        d[f"{ax}.zerolinecolor"] = t["axis"]
        d[f"{ax}.linecolor"] = t["axis"]
    return d


def apply_theme(fig, theme, stats_line=""):
    """Skin `fig` and attach the Dark/Light toggle buttons."""
    axes = axis_names(fig)
    fig.update_layout(
        **{k.replace("__", "."): v for k, v in
           theme_relayout(theme, axes).items()},
        font=dict(family="Inter, -apple-system, Helvetica, Arial, sans-serif",
                  size=12),
        hovermode="x unified",
        hoverdistance=30,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        margin=dict(l=70, r=60, t=110, b=72),
    )
    # keep the title clear of the preset / theme button rows
    fig.update_layout(title=dict(yref="container", y=0.945, yanchor="top"))
    fig.update_layout(
        updatemenus=list(fig.layout.updatemenus or []) + [dict(
            type="buttons", direction="right", showactive=False,
            x=1.0, xanchor="right", y=1.13, yanchor="top",
            pad=dict(r=2, t=2), bgcolor=THEMES[theme]["grid"],
            bordercolor=THEMES[theme]["axis"], font=dict(size=11),
            buttons=[
                dict(label="Dark", method="relayout",
                     args=[theme_relayout("dark", axes)]),
                dict(label="Light", method="relayout",
                     args=[theme_relayout("light", axes)]),
            ],
        )],
    )
    if stats_line:
        fig.add_annotation(
            text=stats_line, xref="paper", yref="paper", x=1, y=-0.062,
            showarrow=False, xanchor="right", font=dict(size=11, color=NEUTRAL),
        )
    return fig


def add_preset_buttons(fig, theme):
    """Toolbar row of visibility presets (All / Trades / Stops / Bare).

    Matches traces by legend name, so it works regardless of how many probe
    overlays were actually present in the data.
    """
    names = [tr.name for tr in fig.data]
    core = {"Up", "Down", "close"}  # webgl candles + hover helper
    fills = {"Long entry", "Short entry", "Exit fill"}
    connectors = {"Winning trades", "Losing trades"}
    stops = set(names) - core - fills - connectors  # the probe overlays
    presets = [
        ("All", set(names)),
        ("Trades", core | fills | connectors),
        ("Stops", core | fills | stops),
        ("Bare", core),
    ]
    buttons = [
        dict(label=label, method="restyle",
             args=[{"visible": [n in visible for n in names]}])
        for label, visible in presets
    ]
    fig.update_layout(
        updatemenus=list(fig.layout.updatemenus or []) + [dict(
            type="buttons", direction="right", showactive=False,
            x=0.0, xanchor="left", y=1.13, yanchor="top",
            pad=dict(r=2, t=2), bgcolor=THEMES[theme]["grid"],
            bordercolor=THEMES[theme]["axis"], font=dict(size=11),
            buttons=buttons,
        )],
    )


# ---------------------------------------------------------------- data layer


def parse_stream(text):
    """Backtest CSV stream -> (dates, ohlc, probes, per-bar fills with tags)."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        sys.exit("empty data stream")
    header = rows[0]
    i_dt, i_tm = header.index("Date"), header.index("Time")
    i_o, i_h, i_l, i_c = (header.index(c) for c in ("Open", "High", "Low", "Close"))
    i_evt = header.index("event")
    i_px, i_qt = header.index("fill_price"), header.index("fill_qty")
    i_ct = header.index("fill_cost") if "fill_cost" in header else None
    probe_names = header[i_c + 1:i_evt]

    dates, opens, highs, lows, closes = [], [], [], [], []
    probes = {n: [] for n in probe_names}
    bar_fills = []  # per bar: [(side, price, qty, tag, fee), ...]
    nan = float("nan")
    for row in rows[1:]:
        if not row:
            continue
        dates.append(datetime.strptime(row[i_dt] + " " + row[i_tm],
                                       "%m/%d/%y %I:%M %p"))
        opens.append(float(row[i_o]))
        highs.append(float(row[i_h]))
        lows.append(float(row[i_l]))
        closes.append(float(row[i_c]))
        for j, name in enumerate(probe_names):
            cell = row[i_c + 1 + j]
            probes[name].append(float(cell) if cell else nan)
        fills = []
        events = [e.strip() for e in row[i_evt].split("|")
                  if e.strip().endswith("filled")]
        if events:
            prices, qtys = row[i_px].split("|"), row[i_qt].split("|")
            costs = row[i_ct].split("|") if i_ct is not None else ["0"] * len(events)
            for event, price, qty, cost in zip(events, prices, qtys, costs):
                tag = event[: -len("filled")].strip()
                fills.append((fill_side(tag), float(price), float(qty), tag,
                              float(cost or 0)))
        bar_fills.append(fills)
    return dates, opens, highs, lows, closes, probes, bar_fills


def is_zero(x):
    return abs(x) < 1e-7


def sgn(x):
    return 0 if is_zero(x) else (1 if x > 0 else -1)


def ledger(dates, closes, bar_fills, lot_size, initial):
    """FIFO lot ledger over the bars.

    Returns (equity, net_lots, realized, trades): equity is mark-to-market,
    trades are rich round trips with entry/exit bar index, datetime, price,
    direction, size and pnl — everything the charts need for markers and
    connectors.
    """
    realized, lots = 0.0, []
    equity, net, realized_series, trades = [], [], [], []
    for i, (dt, close, fills) in enumerate(zip(dates, closes, bar_fills)):
        for side, price, qty, tag, fee in fills:
            realized -= fee
            this_fpl = fee / qty if qty else 0.0
            remaining = side * qty
            while lots and not is_zero(remaining) \
                    and sgn(lots[0][1]) == -sgn(remaining):
                entry_price, lot_qty, entry_i, entry_fpl = lots[0]
                lots = lots[1:]
                direction = float(sgn(lot_qty))
                closed = min(abs(lot_qty), abs(remaining))
                pnl = direction * closed * lot_size * (price - entry_price)
                realized += pnl
                trades.append({
                    "dir": "long" if direction > 0 else "short",
                    "lots": closed,
                    "pnl": pnl - closed * (entry_fpl + this_fpl),
                    "entry_i": entry_i, "entry_dt": dates[entry_i],
                    "entry_px": entry_price,
                    "exit_i": i, "exit_dt": dt, "exit_px": price,
                    "exit_tag": tag,
                })
                left = lot_qty - direction * closed
                if not is_zero(left):
                    lots = [(entry_price, left, entry_i, entry_fpl)] + lots
                remaining += direction * closed
            if not is_zero(remaining):
                lots.append((price, remaining, i, this_fpl))
        unrealized = sum(sgn(q) * abs(q) * lot_size * (close - p)
                         for p, q, _, _ in lots)
        equity.append(initial + realized + unrealized)
        realized_series.append(realized)
        net.append(sum(q for _, q, _, _ in lots))
    return equity, net, realized_series, trades


def load_data(args):
    """Shared context every chart builder consumes."""
    if args.csv:
        text = Path(args.csv).read_text()
        data_name = Path(args.csv).name
    else:
        text, err = run_backtest(DUNE_TARGETS[args.strategy])
        data_name = "backtest"
        if "filled" not in text:
            sys.exit(f"no fills found in strategy output; stderr was:\n{err}")
    initial = read_ocaml_literal("initial_balance", 3_000_000.0)
    lot_size = read_ocaml_literal("lot_size", 50.0)
    dates, o, h, l, c, probes, bar_fills = parse_stream(text)
    equity, net, realized, trades = ledger(dates, c, bar_fills, lot_size, initial)
    if not trades:
        print("warning: no round-trip trades found in the data",
              file=sys.stderr)
    return {
        "dates": dates, "open": o, "high": h, "low": l, "close": c,
        "probes": probes, "bar_fills": bar_fills,
        "equity": equity, "net": net, "realized": realized, "trades": trades,
        "initial": initial, "lot_size": lot_size,
        "strategy": args.strategy, "data": data_name, "args": args,
    }


# ---------------------------------------------------------------- chart helpers


def index_axis(dates):
    """Gapless integer x-axis (TradingView collapses session gaps the same
    way): returns (xs, tickvals, ticktext, hover date strings)."""
    n = len(dates)
    xs = list(range(n))
    step = max(1, n // 12)
    tickvals = xs[::step]
    ticktext = [f"{dates[i]:%d %b %y}" for i in tickvals]
    datestr = [f"{d:%a %d %b %y %H:%M}" for d in dates]
    return xs, tickvals, ticktext, datestr


def set_index_ticks(fig, tickvals, ticktext, axis="xaxis"):
    fig.update_layout(**{axis: dict(tickmode="array", tickvals=tickvals,
                                    ticktext=ticktext)})


# WebGL rendering: tens of thousands of candlesticks as SVG nodes freeze the
# browser, so price charts are drawn as Scattergl subpaths (wick line +
# filled body rectangle per candle, None-separated), which the GPU handles.


def _split_by_direction(o, c):
    for oo, cc in zip(o, c):
        yield cc >= oo


def webgl_candles(fig, xs, o, h, l, c, theme, row=None, col=None):
    """Candlesticks as two WebGL traces (up / down)."""
    kw = dict(row=row, col=col) if row else {}
    up, dn = {"x": [], "y": []}, {"x": [], "y": []}
    for x, oo, hh, ll, cc, is_up in zip(xs, o, h, l, c,
                                        _split_by_direction(o, c)):
        tgt = up if is_up else dn
        w = 0.35
        top, bot = max(oo, cc), min(oo, cc)
        tgt["x"] += [x, x, None, x - w, x + w, x + w, x - w, x - w, None]
        tgt["y"] += [hh, ll, None, top, top, bot, bot, top, None]
    t = THEMES[theme]
    for seg, color, name in ((up, t["up"], "Up"), (dn, t["down"], "Down")):
        fig.add_trace(go.Scattergl(
            x=seg["x"], y=seg["y"], mode="lines", fill="toself",
            fillcolor=color, line=dict(color=color, width=1),
            hoverinfo="skip", name=name, showlegend=False,
        ), **kw)


def webgl_ohlc_bars(fig, xs, o, h, l, c, theme, row=None, col=None):
    """Classic OHLC bars (left open tick, right close tick) as WebGL."""
    kw = dict(row=row, col=col) if row else {}
    up, dn = {"x": [], "y": []}, {"x": [], "y": []}
    for x, oo, hh, ll, cc, is_up in zip(xs, o, h, l, c,
                                        _split_by_direction(o, c)):
        tgt = up if is_up else dn
        w = 0.32
        tgt["x"] += [x, x, None, x - w, x, None, x, x + w, None]
        tgt["y"] += [hh, ll, None, oo, oo, None, cc, cc, None]
    t = THEMES[theme]
    for seg, color, name in ((up, t["up"], "Up"), (dn, t["down"], "Down")):
        fig.add_trace(go.Scattergl(
            x=seg["x"], y=seg["y"], mode="lines",
            line=dict(color=color, width=1.2),
            hoverinfo="skip", name=name, showlegend=False,
        ), **kw)


def add_hover_helper(fig, xs, ohlc, datestr, row=None, col=None):
    """Invisible close line that feeds the unified hover with the bar's full
    OHLC (TradingView crosshair style)."""
    kw = dict(row=row, col=col) if row else {}
    o, h, l, c = ohlc
    fig.add_trace(go.Scattergl(
        x=xs, y=c, mode="lines", line=dict(width=0), opacity=0,
        hovertext=datestr, customdata=list(zip(o, h, l)),
        hovertemplate=("%{hovertext}<br>"
                       "O %{customdata[0]:,.2f}  H %{customdata[1]:,.2f}"
                       "<br>L %{customdata[2]:,.2f}  C %{y:,.2f}"),
        name="close", showlegend=False,
    ), **kw)


# what each order tag does (for the fill hover); fill side comes from
# plot_pnl.SIDE_BY_TAG
TAG_ACTION = {
    "LE": "Enter long", "SE": "Enter short",
    "2NL": "Exit long · 2N stop", "TRL": "Exit long · chandelier",
    "SE cover": "Exit long · reverse stop",
    "2NS": "Exit short · 2N stop", "TRS": "Exit short · chandelier",
    "LE cover": "Exit short · reverse stop",
    "TP_L": "Exit long · take profit", "SL_L": "Exit long · stop loss",
    "TS_L": "Exit long · trailing stop",
    "TP_S": "Exit short · take profit", "SL_S": "Exit short · stop loss",
    "TS_S": "Exit short · trailing stop",
}


def stop_level(ctx, tag, i):
    """The trigger level of the order that produced this fill.

    The level comes from the probe named in the order's (Stop <probe>)
    argument in bin/*.ml, so this works for any strategy. Timing
    (lib/backtest.ml): the strategy evaluates on bar t's close and its orders
    rest into bar t+1, whose range they fill against — so the order behind a
    fill on bar i was placed at the close of bar i-1 with the level it had
    then.
    """
    j = i - 1
    if j < 0:
        return None
    spec = ORDER_SPECS.get(tag)
    series = ctx["probes"].get(spec["stop"]) if spec else None
    if not series:
        return None
    lvl = series[j]
    if math.isnan(lvl):
        return None
    return lvl


def add_trade_markers(fig, ctx, xs, row=None, col=None, connectors=True):
    """TradingView-style trade markers.

    Entry fills (enter_* orders) are triangles beyond the bar's wick — buys
    below the low, sells above the high; exit fills are small circles at the
    fill price. Round trips are joined by dotted connectors colored by PnL.
    The hover of every fill states the order's trigger level (the probe in
    its (Stop <probe>) argument, placed at the prior bar's close), the fill
    price and the slippage between them.
    """
    kw = dict(row=row, col=col) if row else {}
    dates, atr = ctx["dates"], ctx["probes"].get("atr", [])
    long_e = {"x": [], "y": [], "text": []}
    short_e = {"x": [], "y": [], "text": []}
    exits = {"x": [], "y": [], "text": [], "color": []}
    for i, fills in enumerate(ctx["bar_fills"]):
        a = atr[i] if i < len(atr) and not math.isnan(atr[i]) else None
        for side, price, qty, tag, fee in fills:
            spec = ORDER_SPECS.get(tag)
            stop = stop_level(ctx, tag, i)
            action = TAG_ACTION.get(tag, tag)
            text = f"{action} · {qty:g} lots"
            if fee:
                text += f" · fee {fee:,.0f}"
            if stop is not None:
                slip = side * (price - stop)
                text += (f"<br>{tag} stop placed "
                         f"{dates[i - 1]:%d %b %y %H:%M} @ {stop:,.2f}"
                         f"<br>filled {dates[i]:%d %b %y %H:%M} @ {price:,.2f}")
                if abs(slip) > 1e-9:
                    text += f" · slip {slip:+.2f}"
            else:
                text += f"<br>{tag} filled {dates[i]:%d %b %y %H:%M} @ {price:,.2f}"
            off = 0.5 * a if a else 0.005 * price
            entry = spec["entry"] if spec else "enter" in action.lower()
            if entry:
                tgt = long_e if side > 0 else short_e
                y = ctx["low"][i] - off if side > 0 else ctx["high"][i] + off
            else:
                tgt, y = exits, price
                exits["color"].append(BUY if side > 0 else SELL)
            tgt["x"].append(xs[i])
            tgt["y"].append(y)
            tgt["text"].append(text)
    fig.add_trace(go.Scattergl(
        x=long_e["x"], y=long_e["y"], text=long_e["text"], mode="markers",
        name="Long entry", hoverinfo="text",
        marker=dict(symbol="triangle-up", size=11, color=BUY,
                    line=dict(width=0.5, color="rgba(0,0,0,0.4)")),
    ), **kw)
    fig.add_trace(go.Scattergl(
        x=short_e["x"], y=short_e["y"], text=short_e["text"], mode="markers",
        name="Short entry", hoverinfo="text",
        marker=dict(symbol="triangle-down", size=11, color=SELL,
                    line=dict(width=0.5, color="rgba(0,0,0,0.4)")),
    ), **kw)
    fig.add_trace(go.Scattergl(
        x=exits["x"], y=exits["y"], text=exits["text"], mode="markers",
        name="Exit fill", hoverinfo="text",
        marker=dict(symbol="circle", size=6, color=exits["color"],
                    line=dict(width=0.5, color="rgba(0,0,0,0.4)")),
    ), **kw)
    if connectors:
        for won in (True, False):
            cx, cy = [], []
            for t in ctx["trades"]:
                if (t["pnl"] > 0) != won:
                    continue
                cx += [xs[t["entry_i"]], xs[t["exit_i"]], None]
                cy += [t["entry_px"], t["exit_px"], None]
            fig.add_trace(go.Scattergl(
                x=cx, y=cy, mode="lines", hoverinfo="skip",
                name="Winning trades" if won else "Losing trades",
                line=dict(color=WIN if won else LOSS, width=1.2),
                opacity=0.55,
            ), **kw)


def probe_overlays(fig, ctx, xs, row=None, col=None):
    """Every price-scale probe of the strategy as a thin line.

    Names come straight from the CSV header (the strategy's probe list), so
    an arbitrary strategy is overlaid without viz knowing its indicators.
    """
    kw = dict(row=row, col=col) if row else {}
    for i, name in enumerate(price_probes(ctx)):
        series = ctx["probes"][name]
        color, width = probe_style(name, i)
        ys = [None if math.isnan(v) else v for v in series]
        fig.add_trace(go.Scattergl(
            x=xs, y=ys, mode="lines", name=name, hoverinfo="y+name",
            line=dict(color=color, width=width),
        ), **kw)


def stats_footer(ctx):
    """One-line watermark: CAGR / Sharpe / maxDD on the mark-to-market curve."""
    eq, dates, initial = ctx["equity"], ctx["dates"], ctx["initial"]
    day_eq = {}
    for d, e in zip(dates, eq):
        day_eq[d.date()] = e
    days = sorted(day_eq)
    if len(days) < 2:
        return ""
    vals = [day_eq[d] for d in days]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    years = (days[-1] - days[0]).days / 365.25 or 1e-9
    ppy = len(days) / years
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1))
    sharpe = mean / sd * math.sqrt(ppy) if sd else float("nan")
    cagr = (vals[-1] / initial) ** (1 / years) - 1
    peak, maxdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        maxdd = min(maxdd, v / peak - 1)
    return (f"CAGR {cagr:+.1%}   Sharpe {sharpe:.2f}   "
            f"max DD {maxdd:.1%}   trades {len(ctx['trades'])}")


def base_title(ctx, what):
    return (f"{what} — {ctx['strategy']} on {ctx['data']} · "
            f"{len(ctx['dates']):,} bars")


# ---------------------------------------------------------------- price charts


def make_candles(ctx, theme, ohlc_bars=False):
    xs, tv, tt, ds = index_axis(ctx["dates"])
    fig = go.Figure()
    if ohlc_bars:
        webgl_ohlc_bars(fig, xs, ctx["open"], ctx["high"], ctx["low"],
                        ctx["close"], theme)
    else:
        webgl_candles(fig, xs, ctx["open"], ctx["high"], ctx["low"],
                      ctx["close"], theme)
    add_hover_helper(fig, xs, (ctx["open"], ctx["high"], ctx["low"],
                               ctx["close"]), ds)
    probe_overlays(fig, ctx, xs)
    add_trade_markers(fig, ctx, xs)
    add_preset_buttons(fig, theme)
    set_index_ticks(fig, tv, tt)
    what = "OHLC bars" if ohlc_bars else "Candlesticks"
    fig.update_layout(title=dict(text=base_title(ctx, what),
                                 font=dict(size=16)),
                      yaxis_title="Price")
    return fig


def make_heikin_ashi(ctx, theme):
    o, h, l, c = ctx["open"], ctx["high"], ctx["low"], ctx["close"]
    ha_c = [(a + b + d + e) / 4 for a, b, d, e in zip(o, h, l, c)]
    ha_o = [(o[0] + c[0]) / 2]
    for i in range(1, len(o)):
        ha_o.append((ha_o[-1] + ha_c[i - 1]) / 2)
    ha_h = [max(a, b, d) for a, b, d in zip(h, ha_o, ha_c)]
    ha_l = [min(a, b, d) for a, b, d in zip(l, ha_o, ha_c)]
    xs, tv, tt, ds = index_axis(ctx["dates"])
    fig = go.Figure()
    webgl_candles(fig, xs, ha_o, ha_h, ha_l, ha_c, theme)
    add_hover_helper(fig, xs, (ctx["open"], ctx["high"], ctx["low"],
                               ctx["close"]), ds)
    probe_overlays(fig, ctx, xs)
    add_trade_markers(fig, ctx, xs, connectors=False)
    add_preset_buttons(fig, theme)
    set_index_ticks(fig, tv, tt)
    fig.update_layout(title=dict(text=base_title(ctx, "Heikin-Ashi"),
                                 font=dict(size=16)),
                      yaxis_title="Price")
    return fig


# ------------------------------------------------------- timeless chart math


def default_brick(ctx):
    """ATR if the probes carry it, else 1/80th of the close range."""
    atr = [v for v in ctx["probes"].get("atr", []) if not math.isnan(v)]
    if atr:
        return atr[-1]
    c = ctx["close"]
    return (max(c) - min(c)) / 80 or 1.0


def renko(closes, dates, brick):
    """Fixed-brick Renko on closes -> (opens, closes, up flags, dates)."""
    bos, bcs, ups, bds = [], [], [], []
    base = math.floor(closes[0] / brick) * brick
    for px, dt in zip(closes, dates):
        while px - base >= brick:
            bos.append(base)
            base += brick
            bcs.append(base)
            ups.append(True)
            bds.append(dt)
        while base - px >= brick:
            bos.append(base)
            base -= brick
            bcs.append(base)
            ups.append(False)
            bds.append(dt)
    return bos, bcs, ups, bds


def line_break(closes, dates):
    """Three-line break on closes -> (opens, closes, up flags, dates).

    A new line extends in the current direction whenever the close passes the
    last line's close; a reversal line prints only when the close breaks the
    extreme of the previous three lines.
    """
    los, lcs, ups, lds = [], [], [], []
    if len(closes) < 2:
        return los, lcs, ups, lds
    los.append(closes[0])
    lcs.append(closes[1])
    ups.append(closes[1] >= closes[0])
    lds.append(dates[1])
    for px, dt in zip(closes[2:], dates[2:]):
        last = lcs[-1]
        if ups[-1]:
            if px > last:
                los.append(last); lcs.append(px); ups.append(True); lds.append(dt)
            elif px < min(min(a, b) for a, b in zip(los[-3:], lcs[-3:])):
                los.append(last); lcs.append(px); ups.append(False); lds.append(dt)
        else:
            if px < last:
                los.append(last); lcs.append(px); ups.append(False); lds.append(dt)
            elif px > max(max(a, b) for a, b in zip(los[-3:], lcs[-3:])):
                los.append(last); lcs.append(px); ups.append(True); lds.append(dt)
    return los, lcs, ups, lds


def kagi(closes, reversal):
    """Close-based Kagi -> (xs, ys, thick flags per reversal block).

    The line extends while the close makes new extremes and flips direction
    on a retrace of `reversal`; it is thick (yang) above the previous swing
    high and thin (yin) below the previous swing low.
    """
    verts = [[0, closes[0]]]  # [x, price] vertices, one per reversal
    extreme = closes[0]
    direction = 0  # +1 rising, -1 falling, 0 undetermined
    for px in closes[1:]:
        if direction == 0:
            if px != extreme:
                direction = 1 if px > extreme else -1
                extreme = px
                verts[-1][1] = px
        elif direction > 0:
            if px >= extreme:
                extreme = px
                verts[-1][1] = px
            elif extreme - px >= reversal:
                direction = -1
                extreme = px
                verts.append([verts[-1][0] + 1, px])
        else:
            if px <= extreme:
                extreme = px
                verts[-1][1] = px
            elif px - extreme >= reversal:
                direction = 1
                extreme = px
                verts.append([verts[-1][0] + 1, px])
    # step the polyline right at every reversal: vertical then horizontal
    xs, ys = [verts[0][0]], [verts[0][1]]
    for (x0, y0), (x1, y1) in zip(verts, verts[1:]):
        xs += [x1, x1]
        ys += [y0, y1]
    # one thick flag per block; block bi spans xs[2bi .. 2bi+2]
    swing_hi = swing_lo = verts[0][1]
    thick = []
    for _, y_to in verts[1:]:
        thick.append(y_to > swing_hi)
        swing_hi = max(swing_hi, y_to)
        swing_lo = min(swing_lo, y_to)
    return xs, ys, thick


def point_and_figure(closes, box, rev_boxes=3):
    """Close-method P&F -> list of (is_up, [box levels]) columns."""
    cols = []
    direction = 0
    for px in closes:
        lvl = math.floor(px / box) * box
        if not cols:
            cols.append((True, [lvl]))
            continue
        cur_up, levels = cols[-1]
        top, bot = levels[-1], levels[0]
        if cur_up:
            if lvl > top:
                levels.extend(range(int(top + box), int(lvl + box / 2),
                                    int(box)))
            elif top - lvl >= rev_boxes * box:
                new = list(range(int(top - box), int(lvl - box / 2),
                                 -int(box)))
                cols.append((False, new))
        else:
            if lvl < bot:
                levels.extend(range(int(bot - box), int(lvl - box / 2),
                                    -int(box)))
            elif lvl - bot >= rev_boxes * box:
                new = list(range(int(bot + box), int(lvl + box / 2),
                                 int(box)))
                cols.append((True, new))
    return cols


def boxes_figure(ctx, theme, opens, closes, ups, dts, what, unit_desc):
    """Shared renderer for Renko / Line Break style box charts."""
    xs = list(range(len(opens)))
    fig = go.Figure()
    ds = [f"#{i} · {d:%d %b %y %H:%M}" for i, d in enumerate(dts)]
    highs = [max(a, b) for a, b in zip(opens, closes)]
    lows = [min(a, b) for a, b in zip(opens, closes)]
    webgl_candles(fig, xs, opens, highs, lows, closes, theme)
    add_hover_helper(fig, xs, (opens, highs, lows, closes), ds)
    step = max(1, len(xs) // 12)
    fig.update_layout(
        xaxis=dict(tickmode="array", tickvals=xs[::step],
                   ticktext=[f"{dts[i]:%d %b %y}" for i in range(0, len(xs), step)]),
        title=dict(text=base_title(ctx, f"{what} ({unit_desc})"),
                   font=dict(size=16)),
        yaxis_title="Price",
    )
    return fig


def make_renko(ctx, theme):
    brick = ctx["args"].brick or default_brick(ctx)
    bos, bcs, ups, bds = renko(ctx["close"], ctx["dates"], brick)
    return boxes_figure(ctx, theme, bos, bcs, ups, bds, "Renko",
                        f"brick {brick:,.2f}")


def make_linebreak(ctx, theme):
    los, lcs, ups, lds = line_break(ctx["close"], ctx["dates"])
    return boxes_figure(ctx, theme, los, lcs, ups, lds, "Line Break",
                        "3-line")


def make_kagi(ctx, theme):
    rev = ctx["args"].reversal or 2 * default_brick(ctx)
    xs, ys, thick = kagi(ctx["close"], rev)
    t = THEMES[theme]
    fig = go.Figure()
    for is_thick in (True, False):
        sx, sy = [], []
        for bi, flag in enumerate(thick):
            if flag != is_thick:
                continue
            i = bi * 2
            sx += [xs[i], xs[i + 1], xs[i + 2], None]
            sy += [ys[i], ys[i + 1], ys[i + 2], None]
        fig.add_trace(go.Scattergl(
            x=sx, y=sy, mode="lines", hoverinfo="y",
            name="Yang (thick)" if is_thick else "Yin (thin)",
            line=dict(color=t["up"] if is_thick else t["down"],
                      width=3 if is_thick else 1.2),
        ))
    fig.update_layout(title=dict(text=base_title(ctx, f"Kagi (reversal {rev:,.2f})"),
                                 font=dict(size=16)),
                      yaxis_title="Price")
    return fig


def make_pnf(ctx, theme):
    # classic percentage box: 1% of the median close (flag overrides)
    box = ctx["args"].brick or 0.01 * sorted(ctx["close"])[len(ctx["close"]) // 2]
    cols = point_and_figure(ctx["close"], box)
    t = THEMES[theme]
    xx, yy, glyphs, colors = [], [], [], []
    for ci, (is_up, levels) in enumerate(cols):
        for lvl in levels:
            xx.append(ci)
            yy.append(lvl)
            glyphs.append("✕" if is_up else "○")
            colors.append(t["up"] if is_up else t["down"])
    fig = go.Figure(go.Scatter(
        x=xx, y=yy, mode="text", text=glyphs, hoverinfo="y",
        name="P&F",
        textfont=dict(size=11, color=colors, family="Menlo, monospace"),
    ))
    fig.update_layout(title=dict(
        text=base_title(ctx, f"Point & Figure (box {box:,.2f} × 3)"),
        font=dict(size=16)), yaxis_title="Price",
        xaxis=dict(title="column", tickmode="linear", dtick=1,
                   range=[-1, len(cols) + 1]))
    return fig


# ------------------------------------------------------------- analytics


def daily_equity(ctx):
    day_eq, day_close = {}, {}
    for d, e, c in zip(ctx["dates"], ctx["equity"], ctx["close"]):
        day_eq[d.date()] = e
        day_close[d.date()] = c
    days = sorted(day_eq)
    return days, [day_eq[d] for d in days], [day_close[d] for d in days]


def make_equity(ctx, theme):
    dates, eq, initial = ctx["dates"], ctx["equity"], ctx["initial"]
    t = THEMES[theme]
    peak, peaks = float("-inf"), []
    for v in eq:
        peak = max(peak, v)
        peaks.append(peak)
    dd_pct = [(v - p) / p * 100 for v, p in zip(eq, peaks)]
    bh = [initial * c / ctx["close"][0] for c in ctx["close"]]

    days, day_eq, _ = daily_equity(ctx)
    rets = [day_eq[i] / day_eq[i - 1] - 1 for i in range(1, len(day_eq))]
    window = 126
    ppy = len(days) / max(1e-9, (days[-1] - days[0]).days / 365.25)
    roll_x, roll_y = [], []
    for i in range(window, len(days)):
        w = rets[i - window:i]
        m = sum(w) / len(w)
        s = math.sqrt(sum((r - m) ** 2 for r in w) / (len(w) - 1))
        roll_x.append(days[i])
        roll_y.append(m / s * math.sqrt(ppy) if s else float("nan"))

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.55, 0.25, 0.2],
        subplot_titles=("Equity vs buy & hold (mark-to-market)",
                        "Underwater drawdown",
                        f"Rolling {window}-session Sharpe"),
    )
    fig.add_trace(go.Scattergl(x=dates, y=eq, name="Account equity",
                               line=dict(color=t["line"], width=1.4),
                               hovertemplate="%{y:,.0f}"), row=1, col=1)
    fig.add_trace(go.Scattergl(x=dates, y=peaks, name="High-water mark",
                               line=dict(color=NEUTRAL, width=1),
                               hovertemplate="%{y:,.0f}"), row=1, col=1)
    fig.add_trace(go.Scattergl(x=dates, y=bh, name="Buy & hold",
                               line=dict(color=t["up"], width=1),
                               opacity=0.85,
                               hovertemplate="%{y:,.0f}"), row=1, col=1)
    fig.add_trace(go.Scattergl(x=dates, y=dd_pct, name="Drawdown %",
                               fill="tozeroy",
                               fillcolor="rgba(239,83,80,0.35)",
                               line=dict(color=t["down"], width=1),
                               hovertemplate="%{y:.2f}%"), row=2, col=1)
    fig.add_trace(go.Scattergl(x=roll_x, y=roll_y, name="Rolling Sharpe",
                               line=dict(color="#ab47bc", width=1.2),
                               hovertemplate="%{y:.2f}"), row=3, col=1)
    fig.add_hline(y=0, line=dict(color=NEUTRAL, width=1, dash="dot"),
                  row=3, col=1)
    fig.update_layout(title=dict(text=base_title(ctx, "Equity tear sheet"),
                                 font=dict(size=16)),
                      yaxis_title="Equity ($)", yaxis2_title="DD (%)",
                      yaxis3_title="Sharpe")
    return fig


def make_heatmap(ctx, theme):
    days, day_eq, _ = daily_equity(ctx)
    month_end = {}
    for d, e in zip(days, day_eq):
        month_end[(d.year, d.month)] = e
    monthly, annual = {}, {}
    prev = ctx["initial"]
    for key in sorted(month_end):
        r = month_end[key] / prev - 1
        monthly[key] = r
        prev = month_end[key]
    for y in {k[0] for k in monthly}:
        rs = [monthly[(y, m)] for m in range(1, 13) if (y, m) in monthly]
        a = 1.0
        for r in rs:
            a *= 1 + r
        annual[y] = a - 1
    years = sorted(annual)

    lim = max(0.01, max(abs(r) for r in monthly.values()))
    z = [[monthly.get((y, m), None) * 100 for m in range(1, 13)]
         for y in years]
    text = [[f"{monthly[(y, m)] * 100:+.1f}" if (y, m) in monthly else ""
             for m in range(1, 13)] for y in years]
    za = [[annual[y] * 100] for y in years]
    texta = [[f"{annual[y] * 100:+.1f}"] for y in years]

    fig = make_subplots(rows=1, cols=2, column_widths=[0.88, 0.12],
                        shared_yaxes=True, horizontal_spacing=0.015)
    scale = [[0.0, THEMES[theme]["down"]], [0.5, THEMES[theme]["bg"]],
             [1.0, THEMES[theme]["up"]]]
    fig.add_trace(go.Heatmap(
        z=z, x=list(MONTHS), y=[str(y) for y in years], text=text,
        texttemplate="%{text}", colorscale=scale, zmid=0,
        zmin=-lim * 100, zmax=lim * 100, showscale=False,
        xgap=2, ygap=2, hovertemplate="%{y} %{x}: %{text}%<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Heatmap(
        z=za, x=["Year"], y=[str(y) for y in years], text=texta,
        texttemplate="%{text}", colorscale=scale, zmid=0,
        zmin=-lim * 100, zmax=lim * 100, showscale=False,
        xgap=2, ygap=2, hovertemplate="%{y}: %{text}%<extra></extra>",
    ), row=1, col=2)
    fig.update_layout(title=dict(
        text=base_title(ctx, "Monthly returns (%)"), font=dict(size=16)))
    fig.update_yaxes(autorange="reversed")
    return fig


def make_trades(ctx, theme):
    trades = ctx["trades"]
    t = THEMES[theme]
    cum, run = [], 0.0
    for tr in trades:
        run += tr["pnl"]
        cum.append(run)
    xs = [tr["exit_dt"] for tr in trades]
    colors = [WIN if tr["pnl"] > 0 else LOSS for tr in trades]
    hover = [
        f"{tr['dir']} {tr['lots']:g} lots · {tr['pnl']:+,.0f}"
        f"<br>{tr['entry_dt']:%d %b %y} → {tr['exit_dt']:%d %b %y}"
        f"<br>{tr['entry_px']:,.2f} → {tr['exit_px']:,.2f}"
        for tr in trades
    ]
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=False, vertical_spacing=0.09,
        row_heights=[0.45, 0.3, 0.25],
        subplot_titles=("Cumulative realized PnL", "Per-trade PnL",
                        "Trade PnL distribution"),
    )
    fig.add_trace(go.Scatter(x=xs, y=cum, name="Cumulative PnL",
                             fill="tozeroy",
                             fillcolor="rgba(41,98,255,0.15)",
                             line=dict(color=t["line"], width=1.4),
                             hovertemplate="%{y:+,.0f}"), row=1, col=1)
    fig.add_trace(go.Bar(x=xs, y=[tr["pnl"] for tr in trades],
                         name="Per-trade PnL", marker_color=colors,
                         hovertext=hover, hoverinfo="text"), row=2, col=1)
    pnls = [tr["pnl"] for tr in trades]
    fig.add_trace(go.Histogram(x=pnls, nbinsx=40, name="Distribution",
                               marker_color=t["line"], opacity=0.75,
                               hovertemplate="%{y} trades<extra></extra>"),
                  row=3, col=1)
    fig.update_layout(title=dict(text=base_title(ctx, "Trade analytics"),
                                 font=dict(size=16)),
                      yaxis_title="PnL ($)", yaxis2_title="PnL ($)",
                      xaxis3_title="PnL per trade ($)", yaxis3_title="Count",
                      bargap=0.05)
    return fig


def make_exposure(ctx, theme):
    xs, tv, tt, ds = index_axis(ctx["dates"])
    t = THEMES[theme]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scattergl(
        x=xs, y=ctx["net"], name="Net position (lots)", line_shape="hv",
        fill="tozeroy", fillcolor="rgba(247,166,0,0.18)",
        line=dict(color="#f7a600", width=1.3), hovertext=ds,
        hovertemplate="%{y:g} lots"), secondary_y=False)
    fig.add_trace(go.Scattergl(
        x=xs, y=ctx["close"], name="Price", hovertext=ds,
        line=dict(color=t["line"], width=1), opacity=0.8,
        hovertemplate="%{y:,.2f}"), secondary_y=True)
    set_index_ticks(fig, tv, tt)
    exposure = sum(1 for q in ctx["net"] if not is_zero(q)) / len(ctx["net"])
    fig.update_layout(title=dict(
        text=base_title(ctx, f"Exposure — in market {exposure:.1%} of bars"),
        font=dict(size=16)), yaxis_title="Net lots",
        yaxis2_title="Price")
    return fig


# ---------------------------------------------------------------- registry


CHARTS = {
    "candles": ("Candlesticks + trade markers", make_candles),
    "ohlc": ("OHLC bars + trade markers",
             lambda ctx, theme: make_candles(ctx, theme, ohlc_bars=True)),
    "heikin-ashi": ("Heikin-Ashi + trade markers", make_heikin_ashi),
    "renko": ("Renko (timeless)", make_renko),
    "kagi": ("Kagi (timeless)", make_kagi),
    "pnf": ("Point & Figure (timeless)", make_pnf),
    "linebreak": ("Three-line break (timeless)", make_linebreak),
    "equity": ("Equity / drawdown / rolling Sharpe tear sheet", make_equity),
    "heatmap": ("Monthly returns heatmap", make_heatmap),
    "trades": ("Trade analytics (PnL, distribution)", make_trades),
    "exposure": ("Net position exposure", make_exposure),
}
UNAVAILABLE = [("volume", "feed has no volume data")]


def render(name, ctx, theme, out_base, want_png):
    label, builder = CHARTS[name]
    html = out_base.with_suffix(".html")
    if name == "candles":
        # candles gets the fast canvas renderer (scripts/lwc.py, TradingView
        # Lightweight Charts); plotly is only used for the optional PNG
        import lwc

        lwc.render_candles(ctx, theme, html)
        print(f"{label}\n  html: {html}")
        if want_png:
            fig = builder(ctx, theme)
            apply_theme(fig, theme, stats_footer(ctx))
            _write_png(fig, out_base.with_suffix(".png"))
            print(f"  png:  {out_base.with_suffix('.png')}")
        return
    fig = builder(ctx, theme)
    apply_theme(fig, theme, stats_footer(ctx))
    fig.write_html(html, include_plotlyjs="cdn",
                   config=dict(scrollZoom=True, displaylogo=False,
                               modeBarButtonsToRemove=["lasso2d", "select2d"]))
    print(f"{label}\n  html: {html}")
    if want_png:
        png = out_base.with_suffix(".png")
        _write_png(fig, png)
        print(f"  png:  {png}")


def _write_png(fig, png):
    # direct kaleido call: plotly's write_image() gives no way to raise the
    # 90 s render timeout, which big WebGL charts can exceed
    import kaleido

    kaleido.write_fig_sync(
        fig.to_dict(), path=str(png),
        opts=dict(format="png", width=1680, height=940, scale=2),
        kopts={"timeout": 300},
    )


def menu():
    print("\nAvailable visualizations:\n")
    keys = list(CHARTS)
    for i, key in enumerate(keys, 1):
        print(f"  {i:>2}. {key:<12} {CHARTS[key][0]}")
    for key, why in UNAVAILABLE:
        print(f"      {key:<12} (unavailable: {why})")
    print("\n   a. all of the above")
    try:
        choice = input("\nPick a number, a name, or 'a' for all: ").strip().lower()
    except EOFError:
        sys.exit("no stdin; pass a chart name, e.g.: python3 scripts/viz.py candles")
    if choice in ("a", "all"):
        return keys
    if choice.isdigit() and 1 <= int(choice) <= len(keys):
        return [keys[int(choice) - 1]]
    if choice in CHARTS:
        return [choice]
    sys.exit(f"unknown choice: {choice!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chart", nargs="?", choices=list(CHARTS) + ["all"],
                    help="which visualization to render (omit for the menu)")
    ap.add_argument("--strategy", choices=list(DUNE_TARGETS), default="main",
                    help="which bin/ strategy to run (default: main)")
    ap.add_argument("--csv", type=Path,
                    help="load a saved backtest CSV stream instead of running dune")
    ap.add_argument("--out", type=Path,
                    help="output path stem (default: <chart>.html in project root)")
    ap.add_argument("--png", action="store_true",
                    help="also write a static PNG via kaleido")
    ap.add_argument("--style", choices=list(THEMES), default="dark",
                    help="theme at render time (HTML also gets a Dark/Light toggle)")
    ap.add_argument("--brick", type=float, default=0.0,
                    help="Renko brick / P&F box size (default: last ATR probe)")
    ap.add_argument("--reversal", type=float, default=0.0,
                    help="Kagi reversal threshold (default: 2 bricks)")
    args = ap.parse_args()

    ctx = load_data(args)
    names = list(CHARTS) if args.chart == "all" else \
        [args.chart] if args.chart else menu()
    for name in names:
        out_base = args.out or (ROOT / name)
        render(name, ctx, args.style, out_base, args.png)


if __name__ == "__main__":
    main()
