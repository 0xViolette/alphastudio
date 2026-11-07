"""Shared cost configuration backed by the OCaml cost schema."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads((ROOT / "rheo-backtest" / "costs_schema.json").read_text())


@lru_cache(maxsize=1)
def synthetic_schema() -> dict:
    base = schema()
    result = dict(base)
    presets = []
    for preset in base["presets"]:
        if preset["name"] == "none":
            presets.append(dict(preset))
        elif preset["name"] == "synthetic_futures":
            item = dict(preset)
            item["name"] = "costs"
            item["description"] = (
                "option costs (brokerage, transaction, SEBI, STT, stamp, GST)"
            )
            presets.append(item)
    result["presets"] = presets
    result["default_preset"] = "costs"
    knobs = []
    for original in base["knobs"]:
        if original["key"] not in ("fee_per_order", "opt_slip_ticks", "opt_slip_pct"):
            continue
        knob = dict(original)
        knob["applies_to"] = [
            "costs" if value == "synthetic_futures" else value
            for value in knob.get("applies_to", [])
        ]
        if any(value in knob["applies_to"] for value in ("none", "costs")):
            knobs.append(knob)
    result["knobs"] = knobs
    return result


@dataclass(frozen=True, slots=True)
class CostConfig:
    preset: str
    values: dict[str, float]
    lot_size: float
    initial_balance: float
    from_date: str | None = None
    till_date: str | None = None

    def argv(self) -> list[str]:
        args = [
            "--costs",
            self.preset,
            "--lot-size",
            _number(self.lot_size),
            "--initial-balance",
            _number(self.initial_balance),
            "--print-costs",
        ]
        for key, value in self.values.items():
            args.extend((f"--{key.replace('_', '-')}", _number(value)))
        if self.from_date:
            args.extend(("--from", self.from_date))
        if self.till_date:
            args.extend(("--till", self.till_date))
        return args

    def label(self) -> str:
        if self.preset in {"futures", "synthetic_futures"}:
            return f"{self.preset} (NSE rates as of {schema()['as_of']})"
        return self.preset


def option_leg_breakup(
    side: int,
    execution_price: float,
    lots: float,
    lot_size: float,
    fee_per_order: float,
    rates: dict,
) -> dict[str, float]:
    turnover = max(0.0, execution_price * lots * lot_size)
    brokerage = max(0.0, fee_per_order)
    transaction = max(0.0, rates["nse_options_txn_bps"] * 1e-4 * turnover)
    sebi = max(0.0, rates["sebi_bps"] * 1e-4 * turnover)
    stt = (
        max(0.0, rates["stt_options_bps"] * 1e-4 * turnover)
        if side < 0
        else 0.0
    )
    stamp = (
        max(0.0, rates["stamp_options_bps"] * 1e-4 * turnover)
        if side > 0
        else 0.0
    )
    gst = max(0.0, rates["gst"] * (brokerage + transaction + sebi))
    return {
        "brokerage": brokerage,
        "txn": transaction,
        "sebi": sebi,
        "stt": stt,
        "stamp": stamp,
        "gst": gst,
    }


def _number(value: float) -> str:
    return f"{value:.10g}"
