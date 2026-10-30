"""Trade-log generation and CSV writers for tearsheets."""

import csv

import costs as cost_cli


def _generate_trade_log(bars, preset, values, lot_size):
    """Generate a trade log from bars by tracking position.

    Returns a list of dicts with fill metadata.
    """
    trade_log = []
    position = 0.0  # signed lots currently held

    for bar in bars:
        dt = bar.dt
        for fill in bar.fills:
            side, price, qty, fee = fill.side, fill.price, fill.qty, fill.fee
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
    call_px = price * values.get("call_premium_pct", 1.0) / 100.0
    put_px = price * values.get("put_premium_pct", 0.8) / 100.0

    def leg(leg_side, premium):
        return cost_cli.option_leg_breakup(
            leg_side, premium, qty, lot_size,
            values.get("fee_per_order", 20.0), rates,
        )

    if side > 0:
        leg1, leg2 = leg(1, call_px), leg(-1, put_px)
    else:
        leg1, leg2 = leg(-1, call_px), leg(1, put_px)
    return {key: leg1[key] + leg2[key] for key in leg1}

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


def write_synthetic_trade_log(path, trade_log):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "action", "futures_side", "option_legs",
            "qty", "price", "strike_price", "call_premium", "put_premium",
            "fee", "contract", "is_rollover",
            "brokerage", "txn_fee", "sebi_fee", "stt", "stamp", "gst",
            "slippage_call", "slippage_put",
        ])
        for entry in trade_log:
            bk = entry.get("cost_breakup", {})
            writer.writerow([
                entry["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                entry["action"],
                entry["futures_side"],
                entry["option_legs"],
                entry["qty"],
                f"{entry['price']:.2f}",
                f"{entry['strike_price']:.2f}",
                f"{bk['call_premium']:.4f}",
                f"{bk['put_premium']:.4f}",
                f"{entry['fee']:.2f}",
                entry["contract"],
                entry["is_rollover"],
                f"{bk.get('brokerage', 0):.2f}",
                f"{bk.get('txn', 0):.2f}",
                f"{bk.get('sebi', 0):.2f}",
                f"{bk.get('stt', 0):.2f}",
                f"{bk.get('stamp', 0):.2f}",
                f"{bk.get('gst', 0):.2f}",
                f"{bk.get('slippage_call', 0):.4f}",
                f"{bk.get('slippage_put', 0):.4f}",
            ])

