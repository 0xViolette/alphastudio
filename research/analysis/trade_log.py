"""Trade-log helpers for tearsheets."""

import argparse
import csv
import io
import sys
from datetime import date, datetime
from pathlib import Path

import costs as cost_cli
from config import ROOT, OUT_PNG


def side_for(tag):
    import tearsheet
    return tearsheet.side_for(tag)


def _date_arg(value):
    """Parse an ISO date supplied through --from / --till."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc

def _prompt_date_window(args):
    """Collect missing date bounds as part of the CLI wizard.

    Blank input means the first/last available data date. Prompts are printed
    and flushed explicitly, and are not gated on stdin.isatty(), so they remain
    visible in IDE terminals and other CLI launchers. If stdin is unavailable,
    EOF keeps the default (the full available range).
    """
    def ask_date(prompt):
        while True:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            try:
                raw = sys.stdin.readline()
            except (EOFError, OSError):
                return None
            if raw == "":  # EOF in non-interactive execution
                return None
            raw = raw.strip()
            if not raw:
                return None
            try:
                return date.fromisoformat(raw)
            except ValueError:
                print("Please enter a date in YYYY-MM-DD format, or press Enter.")

    if args.date_from is None or args.date_till is None:
        print("\nREPORT DATE WINDOW")
        print("Enter dates as YYYY-MM-DD. Press Enter to use the full available range.")
        if args.date_from is None:
            args.date_from = ask_date("From date (Enter = earliest available): ")
        if args.date_till is None:
            args.date_till = ask_date("To date   (Enter = latest available): ")

def _prompt_output_files(args):
    """Prompt for output file names when running interactively."""
    if not sys.stdin.isatty():
        return

    def ask(prompt):
        try:
            return input(prompt)
        except (EOFError, OSError):
            return ""

    if args.out == OUT_PNG and "--out" not in sys.argv:
        print("\nOUTPUT FILES")
        raw = ask(f"PNG file name (Enter = {OUT_PNG.name}): ").strip()
        if raw:
            args.out = Path(raw)

    if args.csv is None and "--csv" not in sys.argv:
        raw = ask("Export trades CSV? (y/N): ").strip().lower()
        if raw in ("y", "yes"):
            name = ask("  CSV file name (Enter = tearsheet_trades.csv): ").strip()
            args.csv = Path(name) if name else ROOT / "reports" / "trade-logs" / "tearsheet_trades.csv"

    if args.export is None and "--export" not in sys.argv:
        raw = ask("Export HTML report? (y/N): ").strip().lower()
        if raw in ("y", "yes"):
            name = ask("  HTML file name (Enter = tearsheet_report.html): ").strip()
            args.export = Path(name) if name else ROOT / "reports" / "tearsheets" / "tearsheet_report.html"

    if (args.date_from is not None and args.date_till is not None
            and args.date_from > args.date_till):
        sys.exit("--from must be on or before --till")

def _select_date_window(date_from, date_till, bars):
    """Resolve optional dates against the emitted data and return bar indices."""
    available_from = min(dt.date() for dt, _, _ in bars)
    available_till = max(dt.date() for dt, _, _ in bars)
    date_from = date_from or available_from
    date_till = date_till or available_till

    print(
        f"\nAvailable data: {available_from.isoformat()} to "
        f"{available_till.isoformat()} (inclusive)."
    )
    if date_from > date_till:
        sys.exit("--from must be on or before --till")

    indices = [
        i for i, (dt, _, _) in enumerate(bars)
        if date_from <= dt.date() <= date_till
    ]
    if not indices:
        sys.exit(
            f"no bars in requested date range {date_from} to {date_till}; "
            f"available data is {available_from} to {available_till}"
        )

    actual_from = bars[indices[0]][0].date()
    actual_till = bars[indices[-1]][0].date()
    print(f"Reporting window: {actual_from.isoformat()} to {actual_till.isoformat()}\n")
    return indices

def _generate_trade_log(bars, preset, values, lot_size):
    """Generate a trade log from bars by tracking position.

    Returns a list of dicts with fill metadata.
    """
    trade_log = []
    position = 0.0  # signed lots currently held

    for dt, close, fills in bars:
        for side, price, qty, fee in fills:
            delta = side * qty
            closing = position != 0 and (position > 0) != (delta > 0)
            reversing = closing and abs(delta) > abs(position)
            if reversing:
                close_qty = abs(position)
                close_fee = fee * close_qty / qty
                parts = [
                    ("CLOSE", close_qty, close_fee),
                    ("OPEN", qty - close_qty, fee - close_fee),
                ]
            else:
                parts = [("CLOSE" if closing else "OPEN", qty, fee)]

            for action, part_qty, part_fee in parts:
                if preset == "synthetic_futures":
                    entry = {
                        "timestamp": dt,
                        "action": action,
                        "futures_side": "BUY" if side > 0 else "SELL",
                        "option_legs": "BUY CE, SELL PE" if side > 0 else "SELL CE, BUY PE",
                        "qty": part_qty,
                        "price": price,
                        "fee": part_fee,
                        "contract": "N/A",
                        "is_rollover": False,
                    }
                else:
                    entry = {
                        "timestamp": dt,
                        "action": action,
                        "side": "BUY" if side > 0 else "SELL",
                        "qty": part_qty,
                        "price": price,
                        "fee": part_fee,
                    }
                if reversing:
                    entry["_order_qty"] = qty
                trade_log.append(entry)
            position += delta

    return trade_log

def _cost_breakup_futures(price, qty, side, lot_size, values, rates):
    """Compute cost breakup for futures cost model."""
    turnover = price * qty * lot_size
    brokerage = values.get("fee_per_order", 20.0)
    txn = rates["nse_futures_txn_bps"] * 1e-4 * turnover
    sebi = rates["sebi_bps"] * 1e-4 * turnover
    stt = rates["stt_futures_bps"] * 1e-4 * turnover if side < 0 else 0.0
    stamp = rates["stamp_futures_bps"] * 1e-4 * turnover if side > 0 else 0.0
    gst = rates["gst"] * (brokerage + txn + sebi)
    slip = values.get("slip_ticks", 1.0) * values.get("tick_size", 0.05)
    return {
        "brokerage": brokerage,
        "txn": txn,
        "sebi": sebi,
        "stt": stt,
        "stamp": stamp,
        "gst": gst,
        "slippage": slip,
    }

def _cost_breakup_simple(
    price, qty, side, lot_size, values, order_kind=None, raw_price=None,
):
    """Compute simple costs, with slippage only when order metadata supports it."""
    brokerage = values.get("fee_per_order", 20.0)
    kind = order_kind.strip().lower() if isinstance(order_kind, str) else order_kind
    slip = None
    if kind == "limit":
        slip = 0.0
    elif kind in ("market", "mkt", "stop"):
        tick_size = values.get("tick_size")
        if tick_size is None:
            tick_size = values.get("tick_size_override")
        if tick_size is None and raw_price is not None:
            if raw_price < 15_000:
                tick_size = 0.05
            elif raw_price < 30_000:
                tick_size = 0.10
            else:
                tick_size = 0.20
        if tick_size is not None:
            slip = values.get("slip_ticks", 1.0) * tick_size
    return {
        "fee": brokerage,
        "slippage": slip,
    }

def _cost_breakup_synthetic_futures(price, qty, side, lot_size, values, rates):
    """Compute cost breakup for synthetic_futures cost model (estimated premiums)."""
    call_prem_pct = values.get("call_premium_pct", 1.0)
    put_prem_pct = values.get("put_premium_pct", 0.8)
    call_px = price * call_prem_pct / 100.0
    put_px = price * put_prem_pct / 100.0
    opt_tick = values.get("opt_tick_size", 0.05)
    opt_slip_ticks = values.get("opt_slip_ticks", 1.0)
    opt_slip_pct = values.get("opt_slip_pct", 0.5)

    def leg(side, px):
        turnover = px * qty * lot_size
        brokerage = values.get("fee_per_order", 20.0)
        txn = rates["nse_options_txn_bps"] * 1e-4 * turnover
        sebi = rates["sebi_bps"] * 1e-4 * turnover
        stt = rates["stt_options_bps"] * 1e-4 * turnover if side < 0 else 0.0
        stamp = rates["stamp_options_bps"] * 1e-4 * turnover if side > 0 else 0.0
        gst = rates["gst"] * (brokerage + txn + sebi)
        return brokerage, txn, sebi, stt, stamp, gst

    if side > 0:  # long: buy CE + sell PE
        b1, t1, s1, stt1, stamp1, g1 = leg(1, call_px)
        b2, t2, s2, stt2, stamp2, g2 = leg(-1, put_px)
    else:  # short: sell CE + buy PE
        b1, t1, s1, stt1, stamp1, g1 = leg(-1, call_px)
        b2, t2, s2, stt2, stamp2, g2 = leg(1, put_px)

    return {
        "brokerage": b1 + b2,
        "txn": t1 + t2,
        "sebi": s1 + s2,
        "stt": stt1 + stt2,
        "stamp": stamp1 + stamp2,
        "gst": g1 + g2,
    }

def _write_trade_log_csv(path, trade_log, preset, values, lot_size):
    """Write trade log to CSV with format depending on cost model."""
    import csv as csv_mod
    import costs as cost_cli

    meta = cost_cli.schema()
    rates = meta["rates"]

    with open(path, "w", newline="") as f:
        writer = csv_mod.writer(f)

        if preset == "synthetic_futures":
            writer.writerow([
                "timestamp", "action", "futures_side", "option_legs",
                "qty", "price", "fee", "contract", "is_rollover",
                "brokerage", "txn_fee", "sebi_fee", "stt", "stamp", "gst",
            ])
            for entry in trade_log:
                side = 1 if entry["futures_side"] == "BUY" else -1
                order_qty = entry.get("_order_qty", entry["qty"])
                share = entry["qty"] / order_qty if order_qty else 1.0
                bk = _cost_breakup_synthetic_futures(
                    entry["price"], order_qty, side, lot_size, values, rates,
                )
                bk = {key: value * share for key, value in bk.items()}
                writer.writerow([
                    entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                    entry["action"],
                    entry["futures_side"],
                    entry["option_legs"],
                    entry["qty"],
                    f"{entry['price']:.2f}",
                    f"{entry['fee']:.2f}",
                    entry["contract"],
                    entry["is_rollover"],
                    f"{bk['brokerage']:.2f}",
                    f"{bk['txn']:.2f}",
                    f"{bk['sebi']:.2f}",
                    f"{bk['stt']:.2f}",
                    f"{bk['stamp']:.2f}",
                    f"{bk['gst']:.2f}",
                ])
        elif preset == "futures":
            writer.writerow([
                "timestamp", "action", "side", "qty", "price", "fee",
                "brokerage", "txn", "sebi", "stt", "stamp", "gst",
            ])
            for entry in trade_log:
                side = 1 if entry["side"] == "BUY" else -1
                order_qty = entry.get("_order_qty", entry["qty"])
                share = entry["qty"] / order_qty if order_qty else 1.0
                bk = _cost_breakup_futures(
                    entry["price"], order_qty, side, lot_size, values, rates,
                )
                bk = {key: value * share for key, value in bk.items()}
                writer.writerow([
                    entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                    entry["action"],
                    entry["side"],
                    entry["qty"],
                    f"{entry['price']:.2f}",
                    f"{entry['fee']:.2f}",
                    f"{bk['brokerage']:.2f}",
                    f"{bk['txn']:.2f}",
                    f"{bk['sebi']:.2f}",
                    f"{bk['stt']:.2f}",
                    f"{bk['stamp']:.2f}",
                    f"{bk['gst']:.2f}",
                ])
        elif preset == "simple":
            writer.writerow([
                "timestamp", "action", "side", "qty", "price", "fee",
                "flat_fee", "slippage",
            ])
            for entry in trade_log:
                side = 1 if entry["side"] == "BUY" else -1
                order_qty = entry.get("_order_qty", entry["qty"])
                share = entry["qty"] / order_qty if order_qty else 1.0
                bk = _cost_breakup_simple(
                    entry["price"], order_qty, side, lot_size, values,
                    entry.get("order_kind"), entry.get("raw_price"),
                )
                bk["fee"] *= share
                writer.writerow([
                    entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                    entry["action"],
                    entry["side"],
                    entry["qty"],
                    f"{entry['price']:.2f}",
                    f"{entry['fee']:.2f}",
                    f"{bk['fee']:.2f}",
                    (f"{bk['slippage']:.4f}"
                     if bk["slippage"] is not None else "n/a"),
                ])
        else:  # none
            writer.writerow([
                "timestamp", "action", "side", "qty", "price", "fee",
            ])
            for entry in trade_log:
                writer.writerow([
                    entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                    entry["action"],
                    entry["side"],
                    entry["qty"],
                    f"{entry['price']:.2f}",
                    "0.00",
                ])

