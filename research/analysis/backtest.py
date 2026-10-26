"""Backtest parsing and ledger helpers for tearsheets."""

import csv
import io
import sys
from datetime import datetime

from plot_pnl import SIDE_BY_TAG, is_zero, sgn


TAGS = {}


def side_for(tag):
    if tag in TAGS:
        return TAGS[tag]
    if tag in SIDE_BY_TAG:
        return SIDE_BY_TAG[tag]
    raise KeyError(tag)

def parse_tags(out):
    tags = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("# tags:"):
            continue
        for tok in line[len("# tags:"):].split():
            if "=" in tok:
                name, side = tok.rsplit("=", 1)
                tags[name] = int(side)
    return tags

def parse_bars(out):
    """Every bar of the backtest's CSV stream as (datetime, close, fills)."""
    lines = [ln for ln in out.splitlines() if not ln.lstrip().startswith("#")]
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    if not rows:
        return []
    header = None
    for r in rows:
        if r and "Date" in r and "event" in r:
            header = r
            rows = rows[rows.index(r):]
            break
    if header is None:
        return []
    i_dt, i_tm = header.index("Date"), header.index("Time")
    i_cl = header.index("Close")
    i_evt, i_px, i_qt = (header.index(c) for c in ("event", "fill_price", "fill_qty"))
    i_ct = header.index("fill_cost") if "fill_cost" in header else None
    bars = []
    for row in rows[1:]:
        if not row:
            continue
        dt = datetime.strptime(row[i_dt] + " " + row[i_tm], "%m/%d/%y %I:%M %p")
        fills = []
        events = [e.strip() for e in row[i_evt].split("|") if e.strip().endswith("filled")]
        prices = row[i_px].split("|") if row[i_px] else []
        qtys = row[i_qt].split("|") if row[i_qt] else []
        costs = (
            (row[i_ct].split("|") if row[i_ct] else [])
            if i_ct is not None else ["0"] * len(events)
        )
        if not (len(events) == len(prices) == len(qtys) == len(costs)):
            sys.exit(f"event/fill columns disagree on row {row[0:2]}: {row[i_evt:]}")
        for event, price, qty, cost in zip(events, prices, qtys, costs):
            tag = event[: -len("filled")].strip()
            try:
                side = side_for(tag)
            except KeyError:
                sys.exit(
                    f"unknown order tag {tag!r} in {event!r}; "
                    f"add it to the strategy manifest or SIDE_BY_TAG"
                )
            fills.append((side, float(price), float(qty),
                          float(cost or 0)))
        bars.append((dt, float(row[i_cl]), fills))
    return bars

def ledger(bars, lot_size, initial):
    """One pass over the bars -> (equity, net_lots, realized_pnl, trades, fees).

    Equity is marked to market: initial balance + realized P/L + the P/L of
    every still-open lot at the bar's close. Trades are the FIFO round trips
    the lot ledger closes, net of entry and exit fees. `realized_pnl` is
    realized-only and net: each fill's fee is subtracted as it happens.
    Slippage needs no handling — it is already inside the fill prices.
    """
    realized, lots, fees = 0.0, [], 0.0
    equity, net, realized_pnl, trades = [], [], [], []
    for i, (dt, close, fills) in enumerate(bars):
        for side, price, qty, fee in fills:
            realized -= fee
            fees += fee
            this_fpl = fee / qty if qty else 0.0
            remaining = side * qty
            while lots and not is_zero(remaining) and sgn(lots[0][1]) == -sgn(remaining):
                entry_price, lot_qty, entry_dt, entry_i, entry_fpl = lots[0]
                rest = lots[1:]
                direction = float(sgn(lot_qty))
                closed = min(abs(lot_qty), abs(remaining))
                gross = direction * closed * lot_size * (price - entry_price)
                realized += gross
                trades.append(
                    {
                        "pnl": gross - closed * (entry_fpl + this_fpl),
                        "lots": closed,
                        "dir": "long" if direction > 0 else "short",
                        "entry": entry_dt,
                        "exit": dt,
                        "bars": i - entry_i,
                        "entry_price": entry_price,
                        "exit_price": price,
                        "points": direction * (price - entry_price),
                    }
                )
                left = lot_qty - direction * closed
                if not is_zero(left):
                    rest = [(entry_price, left, entry_dt, entry_i, entry_fpl)] + rest
                lots = rest
                remaining += direction * closed
            if not is_zero(remaining):
                lots.append((price, remaining, dt, i, this_fpl))
        unrealized = sum(
            sgn(q) * abs(q) * lot_size * (close - price) for price, q, _, _, _ in lots
        )
        equity.append(initial + realized + unrealized)
        realized_pnl.append(realized)
        net.append(sum(q for _, q, _, _, _ in lots))
    return equity, net, realized_pnl, trades, fees


from metrics import (
    NORMAL,
    moments,
    quantile,
    downside_deviation,
    cvar,
    sharpe_var,
    psr,
    min_track_record,
    expected_max_sharpe,
    ulcer_index,
    drawdowns,
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

