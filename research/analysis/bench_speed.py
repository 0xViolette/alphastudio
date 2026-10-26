#!/usr/bin/env python3
"""Benchmark: run bin/main.ml over 5 years of synthetic 1-minute bars.

Generates a geometric-random-walk OHLC series (same family as
scripts/check_signals.py, but zero-mean so it stays finite at any horizon), writes it as 40-byte little-endian records,
streams it to the compiled binary's stdin, and reports wall time,
the binary's own reported CPU time, and throughput in bars/second.

Usage:  python3 scripts/bench_speed.py [--years 5] [--seed 42]
"""

import argparse
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import time

import costs as cost_cli

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = os.path.join(ROOT, "_build", "default", "research", "strategies", "chandelier.exe")

RECORD = struct.Struct("<qdddd")  # timestamp, open, high, low, close


def gen_bars(path, n, seed):
    """Write n synthetic 1-minute bars to path; return bytes written.

    Same family of geometric random walk as scripts/check_signals.py, but
    with *zero-mean* log returns: the +0.0003/bar drift of the short-series
    recipe compounds without bound, and at ~4.4 years of 1-minute bars the
    log-price crosses exp(709), the IEEE-754 overflow point.  Prices, equity
    and order sizes then turn inf/nan and bin/main.ml's broker spins forever
    netting nan lots against nan lots.  A plain random walk keeps the log
    price at ~0.01*sqrt(n) standard deviations -- far inside the double
    range for any realistic --years value.
    """
    rng = random.Random(seed)
    chunk = bytearray(RECORD.size * 100_000)
    c = 5000.0
    written = 0
    with open(path, "wb") as f:
        for base in range(0, n, 100_000):
            k = min(100_000, n - base)
            for j in range(k):
                i = base + j
                o = c
                c = o * math.exp(rng.gauss(0.0, 0.01))
                if not (math.isfinite(c) and c > 0.0):
                    # last-resort guard: never hand the binary a non-finite bar
                    c = o
                hi = max(o, c) * (1.0 + rng.random() * 0.004)
                lo = min(o, c) * (1.0 - rng.random() * 0.004)
                RECORD.pack_into(chunk, j * RECORD.size, i * 60_000, o, hi, lo, c)
            f.write(chunk[: k * RECORD.size])
            written += k * RECORD.size
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=42)
    cost_cli.add_arguments(ap)
    args = ap.parse_args()
    cfg = cost_cli.resolve(args, prompt=False)

    # 1-minute bars, continuous (24/7) market
    n = int(args.years * 365 * 24 * 60)

    subprocess.run(["dune", "build", "research/strategies/chandelier.exe"], cwd=ROOT, check=True)
    if not os.path.exists(EXE):
        sys.exit(f"missing {EXE} — run: dune build research/strategies/chandelier.exe")

    fd, path = tempfile.mkstemp(suffix=".bin")
    os.close(fd)
    try:
        t0 = time.perf_counter()
        nbytes = gen_bars(path, n, args.seed)
        t_gen = time.perf_counter() - t0
        print(
            f"data: {n:,} bars ({args.years:g} years of 1-minute, 24/7), "
            f"{nbytes / 1e6:.1f} MB, generated in {t_gen:.2f}s"
        )

        with open(path, "rb") as f:
            t0 = time.perf_counter()
            proc = subprocess.run(
                [EXE] + cfg.argv(), stdin=f, capture_output=True, text=True
            )
            t_wall = time.perf_counter() - t0
    finally:
        os.unlink(path)

    if proc.returncode != 0:
        sys.exit(f"OCaml exited {proc.returncode}: {proc.stderr}")

    # stdout is the bar-by-bar CSV report (huge); only its stderr carries
    # the binary's own timing
    print(proc.stderr.strip())
    print(f"csv rows:   {proc.stdout.count(chr(10)):,}")
    print(f"wall time: {t_wall:.4f}s -> {n / t_wall:,.0f} bars/s")


if __name__ == "__main__":
    main()
