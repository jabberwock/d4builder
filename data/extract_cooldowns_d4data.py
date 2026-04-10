#!/usr/bin/env python3
"""
Extract authoritative skill cooldowns from d4data Power JSONs.

Reads tCooldownTime field from each Power JSON, resolves SF chain to find
the base cooldown in seconds. Output: data/d4data_cooldowns.json
"""

import json
import re
from pathlib import Path
import sys

# Reuse the SF chain resolver from the coefficient extractor
sys.path.insert(0, str(Path(__file__).parent))
from extract_coefficients_d4data import resolve_sf_chain

POWER_DIR = Path("/Users/michael/code/d4builder/temp/d4data/json/base/meta/Power")
OUT = Path(__file__).parent / "d4data_cooldowns.json"


_TABLE35_PATTERN = re.compile(r"(-?\d+\.?\d*)\s*\*\s*Table\(35")


def _find_table35_coefficient(formula: str, sf_map: dict[str, str],
                               visited: set[str] | None = None) -> float | None:
    """
    Walk an expression and any SF references it touches, looking for the FIRST
    `N * Table(35, sLevel)` pattern. Returns N (the base cooldown in seconds).
    Skips ternary modifier branches by exploring all branches.
    """
    if visited is None:
        visited = set()
    if not formula:
        return None

    # Direct match: "N * Table(35, ...)"
    m = _TABLE35_PATTERN.search(formula)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # Walk every SF reference in the formula
    for sf_name in re.findall(r"SF_\d+", formula):
        if sf_name in visited:
            continue
        visited.add(sf_name)
        sub = sf_map.get(sf_name, "")
        if sub:
            result = _find_table35_coefficient(sub, sf_map, visited)
            if result is not None:
                return result

    return None


def _resolve_field(d: dict, field: str, sf_map: dict[str, str]) -> float | None:
    """Resolve any tCooldownTime/tRechargeTime field to a numeric value."""
    obj = d.get(field)
    if not isinstance(obj, dict):
        return None
    formula = obj.get("value", "")
    if not formula:
        return None
    # Plain number
    try:
        return float(formula)
    except (ValueError, TypeError):
        pass
    # Walk SF chain for N * Table(35, sLevel)
    cd = _find_table35_coefficient(formula, sf_map)
    if cd is not None:
        return cd
    # Fall back to simple resolver
    coeff, _ = resolve_sf_chain(formula, sf_map, prefer_table=35)
    return coeff


def extract_cooldown(filepath: Path) -> float | None:
    """
    Extract base cooldown in seconds from a Power JSON.

    Cooldown source priority:
      1. tCooldownTime (standard cooldown skills)
      2. tRechargeTime (charge-based skills like Familiar, Eagle Ultimate)
         — use this when tCooldownTime is 0 or < 1s (recast lockout)
      3. None — skill has no time-based limit (Hydra: mana + max-active only)

    For charge-based skills, tCooldownTime can be a small recast lockout
    (e.g. Eagle Ultimate's 0.25s) while the real cooldown is in tRechargeTime.
    We treat that as a charge-based skill and prefer tRechargeTime.

    Strategy: walk SF chains for `N * Table(35, sLevel)`. Skip ternary modifier
    branches by exploring all branches with the SF walker.
    """
    try:
        with open(filepath) as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Build SF lookup once
    sf_map: dict[str, str] = {}
    for i, sf in enumerate(d.get("ptScriptFormulas", [])):
        if isinstance(sf, dict):
            tf = sf.get("tFormula", {})
            if isinstance(tf, dict):
                val = tf.get("value", "")
                if val:
                    sf_map[f"SF_{i}"] = val

    cd = _resolve_field(d, "tCooldownTime", sf_map)
    recharge = _resolve_field(d, "tRechargeTime", sf_map)

    # Standard cooldown skill: tCooldownTime resolves to a real cooldown
    if cd is not None and 1.0 <= cd <= 300:
        return cd

    # Charge-based skill: tCooldownTime is missing/trivial, use tRechargeTime
    # (e.g. Familiar 12s, Eagle Ultimate 45s)
    if recharge is not None and 1.0 <= recharge <= 300:
        return recharge

    # Edge case: tCooldownTime resolved to a sub-second recast lockout
    # but we also have a meaningful recharge. Already handled above.
    return None


def main() -> None:
    if not POWER_DIR.exists():
        print(f"ERROR: {POWER_DIR} not found")
        return

    files = sorted(POWER_DIR.glob("*.pow.json"))
    print(f"Processing {len(files)} Power JSON files...")

    results: dict[str, float] = {}
    for fp in files:
        key = fp.name.replace(".pow.json", "")
        cd = extract_cooldown(fp)
        if cd is not None:
            results[key] = cd

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nExtracted {len(results)} cooldowns to {OUT}")

    # Sample verification — known cooldowns
    samples = [
        ("Barbarian_WrathoftheBerserker", 60),
        ("Sorcerer_Inferno", 45),
        ("Sorcerer_DeepFreeze", None),
        ("Sorcerer_UnstableCurrents", 70),
        ("Necromancer_ArmyoftheDead", 90),
        ("Druid_GrizzlyRage", None),
        ("Rogue_DeathTrap", 60),
        ("Paladin_HeavensFury", None),
        ("Spiritborn_Eagle_Ultimate", None),
    ]
    print("\nSample cooldowns:")
    for pname, expected in samples:
        actual = results.get(pname)
        if actual is not None:
            check = " ✓" if expected is None or abs(actual - expected) < 1 else f" (expected ~{expected})"
            print(f"  {pname:45s} {actual:>5.0f}s{check}")
        else:
            print(f"  {pname:45s} NOT FOUND")


if __name__ == "__main__":
    main()
