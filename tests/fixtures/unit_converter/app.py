# app.py
"""Command‑line unit converter.

Provides a public ``convert`` function and a ``main`` entry point for the CLI.
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict

# Conversion factors to base units (meter for length, kilogram for mass)
_LENGTH_TO_METER: Dict[str, float] = {
    "m": 1.0,
    "km": 1000.0,
    "mi": 1609.344,
    "ft": 0.3048,
    "in": 0.0254,
    "cm": 0.01,
}

_MASS_TO_KG: Dict[str, float] = {
    "kg": 1.0,
    "g": 0.001,
    "lb": 0.45359237,
    "oz": 0.028349523125,
}

# Temperature conversion functions use Celsius as the intermediate base.
def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0

def _f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0

def _c_to_k(c: float) -> float:
    return c + 273.15

def _k_to_c(k: float) -> float:
    return k - 273.15

_TEMPERATURE_FUNCS: Dict[tuple[str, str], Callable[[float], float]] = {
    ("c", "f"): _c_to_f,
    ("f", "c"): _f_to_c,
    ("c", "k"): _c_to_k,
    ("k", "c"): _k_to_c,
    ("f", "k"): lambda f: _c_to_k(_f_to_c(f)),
    ("k", "f"): lambda k: _c_to_f(_k_to_c(k)),
    ("c", "c"): lambda c: c,
    ("f", "f"): lambda f: f,
    ("k", "k"): lambda k: k,
}

# Mapping unit to its category for validation
_UNIT_CATEGORY: Dict[str, str] = {}
for u in _LENGTH_TO_METER:
    _UNIT_CATEGORY[u] = "length"
for u in _MASS_TO_KG:
    _UNIT_CATEGORY[u] = "mass"
for u in ("c", "f", "k"):
    _UNIT_CATEGORY[u] = "temp"


def _normalize(unit: str) -> str:
    """Return a lower‑cased unit string.

    Raises ValueError if the unit is not recognised.
    """
    u = unit.lower()
    if u not in _UNIT_CATEGORY:
        raise ValueError(f"Unsupported unit: {unit}")
    return u


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert *value* from *from_unit* to *to_unit*.

    Supported categories and units (case‑insensitive):
    - Length: m, km, mi, ft, in, cm
    - Mass:   kg, g, lb, oz
    - Temp:   c, f, k

    Raises:
        ValueError: if either unit is unknown or units belong to different categories.
    """
    fu = _normalize(from_unit)
    tu = _normalize(to_unit)
    if _UNIT_CATEGORY[fu] != _UNIT_CATEGORY[tu]:
        raise ValueError(
            f"Cannot convert between different categories: {from_unit} -> {to_unit}"
        )
    category = _UNIT_CATEGORY[fu]
    if category == "length":
        # Convert to meters then to target unit
        meters = value * _LENGTH_TO_METER[fu]
        return meters / _LENGTH_TO_METER[tu]
    if category == "mass":
        kg = value * _MASS_TO_KG[fu]
        return kg / _MASS_TO_KG[tu]
    # temperature
    func = _TEMPERATURE_FUNCS.get((fu, tu))
    if func is None:
        # Should not happen because categories match and we defined all combos
        raise ValueError(f"Unsupported temperature conversion: {from_unit} -> {to_unit}")
    return func(value)


def _build_parser() -> argparse.ArgumentParser:
    # Use exit_on_error=False to raise exceptions instead of exiting automatically.
    parser = argparse.ArgumentParser(
        description="Convert between length, mass, and temperature units.",
        prog="python -m app",
        exit_on_error=False,
    )
    # Parse value as string to control conversion error messages.
    parser.add_argument("value", help="numeric value to convert")
    parser.add_argument("from_unit", help="unit to convert from")
    parser.add_argument("to_unit", help="unit to convert to")
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    Prints the converted value rounded to 4 decimal places.
    Errors are printed to stderr and cause exit code 1.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        # Convert value from string to float, preserving the original error message on failure.
        try:
            value = float(args.value)
        except ValueError as ve:
            # Re‑raise to be caught by outer handler with original message.
            raise ValueError(str(ve))
        result = convert(value, args.from_unit, args.to_unit)
        # Round to 4 decimal places as required
        print(f"{result:.4f}")
    except Exception as exc:  # noqa: BLE001
        # argparse already handles help and prints to stdout; other errors go to stderr
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
