#!/usr/bin/env python3
"""
Compute exact affix max_values at item power 800 by following the
affix → attributes → formula chain in maxroll data.

Each affix in maxroll has an 'attributes' list. Each attribute references
a formula by name. We evaluate the formula at IPower=800 with max random
rolls to get the maximum possible value.

Updates the affixes table with the computed max_value.
"""

import json
import re
import sqlite3
from pathlib import Path

MAXROLL = (lambda: __import__("_maxroll").MAXROLL_PATH)()
DB = Path(__file__).parent / "d4_stats.db"
ITEM_POWER = 800


def eval_random(match: re.Match) -> str:
    """FloatRandomRangeWithInterval(base, step, max_steps) → max value."""
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
    # Find the second argument respecting nested parens
    depth = 0
    parts = []
    current = ""
    for c in args:
        if c == "(":
            depth += 1
            current += c
        elif c == ")":
            depth -= 1
            current += c
        elif c == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += c
    parts.append(current.strip())
    if len(parts) >= 2:
        return f"({parts[1]})"
    return "0"


def evaluate_formula(formula: str, ipower: int = ITEM_POWER) -> float | None:
    """Evaluate a formula at the given item power."""
    if not formula:
        return None
    s = formula
    s = re.sub(r"\bIPower\(\)", str(ipower), s)
    s = re.sub(r"FloatRandomRangeWithInterval\(([^)]+)\)", eval_random, s)
    s = re.sub(r"RandomInt\(([^()]*(?:\([^()]*\)[^()]*)*)\)", eval_random_int, s)
    s = re.sub(r"\bRound\(", "round(", s)
    try:
        return float(eval(s, {"__builtins__": {}}, {"round": round}))
    except (SyntaxError, NameError, ValueError, TypeError, ZeroDivisionError):
        return None


def get_max_formula(formula_list: list, ipower: int) -> str | None:
    """Pick the formula whose power threshold matches the target item power."""
    if not isinstance(formula_list, list) or not formula_list:
        return None
    best = None
    for fdata in formula_list:
        if not isinstance(fdata, dict):
            continue
        threshold = fdata.get("power", 0)
        if threshold <= ipower:
            if best is None or threshold > best.get("power", 0):
                best = fdata
    return best.get("formula") if best else None


def main() -> None:
    if not MAXROLL.exists() or not DB.exists():
        print("ERROR: Required files missing")
        return

    print(f"Loading maxroll data...")
    with open(MAXROLL) as f:
        md = json.load(f)

    formulas = md.get("attributeFormulas", {})
    affixes = md.get("affixes", {})
    print(f"  formulas: {len(formulas)}")
    print(f"  affixes:  {len(affixes)}")
    print()

    # Compute formula → max value cache
    print(f"Evaluating {len(formulas)} formulas at IPower={ITEM_POWER}...")
    formula_values: dict[str, float] = {}
    for name, formula_list in formulas.items():
        f_str = get_max_formula(formula_list, ITEM_POWER)
        if f_str:
            val = evaluate_formula(f_str, ITEM_POWER)
            if val is not None:
                formula_values[name] = val

    print(f"  resolved: {len(formula_values)} formulas")
    print()

    # Walk affixes, find their linked formula, look up the value
    affix_max_values: dict[str, float] = {}
    skipped = 0
    multi_attr = 0
    for affix_name, adata in affixes.items():
        if not isinstance(adata, dict):
            continue
        attributes = adata.get("attributes", [])
        if not isinstance(attributes, list) or not attributes:
            skipped += 1
            continue
        # Sum the values from all attributes (some affixes have multiple)
        total = 0.0
        found_any = False
        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            f_name = attr.get("formula")
            if f_name and f_name in formula_values:
                total += formula_values[f_name]
                found_any = True
        if found_any:
            affix_max_values[affix_name] = total
            if len(attributes) > 1:
                multi_attr += 1

    print(f"Computed values for {len(affix_max_values)} affixes")
    print(f"  Affixes with multiple attributes: {multi_attr}")
    print(f"  Affixes skipped (no attributes): {skipped}")
    print()

    # Update the affixes table
    conn = sqlite3.connect(str(DB))
    print("Updating affixes table with computed max_values...")
    updated = 0
    not_found_in_db = 0
    for affix_name, value in affix_max_values.items():
        # Convert decimal to percentage (formulas return 0.21 = 21%)
        max_value_pct = value * 100
        cur = conn.execute(
            "UPDATE affixes SET max_value = ? WHERE internal_name = ?",
            (max_value_pct, affix_name),
        )
        if cur.rowcount > 0:
            updated += 1
        else:
            not_found_in_db += 1
    conn.commit()

    print(f"  Updated {updated} affixes")
    print(f"  {not_found_in_db} affixes in maxroll not found in our DB")
    print()

    # Show sample affixes after update
    print("Sample affix values (after update):")
    samples = ["CritDamage", "DamageVulnerable", "OverpowerDamage", "DamageBerserking",
               "ResourceGeneration", "MaxLife", "AttackSpeed"]
    print(f"{'Affix':30s} {'New max_value':>15s}")
    for name in samples:
        row = conn.execute(
            "SELECT max_value FROM affixes WHERE internal_name = ?",
            (name,),
        ).fetchone()
        if row:
            print(f"  {name:30s} {row[0]:>14.2f}%")

    conn.close()


if __name__ == "__main__":
    main()
