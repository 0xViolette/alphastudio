"""Terminal formatting helpers for tearsheets."""

import os
import sys

W = 84
_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def tone(value, fmt):
    """Format value, green if positive, red if negative."""
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
    # pad before dimming, otherwise the escape codes count toward the width
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

def polarity(value):
    """+1 / -1 for a signed metric, None when it is zero, NaN or text."""
    try:
        if value > 0:
            return 1
        if value < 0:
            return -1
    except TypeError:
        pass
    return None

def signed_text(s, sign):
    if not _COLOR or not sign:
        return s
    return f"\033[{32 if sign > 0 else 31}m{s}\033[0m"

def row(label, value, note="", sign=None, sub=False, dim_value=False):
    """One label/value/note line, shared by the terminal and HTML reports."""
    return {"label": label, "chunks": [(value, sign)], "note": note,
            "sub": sub, "dim": dim_value}

def chunks_row(label, chunks, note="", sub=False):
    return {"label": label, "chunks": chunks, "note": note,
            "sub": sub, "dim": False}

def kv_row(r):
    prefix = "    " if r["sub"] else "  "
    label = r["label"]
    plain = "".join(t for t, _ in r["chunks"])
    if r["dim"]:
        value = dim(plain)
    else:
        value = "".join(signed_text(t, s) for t, s in r["chunks"])
    padding = " " * max(0, 18 - len(plain))
    print(f"{prefix}{dim(f'{label:<32}')}{padding}{value}   {dim(r['note'])}")

