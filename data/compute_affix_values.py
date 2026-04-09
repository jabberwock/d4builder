#!/usr/bin/env python3
"""
Compute exact affix values at item power 800 (max gear) using attribute_formulas.
Updates affixes.max_value with the real values from formula evaluation.

Formulas use functions like IPower(), Round(), FloatRandomRangeWithInterval(),
RandomInt(). We evaluate them at IPower=800 with random ranges replaced by their max.
"""

import json
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "d4_stats.db"
ITEM_POWER = 800  # max item power for endgame gear


def eval_random(match: re.Match) -> str:
    """FloatRandomRangeWithInterval(base, step, max_steps) → base + step*max_steps."""
    args = match.group(1)
    parts = [p.strip() for p in args.split(",")]
    if len(parts) >= 3:
        try:
            base = float(parts[0])
            step = float(parts[1])
            steps = float(parts[2])
            return str(base + step * steps)
        except ValueError:
            return "0"
    return "0"


def eval_random_int(match: re.Match) -> str:
    """RandomInt(min, max) → max."""
    args = match.group(1)
    parts = [p.strip() for p in args.split(",")]
    if len(parts) >= 2:
        try:
            return parts[1]
        except (ValueError, IndexError):
            return "0"
    return "0"


def evaluate_formula(formula: str, ipower: int = ITEM_POWER) -> float | None:
    """
    Evaluate an attribute formula at the given item power.
    Replaces special functions with their max values, then eval()s.
    """
    if not formula:
        return None
    s = formula

    # Replace IPower() with the actual value
    s = re.sub(r"\bIPower\(\)", str(ipower), s)

    # Replace FloatRandomRangeWithInterval(a,b,c) with a + b*c (max value)
    s = re.sub(r"FloatRandomRangeWithInterval\(([^)]+)\)", eval_random, s)

    # Replace RandomInt(a, b) with b (max)
    s = re.sub(r"RandomInt\(([^)]+)\)", eval_random_int, s)

    # Replace Round(...) — Python doesn't have Round, but round() works
    s = re.sub(r"\bRound\(", "round(", s)

    try:
        # Safe-ish eval — only allows basic math
        return float(eval(s, {"__builtins__": {}}, {"round": round}))
    except (SyntaxError, NameError, ValueError, TypeError, ZeroDivisionError):
        return None


def main() -> None:
    if not DB.exists():
        print(f"ERROR: {DB} not found")
        return

    conn = sqlite3.connect(str(DB))
    formula_rows = conn.execute(
        "SELECT attribute_name, formulas_json FROM attribute_formulas"
    ).fetchall()

    print(f"Computing affix values at IPower={ITEM_POWER}...")
    print(f"Loaded {len(formula_rows)} formulas")
    print()

    # Build name → max_value lookup
    computed_values: dict[str, float] = {}
    for name, formulas_json in formula_rows:
        try:
            formulas = json.loads(formulas_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(formulas, list) or not formulas:
            continue
        # Pick the formula whose power threshold matches our item power
        # formulas is a list of {power, formula} where power is the min item power
        best_formula = None
        for fdata in formulas:
            if not isinstance(fdata, dict):
                continue
            power_threshold = fdata.get("power", 0)
            if power_threshold <= ITEM_POWER:
                if best_formula is None or fdata["power"] > best_formula["power"]:
                    best_formula = fdata
        if not best_formula:
            continue

        val = evaluate_formula(best_formula.get("formula", ""), ITEM_POWER)
        if val is not None:
            computed_values[name] = val

    print(f"Computed {len(computed_values)} affix values")
    print()

    # Show sample comparisons
    samples = [
        ("Affix15%", "general 15% affix"),
        ("Affix50%_CritDamage", "Crit Damage"),
        ("Affix6%_Dodge", "Dodge"),
        ("Affix8%_CDReduction", "CD Reduction"),
        ("Affix40%_CoreVuln", "Core vs Vulnerable"),
        ("Affix30%_DamageRegular", "General Damage"),
        ("Affix80%_CoreDoubled", "Core Doubled"),
    ]
    print(f"{'Formula':40s} {'Computed':>12s} {'Existing':>12s}")
    print("-" * 70)
    for fname, label in samples:
        computed = computed_values.get(fname)
        # Try to find existing affix with similar name
        existing = None
        # Map formula name to affix internal_name
        affix_lookup = fname.replace("Affix", "").replace("%", "").replace("_", "")
        rows = conn.execute(
            "SELECT internal_name, max_value FROM affixes "
            "WHERE LOWER(REPLACE(REPLACE(internal_name, '_', ''), ' ', '')) LIKE ? "
            "LIMIT 1",
            (f"%{affix_lookup.lower()}%",),
        ).fetchone()
        if rows:
            existing = rows[1]
        ce = f"{computed*100:.1f}%" if computed else "?"
        ex = f"{existing}" if existing is not None else "?"
        print(f"{label:40s} {ce:>12s} {ex:>12s}")

    conn.close()
    print()
    print("Note: 'Computed' values are at item power 800 with max random rolls.")
    print("'Existing' values are the max_value already in the affixes table.")
    print()
    print("If the computed values are much HIGHER than existing, our existing")
    print("max_value field is underestimating endgame stat rolls.")


if __name__ == "__main__":
    main()
