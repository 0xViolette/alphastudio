"""Terminal rendering for performance tearsheets."""

import os
import sys

from report import MONTHS

W = 84
_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def tone(value, fmt):
    s = fmt.format(value)
    if not _COLOR or value is None:
        return s
    if value > 0:
        return f"\033[32m{s}\033[0m"
    if value < 0:
        return f"\033[31m{s}\033[0m"
    return s


def dim(s):
    return f"\033[2m{s}\033[0m" if _COLOR else s


def bold(s):
    return f"\033[1m{s}\033[0m" if _COLOR else s


def rule(title=""):
    head = f"\u2500\u2500 {title} " if title else ""
    print(bold(head) + "\u2500" * max(0, W - len(head)))


def kv(label, value, note=""):
    print(f"  {dim(f'{label:<32}')}{value:>18}   {dim(note)}")


def banner(title, subtitle=""):
    inner = W - 2
    bar = "\u2500" * inner
    if _COLOR:
        bar = f"\033[36m{bar}\033[0m"
    print(f"\u256d{bar}\u256e")
    print(f"\u2502 {bold(title)}")
    if subtitle:
        print(f"\u2502   {dim(subtitle)}")
    print(f"\u2570{bar}\u256e")


def signed_text(s, sign):
    if not _COLOR or not sign:
        return s
    return f"\033[{32 if sign > 0 else 31}m{s}\033[0m"


def kv_row(r):
    prefix = "    " if r["sub"] else "  "
    label = r["label"]
    plain = "".join(text for text, _ in r["chunks"])
    if r["dim"]:
        value = dim(plain)
    else:
        value = "".join(signed_text(text, sign) for text, sign in r["chunks"])
    padding = " " * max(0, 18 - len(plain))
    print(f"{prefix}{dim(f'{label:<32}')}{padding}{value}   {dim(r['note'])}")


def print_report(content):
    print()
    banner(content["title"], content["subtitle"])
    print()
    for item in content["overview"]:
        kv_row(item)
    for title, rows in content["sections"]:
        rule(title)
        for item in rows:
            kv_row(item)

    print()
    rule("MONTHLY RETURNS (%)")
    grid, annual = content["monthly"], content["annual"]
    head = "  " + f"{'year':<6}" + "".join(f"{month:>7}" for month in MONTHS)
    print(dim(head + f"{'year':>9}"))
    for year in sorted(annual):
        cells = [
            f"{'':>7}" if (year, month) not in grid
            else tone(grid[(year, month)] * 100, "{:+7.2f}")
            for month in range(1, 13)
        ]
        print(f"  {dim(f'{year:<6}')}" + "".join(cells) + tone(annual[year] * 100, "{:+9.2f}"))

    grid_p = content.get("monthly_points", {})
    annual_p = content.get("annual_points", {})
    if grid_p or annual_p:
        print()
        rule("MONTHLY RETURNS (POINTS)")
        head = "  " + f"{'year':<6}" + "".join(f"{month:>11}" for month in MONTHS)
        print(dim(head + f"{'year':>12}"))
        for year in sorted(annual_p):
            cells = [
                f"{'':>11}" if (year, month) not in grid_p
                else tone(grid_p[(year, month)], "{:+11.2f}")
                for month in range(1, 13)
            ]
            print(f"  {dim(f'{year:<6}')}" + "".join(cells) + tone(annual_p[year], "{:+12.2f}"))

    if content["bench"]:
        rule("VERSUS BUY & HOLD")
        for item in content["bench"]:
            kv_row(item)

    print()
    print(dim(content["footer"]))
    print()

