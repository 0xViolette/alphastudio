#!/usr/bin/env python3
"""Benchmark a strategy on all available futures bars, without analysis.

Usage: python3 bench_speed.py [strategy]
"""

import argparse
import subprocess
import sys
import time

import costs as cost_cli
from cli import resolve_strategy
from config import ROOT
from engine import EMITTER, STRATEGIES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("strategy", nargs="?", choices=list(STRATEGIES))
    cost_cli.add_arguments(parser)
    parser.set_defaults(costs="futures")
    args = parser.parse_args()
    strategy = resolve_strategy(args.strategy)
    cfg = cost_cli.resolve(args, prompt=False)
    data_from, data_till = cost_cli.data_bounds()
    target = STRATEGIES[strategy]

    subprocess.run(["dune", "build", target], cwd=ROOT, check=True)
    executable = ROOT / "_build" / "default" / target

    emitter = subprocess.Popen(
        [sys.executable, str(EMITTER)], stdout=subprocess.PIPE, cwd=ROOT
    )
    started = time.perf_counter()
    process = subprocess.Popen(
        [str(executable)] + cfg.argv(),
        stdin=emitter.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )
    emitter.stdout.close()
    _, err = process.communicate()
    elapsed = time.perf_counter() - started
    emitter.wait()

    if process.returncode != 0:
        sys.exit(f"strategy exited {process.returncode}:\n{err}")
    if emitter.returncode != 0:
        sys.exit(f"data emitter exited {emitter.returncode}: {EMITTER}")

    print(f"strategy:  {strategy}")
    print(f"period:    {cfg.from_date or data_from} to {cfg.till_date or data_till}")
    print(f"costs:     {cfg.preset}")
    print(f"wall time: {elapsed:.3f}s")


if __name__ == "__main__":
    main()
