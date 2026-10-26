#!/usr/bin/env python3
"""Cross-check bin/main.ml probe values against an independent Python
re-implementation of the DSL semantics (lib/graph.ml + lib/runtime.ml).

Generates deterministic synthetic OHLC bars, runs the compiled OCaml
binary on them (40-byte little-endian records on stdin), and diffs every
printed probe column of its CSV report against the Python reference.

Probes that depend on the live position/ledger (atr_at_entry, lx2n, sx2n,
and tradecon once the first fill lands) cannot be replayed without a full
broker simulation, so they are reported but only checked while flat.

Usage:  python3 scripts/check_signals.py
"""

import argparse
import math
import os
import random
import struct
import subprocess
import sys
import tempfile

import costs as cost_cli

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = os.path.join(ROOT, "_build", "default", "research", "strategies", "chandelier.exe")

N_BARS = 300
SEED = 42

# strategy parameters (keep in sync with bin/main.ml)
ECH, ATRBARS, MA = 10, 20, 65
BPV, RISK, EQUITY, LEV, ATRG = 65.0, 0.01, 3_000_000.0, 2.0, 2.0

# fill-dependent probes: they read Position.entry_price / bars_since(entered)
FILL_DEPENDENT = ("atr_at_entry", "lx2n", "sx2n")


def gen_bars(n=N_BARS, seed=SEED):
    """Geometric random walk with drift; high/low extend the body."""
    rng = random.Random(seed)
    bars = []
    c = 5000.0
    for i in range(n):
        o = c
        c = o * math.exp(rng.gauss(0.0003, 0.01))
        hi = max(o, c) * (1.0 + rng.random() * 0.004)
        lo = min(o, c) * (1.0 - rng.random() * 0.004)
        bars.append((i * 60_000, o, hi, lo, c))
    return bars


# ---- reference implementation (None == DSL's Pending) ----
#
# Semantics mirrored from lib/graph.ml + lib/strategy.ml + lib/ohlc.ml:
#  - any binary/unary op with a Pending operand is Pending
#  - window n s = [s; pre s; ...] (n elems, newest first); sma/highest/
#    lowest fold over it, so they are Pending until n present values exist
#  - sum folds left starting at 0.0, newest element first (FP order kept
#    identical to OCaml's fold_left)
#  - ema n s: recurrence; while the knot (previous loop value) is Pending,
#    take sma n s verbatim, else alpha*s + (1-alpha)*prev, alpha = 2/(n+1)
#  - tr: Pending on the first bar (pre close is Pending there)


def sma_series(n, s):
    out = []
    for i in range(len(s)):
        if i - n + 1 < 0:
            out.append(None)
            continue
        acc = 0.0
        ok = True
        for k in range(n):
            x = s[i - k]
            if x is None:
                ok = False
                break
            acc += x
        out.append(acc / n if ok else None)
    return out


def ema_series(n, s):
    alpha = 2.0 / (n + 1)
    seed = sma_series(n, s)
    out = []
    prev = None
    for i in range(len(s)):
        if prev is None:
            cur = seed[i]
        elif s[i] is None:
            cur = None
        else:
            cur = s[i] * alpha + prev * (1.0 - alpha)
        out.append(cur)
        prev = cur
    return out


def rolling_ext(n, s, fn):
    out = []
    for i in range(len(s)):
        if i - n + 1 < 0 or any(x is None for x in s[i - n + 1 : i + 1]):
            out.append(None)
        else:
            out.append(fn(s[i - n + 1 : i + 1]))
    return out


def fmap(f, s):
    return [None if x is None else f(x) for x in s]


def reference(bars, bpv=BPV, equity=EQUITY):
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    closes = [b[4] for b in bars]
    tr = [None]
    for i in range(1, len(bars)):
        pc = closes[i - 1]
        tr.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))

    le = fmap(lambda x: x + 0.05, rolling_ext(ECH, highs, max))
    se = fmap(lambda x: x - 0.05, rolling_ext(ECH, lows, min))
    atr1 = sma_series(ATRBARS, tr)
    atr = ema_series(20, atr1)
    mov = ema_series(MA, closes)

    # net_pnl stays 0 until the first fill, so every sizing column below is
    # exact while the book is flat (after that the ledger path is theirs)
    netequity = equity * LEV

    def div(a, b):
        return None if a is None or b is None else a / b

    qty1 = fmap(lambda a: (netequity * RISK) / ((ATRG * a) * bpv), atr)
    qty2 = fmap(lambda x: div(netequity, x * bpv), le)
    qty3 = fmap(lambda x: div(netequity, x * bpv), se)
    con1 = fmap(lambda q: float(math.floor(q)), qty1)
    con2 = fmap(lambda q: float(math.floor(q)), qty2)
    con3 = fmap(lambda q: float(math.floor(q)), qty3)
    tradecon = [
        None if any(x is None for x in (a, b, c)) else max(min(a, b, c), 2.0)
        for a, b, c in zip(con1, con2, con3)
    ]

    trail_mult, trail_n = 2.0, 22
    chand_l = rolling_ext(
        trail_n,
        [None if a is None else h - trail_mult * a for h, a in zip(highs, atr)],
        max,
    )
    lowest_low = rolling_ext(trail_n, lows, min)
    chand_s = [
        None if lo is None or a is None else lo + trail_mult * a
        for lo, a in zip(lowest_low, atr)
    ]

    return {
        "atr1": atr1,
        "atr": atr,
        "mov": mov,
        "tradecon": tradecon,
        "le": le,
        "se": se,
        "chand_l": chand_l,
        "chand_s": chand_s,
    }


