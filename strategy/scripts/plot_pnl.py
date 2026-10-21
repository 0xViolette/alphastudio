"""Run the strategy on the data and plot the printed net PnL.

Pipeline (run from the strategy/ project root):
    python ../local_data/new_data_emitter.py | dune exec strategy

Usage: python3 scripts/plot_pnl.py [meanrev|main] [--out pnl.png]

The OCaml backtester prints one CSV row per bar on stdout:
    Date,Time,Open,High,Low,Close,<probes...>,event,fill_price,fill_qty,fill_cost

Fills appear in `event` ("<tag> filled", several pipe-separated per bar)
with their price, qty and per-fill fees in the last three columns. We
replay those fills through the same lot-offsetting ledger lib/ledger.ml
uses, subtracting each fill's fees, giving the realized net pnl after
every fill, and plot initial_balance + realized. Slippage is already
embedded in fill_price. The initial balance and lot size are read from
lib/ohlc.ml, so editing the OCaml values is picked up automatically on
the next run.
"""

import argparse
import csv
import io
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EMITTER = ROOT.parent / "local_data" / "new_data_emitter.py"
OHLC_SRC = ROOT / "lib" / "backtest.ml"
OUT_PNG = ROOT / "pnl.png"

# which `dune exec` target runs each selectable strategy in bin/
DUNE_TARGETS = {"meanrev": "strategy", "main": "bin/main.exe"}

NUM = r"([-+0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?)"

# order tag -> direction of its fill (+1 buy / -1 sell), mirroring the
# enter_*/exit_* calls in bin/main.ml and bin/meanrev.ml. A tag the script
# does not know is a hard error rather than a silently wrong plot.
SIDE_BY_TAG = {
    "LE": +1,  # enter_long
    "SE": -1,  # enter_short
    "2NL": -1,  # exit_long
    "2NS": +1,  # exit_short
    "TRL": -1,
    "TRS": 1,
    "LE cover": +1,  # exit_short
    "SE cover": -1,  # exit_long
    "TP_L": -1,  # exit_long
    "SL_L": -1,  # exit_long
    "TS_L": -1,  # exit_long
    "TP_S": +1,  # exit_short
    "SL_S": +1,  # exit_short
    "TS_S": +1,  # exit_short
}


def read_ocaml_literal(name, default):
    """`~name:3_000_000.` out of lib/backtest.ml."""
    match = re.search(r"~" + re.escape(name) + r":\s*" + NUM, OHLC_SRC.read_text())
    if not match:
        return default
    return float(match.group(1).replace("_", ""))


def cost_model_name():
    """Active STRATEGY_COSTS preset label: env var, else ROOT/.env, else default.

    Mirrors lib/costs.ml's lookup (real env beats .env) so reports name the
    model the OCaml side actually used."""
    if os.environ.get("STRATEGY_COSTS"):
        return os.environ["STRATEGY_COSTS"]
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                if key.strip() == "STRATEGY_COSTS":
                    return value.strip().strip('"').strip("'") + " (.env)"
    return "india (default)"


def run_backtest(dune_target):
    emitter = subprocess.Popen(
        [sys.executable, str(EMITTER)], stdout=subprocess.PIPE, cwd=ROOT
    )
    strategy = subprocess.Popen(
        ["dune", "exec", dune_target],
        stdin=emitter.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )
    emitter.stdout.close()  # let strategy own the pipe; emitter gets SIGPIPE
    out, err = strategy.communicate()
    emitter.wait()
    if strategy.returncode != 0:
        sys.exit(f"dune exec strategy failed ({strategy.returncode}):\n{err}")
    return out, err


def parse_rows(out):
    """The backtest's fill stream as [(side, price, qty, datetime), ...]."""
    rows = list(csv.reader(io.StringIO(out)))
    if not rows:
        return []
    header = rows[0]
    i_dt = header.index("Date")
    i_tm = header.index("Time")
    i_evt = header.index("event")
    i_px = header.index("fill_price")
    i_qt = header.index("fill_qty")
    i_ct = header.index("fill_cost") if "fill_cost" in header else None
    fills = []
    for row in rows[1:]:
        if not row or "filled" not in row[i_evt]:
            continue
        parts = [e.strip() for e in row[i_evt].split("|")]
        events = [e for e in parts if e.endswith("filled")]
        prices = row[i_px].split("|")
        qtys = row[i_qt].split("|")
        costs = row[i_ct].split("|") if i_ct is not None else ["0"] * len(events)
        if not (len(events) == len(prices) == len(qtys) == len(costs)):
            sys.exit(f"event/fill columns disagree on row {row[0:2]}: {row[i_evt:]}")
        dt = datetime.strptime(row[i_dt] + " " + row[i_tm], "%m/%d/%y %I:%M %p")
        for event, price, qty, cost in zip(events, prices, qtys, costs):
            tag = event[: -len("filled")].strip()
            if tag not in SIDE_BY_TAG:
                sys.exit(
                    f"unknown order tag {tag!r} in {event!r}; "
                    f"add it to SIDE_BY_TAG (direction of the fill)"
                )
            fills.append((SIDE_BY_TAG[tag], float(price), float(qty),
                          float(cost or 0), dt))
    return fills


