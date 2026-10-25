"""Run the strategy on the data and plot the printed net PnL.

Pipeline (run from the strategy/ project root):
    python ../local_data/new_data_emitter.py | dune exec bin/main.exe

Usage: python3 scripts/plot_pnl.py [meanrev|main] [--out pnl.png]

The OCaml backtester prints one CSV row per bar on stdout:
    Date,Time,Open,High,Low,Close,<probes...>,event,fill_price,fill_qty,fill_cost

Fills appear in `event` ("<tag> filled", several pipe-separated per bar)
with their price, qty and per-fill fees in the last three columns. We
replay those fills through the same lot-offsetting ledger lib/ledger.ml
uses, subtracting each fill's fees, giving the realized net pnl after
every fill, and plot initial_balance + realized. Slippage is already
embedded in fill_price. Costs, lot size and the initial balance come from
the shared cost wizard in scripts/costs.py (or its flags); the OCaml binary
owns the defaults.
"""

import argparse
import csv
import io
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import costs as cost_cli

ROOT = Path(__file__).resolve().parent.parent.parent
EMITTER = ROOT / "data" / "new_data_emitter.py"
OUT_PNG = ROOT / "out" / "pnl.png"


def strategies() -> dict[str, str]:
    """Selectable strategies: every bin/<name>.ml maps to `dune exec
    bin/<name>.exe`. Adding a strategy file (plus a stanza in bin/dune)
    makes it available everywhere; nothing here names a strategy."""
    return {p.stem: f"strategies/{p.stem}.exe" for p in sorted((ROOT / "strategies").glob("*.ml"))}


STRATEGIES = strategies()
DEFAULT_STRATEGY = "main" if "main" in STRATEGIES else next(iter(STRATEGIES), "")

NUM = r"([-+0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?)"

# order tag -> direction of its fill (+1 buy / -1 sell), mirroring the
# enter_*/exit_* calls in bin/*.ml. A tag the script
# does not know is a hard error rather than a silently wrong plot.
TAGS = {}


def side_for(tag):
    if tag in TAGS:
        return TAGS[tag]
    if tag in SIDE_BY_TAG:
        return SIDE_BY_TAG[tag]
    raise KeyError(tag)


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


def ask_strategy(default: str = DEFAULT_STRATEGY) -> str:
    """Strategy picker, in the style of the cost wizard."""
    names = list(STRATEGIES)
    if len(names) == 1:
        return names[0]
    print("\nStrategy (bin/*.ml):")
    for i, name in enumerate(names, 1):
        mark = " (default)" if name == default else ""
        print(f"  {i}. {name}{mark}")
    default_i = names.index(default) + 1
    while True:
        try:
            raw = input(f"choose 1-{len(names)} [{default_i}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit("strategy picker cancelled")
        if not raw:
            return default
        if raw in STRATEGIES:
            return raw
        try:
            i = int(raw)
        except ValueError:
            i = 0
        if 1 <= i <= len(names):
            return names[i - 1]
        print(f"  enter 1-{len(names)} or a strategy name", file=sys.stderr)


def resolve_strategy(name: str | None = None, *, prompt: bool | None = None) -> str:
    """Which strategy to run: an explicit choice wins, then a TTY picker,
    then the default."""
    if name is not None:
        return name
    if prompt is None:
        prompt = sys.stdin.isatty()
    if prompt:
        return ask_strategy()
    return DEFAULT_STRATEGY


def run_backtest(dune_target, cost_argv=None):
    emitter = subprocess.Popen(
        [sys.executable, str(EMITTER)], stdout=subprocess.PIPE, cwd=ROOT
    )
    cmd = ["dune", "exec", dune_target, "--"] + list(cost_argv or [])
    strategy = subprocess.Popen(
        cmd,
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


def parse_tags(out):
    """Parse the `# tags: TAG=+1 ...` manifest emitted by the strategy."""
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


def parse_rows(out):
    """The backtest's fill stream as [(side, price, qty, datetime), ...]."""
    lines = [ln for ln in out.splitlines() if not ln.lstrip().startswith("#")]
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    if not rows:
        return []
    # locate the CSV header row
    header = None
    for r in rows:
        if r and "Date" in r and "event" in r:
            header = r
            # drop everything up to and including header
            rows = rows[rows.index(r):]
            break
    if header is None:
        return []
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
            try:
                side = side_for(tag)
            except KeyError:
                sys.exit(
                    f"unknown order tag {tag!r} in {event!r}; "
                    f"add it to the strategy manifest or SIDE_BY_TAG"
                )
            fills.append((side, float(price), float(qty),
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
        choices=list(STRATEGIES),
        default=None,
        help=f"which bin/ strategy to run (default: {DEFAULT_STRATEGY}; "
             "prompts on a TTY when omitted)",
    )
    ap.add_argument("--out", type=Path, default=OUT_PNG, help="output PNG path")
    ap.add_argument("--save-log", type=Path, help="also save raw strategy stdout here")
    cost_cli.add_arguments(ap)
    args = ap.parse_args()

    strategy = resolve_strategy(args.strategy)
    cfg = cost_cli.resolve(args)
    initial = cfg.initial_balance
    lot_size = cfg.lot_size
    out, err = run_backtest(STRATEGIES[strategy], cfg.argv())
    global TAGS
    TAGS = parse_tags(out)
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
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=150)

    peak_i = max(range(len(pnl)), key=lambda i: pnl[i])
    trough_i = min(range(len(pnl)), key=lambda i: pnl[i])
    print(f"initial balance: {initial:,.2f}")
    print(f"cost model:      {cfg.label()}")
    print(f"fills:           {len(pnl)}")
    print(f"total fees:      {sum(f[3] for f in fills):,.2f}   (slippage is in fill_price)")
    print(f"final net pnl:   {pnl[-1]:,.2f}   (net of slippage and fees)")
    print(f"final equity:    {equity[-1]:,.2f}   (initial + net pnl)")
    print(f"peak net pnl:    {pnl[peak_i]:,.2f}  (fill #{peak_i})")
    print(f"trough net pnl:  {pnl[trough_i]:,.2f}  (fill #{trough_i})")
    print(f"plot saved to:   {args.out}")


if __name__ == "__main__":
    main()