# ---- run OCaml, parse CSV, compare ----


def run_ocaml(bars, cost_argv=None):
    """Returns (names, probes, ohlc, filled, stderr): probes is a dict of
    name -> [float] over the probe block of the CSV report (empty cell = nan),
    ohlc the parsed Open/High/Low/Close columns, filled a per-bar flag."""
    if not os.path.exists(EXE):
        sys.exit(f"missing {EXE} — run: dune build research/strategies/chandelier.exe")
    blob = b"".join(struct.pack("<qdddd", *b) for b in bars)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        with open(path, "rb") as f:
            proc = subprocess.run(
                [EXE] + list(cost_argv or []),
                stdin=f,
                capture_output=True,
                text=True,
            )
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        sys.exit(f"OCaml exited {proc.returncode}: {proc.stderr}")

    nan = float("nan")
    lines = proc.stdout.strip().splitlines()
    header = None
    for ln in lines:
        parts = ln.split(",")
        if "Date" in parts and "event" in parts:
            header = parts
            lines = lines[lines.index(ln):]
            break
    if header is None:
        sys.exit("no CSV header in strategy output")
    start = header.index("Close") + 1
    idx_event = header.index("event")
    names = header[start:idx_event]
    probes = {name: [] for name in names}
    ohlc, filled = [], []
    for line in lines[1:]:
        vals = line.split(",")
        for j, name in enumerate(names):
            probes[name].append(float(vals[start + j]) if vals[start + j] else nan)
        ohlc.append([float(vals[j]) for j in (2, 3, 4, 5)])
        filled.append("filled" in vals[idx_event])
    return names, probes, ohlc, filled, proc.stderr.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    cost_cli.add_arguments(ap)
    args = ap.parse_args()
    cfg = cost_cli.resolve(args, prompt=False)
    bars = gen_bars()
    names, probes, ohlc, filled, stderr = run_ocaml(bars, cfg.argv())
    ref = reference(bars, cfg.lot_size, cfg.initial_balance)

    print(f"data: {len(bars)} synthetic bars (seed={SEED}); OCaml: {stderr}")
    print(f"price range: close {min(b[4] for b in bars):.2f} .. {max(b[4] for b in bars):.2f}\n")

    nan = float("nan")
    mismatches = 0

    # the report must echo the bars it was fed (cells are %.10g rounded)
    for i, (bar, row) in enumerate(zip(bars, ohlc)):
        if any(
            abs(want - got) > 1e-9 * max(abs(want), 1.0)
            for want, got in zip(bar[1:5], row)
        ):
            mismatches += 1
            if mismatches < 5:
                print(f"  OHLC MISMATCH bar {i+1}: ocaml={row} input={bar[1:5]}")
    print(f"{'signal':<14} {'1st present bar':>15} {'expected':>9} {'last value':>14} {'max rel diff':>12}")
    first_fill = next((i for i, f in enumerate(filled) if f), len(bars))
    skipped = [n for n in names if n in FILL_DEPENDENT]
    worst_rel, worst = 0.0, None
    for name in names:
        if name in FILL_DEPENDENT:
            print(f"{name:<14} {'-':>15} {'-':>9} {'-':>14} {'skipped':>12}  (needs ledger)")
            continue
        oc = probes[name]
        py = ref[name]
        # sizing reads Account.net_pnl, so the reference is only valid flat
        limit = first_fill if name == "tradecon" else len(bars)
        first_oc = next((i + 1 for i, v in enumerate(oc) if not math.isnan(v)), None)
        first_py = next((i + 1 for i, v in enumerate(py) if v is not None), None)
        col_rel = 0.0
        for i, (a, b) in enumerate(zip(oc, py)):
            if i >= limit:
                break
            b = nan if b is None else b
            if math.isnan(a) and math.isnan(b):
                continue
            if math.isnan(a) != math.isnan(b):
                mismatches += 1
                if mismatches < 5:
                    print(f"  PENDING MISMATCH {name} bar {i+1}: ocaml={a} py={b}")
                continue
            rel = abs(a - b) / max(abs(b), 1e-12)
            col_rel = max(col_rel, rel)
            if rel > 1e-9:
                mismatches += 1
                if mismatches < 5:
                    print(f"  VALUE MISMATCH {name} bar {i+1}: ocaml={a} py={b}")
        if col_rel > worst_rel:
            worst_rel, worst = col_rel, (name, limit, None, None)
        flag = "" if first_oc == first_py else "  <-- WARMUP MISMATCH"
        if name == "tradecon":
            flag += f"  (checked bars 1..{limit}, flat)"
        last = oc[limit - 1] if limit else nan
        print(f"{name:<14} {str(first_oc):>15} {str(first_py):>9} {last:>14.4f} {col_rel:>12.2e}{flag}")

    print()
    if skipped:
        print(f"position-dependent probes not replayed: {', '.join(skipped)}\n")
    if mismatches:
        print(f"RESULT: {mismatches} mismatch(es); worst rel diff {worst_rel:.3e} at {worst}")
        sys.exit(1)
    print(f"RESULT: all {first_fill} flat bars x {len(names) - len(skipped)} probes match "
          f"(worst rel diff {worst_rel:.3e})")


if __name__ == "__main__":
    main()