def parse_fills(out):
    """The backtest's fill stream as [(side, price, qty, fee), ...] in order."""
    return [(side, price, qty, fee) for side, price, qty, fee, _ in parse_rows(out)]


def is_zero(x):
    return abs(x) < 1e-7


def sgn(x):
    return 0 if is_zero(x) else (1 if x > 0 else -1)


def replay(fills, lot_size):
    """Replay fills through the lot-offsetting ledger of lib/ledger.ml,
    subtracting each fill's fees; returns the realized net pnl after each
    fill plus the total fees paid."""
    realized,  lots, out = 0.0,  [], []  # lots: [(price, signed_qty), ...]

    def apply(signed_qty, price) :
        nonlocal realized,  lots

        def offset(cur, remaining):
            nonlocal realized
            if cur and sgn(cur[0][1]) == -sgn(remaining):
                entry_price, lot_qty = cur[0]
                rest = cur[1:]
                direction = float(sgn(lot_qty))
                closed = min(abs(lot_qty), abs(remaining))
                realized += direction * closed * lot_size * (price - entry_price)
                left = lot_qty - direction * closed
                rest = rest if is_zero(left) else [(entry_price, left)] + rest
                remaining = remaining + direction * closed
                return rest if is_zero(remaining) else offset(rest, remaining)
            return cur if is_zero(remaining) else cur + [(price, remaining)]

        lots = offset(lots, signed_qty)
        out.append(realized)

    for side, price, qty, fee in fills:
        realized -= fee
        apply(side * qty, price)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "strategy",
        nargs="?",
        choices=list(DUNE_TARGETS),
        default="main",
        help="which bin/ strategy to run (default: main)",
    )
    ap.add_argument("--out", type=Path, default=OUT_PNG, help="output PNG path")
    ap.add_argument("--save-log", type=Path, help="also save raw strategy stdout here")
    args = ap.parse_args()

    initial = read_ocaml_literal("initial_balance", 3_000_000.0)
    lot_size = read_ocaml_literal("lot_size", 50.0)
    out, err = run_backtest(DUNE_TARGETS[args.strategy])
    if args.save_log:
        args.save_log.write_text(out)

    fills = parse_fills(out)
    if not fills:
        sys.exit(f"no fills found in strategy output; stderr was:\n{err}")

    pnl = replay(fills, lot_size)
    # equity[0] is the account before any fill, then one point per fill
    equity = [initial] + [initial + p for p in pnl]
    xs = list(range(len(equity)))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(xs, equity, linewidth=1.2, color="tab:blue")
    ax.fill_between(xs, equity, initial, color="tab:blue", alpha=0.12)
    ax.axhline(initial, color="gray", linewidth=0.8, linestyle="--")
    ax.set_title(f"Net PnL per fill (from initial balance {initial:,.0f})")
    ax.set_xlabel("fill #")
    ax.set_ylabel("initial balance + net pnl")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(args.out, dpi=150)

    peak_i = max(range(len(pnl)), key=lambda i: pnl[i])
    trough_i = min(range(len(pnl)), key=lambda i: pnl[i])
    print(f"initial balance: {initial:,.2f}   (from {OHLC_SRC.relative_to(ROOT)})")
    print(f"cost model:      {cost_model_name()}")
    print(f"fills:           {len(pnl)}")
    print(f"total fees:      {sum(f[3] for f in fills):,.2f}   (slippage is in fill_price)")
    print(f"final net pnl:   {pnl[-1]:,.2f}   (net of slippage and fees)")
    print(f"final equity:    {equity[-1]:,.2f}   (initial + net pnl)")
    print(f"peak net pnl:    {pnl[peak_i]:,.2f}  (fill #{peak_i})")
    print(f"trough net pnl:  {pnl[trough_i]:,.2f}  (fill #{trough_i})")
    print(f"plot saved to:   {args.out}")


if __name__ == "__main__":
    main()
