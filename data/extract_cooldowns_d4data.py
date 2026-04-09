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


def extract_cooldown(filepath: Path) -> float | None:
    """Extract base cooldown in seconds from a Power JSON."""
    try:
        with open(filepath) as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    cooldown_obj = d.get("tCooldownTime")
    if not isinstance(cooldown_obj, dict):
        return None
    cd_formula = cooldown_obj.get("value", "")
    if not cd_formula:
        return None

    # Build SF lookup
    sf_map: dict[str, str] = {}
    for i, sf in enumerate(d.get("ptScriptFormulas", [])):
        if isinstance(sf, dict):
            tf = sf.get("tFormula", {})
            if isinstance(tf, dict):
                val = tf.get("value", "")
                if val:
                    sf_map[f"SF_{i}"] = val

    # Resolve. The cooldown formula is typically "N * Table(35, sLevel)"
    # We want the N (base cooldown).
    coeff, table_id = resolve_sf_chain(cd_formula, sf_map, prefer_table=35)
    if coeff is None:
        return None

    # Sanity: cooldowns should be 1-300 seconds
    if 0.5 <= coeff <= 300:
        return coeff
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
