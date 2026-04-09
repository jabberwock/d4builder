#!/usr/bin/env python3
"""
Extract coefficients from temp/powers/*.json — these are pre-processed
.pow files with primary_candidate marking, rank scoring, and table_type tags.

This is more authoritative than parsing temp/d4data/json/base/meta/Power
because the analyzer already identified which payload is the primary damage.

Output: data/processed_coefficients.json
"""

import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from extract_coefficients_d4data import resolve_sf_chain

POWERS_DIR = Path("/Users/michael/code/d4builder/temp/powers")
OUT = Path(__file__).parent / "processed_coefficients.json"


def build_sf_map(d: dict) -> dict[str, str]:
    """Reconstruct an SF lookup from the formulas list (best effort)."""
    sf_map: dict[str, str] = {}
    # The processed JSONs don't include raw SF definitions, only the formula expressions.
    # We can extract definitions from formulas where SF_N appears as the entire formula
    for f in d.get("formulas", []):
        formula = f.get("formula", "")
        # If a formula is just a constant or simple expression and has parsed_values,
        # try to extract the value
        # Skip — without raw SF definitions, we can't fully resolve SF_N references
        pass
    return sf_map


def extract_payload_coefficient(payload: dict) -> tuple[float | None, str]:
    """
    Get the coefficient from a payload's damage block.
    Returns (coefficient, source) where source describes how it was extracted.
    """
    dmg = payload.get("damage", {})
    if not isinstance(dmg, dict):
        return None, "no_damage"

    # Check table type — only damage payloads count
    if dmg.get("table_type") and dmg.get("table_type") != "damage":
        return None, "non_damage_table"
    if dmg.get("table_id") and dmg.get("table_id") != 34:
        return None, f"table_{dmg.get('table_id')}"

    formula = dmg.get("formula", "")

    # Pattern 1: "N * Table(34, sLevel)" — direct numeric coefficient
    m = re.match(r"(-?\d+\.?\d*)\s*\*\s*Table\(34", formula)
    if m:
        return float(m.group(1)), "direct"

    # Pattern 2: "(SF_X ? SF_Y : N) * Table(34, ...)" — extract default branch
    m = re.match(r"\((?:SF_\d+\s*\?\s*\S+\s*:\s*)(\d+(?:\.\d+)?)\)\s*\*\s*Table\(34", formula)
    if m:
        return float(m.group(1)), "ternary_default"

    # Pattern 3: "SF_N * Table(34, ...)" — coefficient is an SF
    # We can't resolve without the SF map, but the coefficient_sf field may have parsed it
    coeff_sf = dmg.get("coefficient_sf", "")
    if coeff_sf:
        # Try ternary default again
        m = re.search(r":\s*(\d+(?:\.\d+)?)$", coeff_sf)
        if m:
            return float(m.group(1)), "sf_ternary_default"
        # Direct number
        try:
            return float(coeff_sf), "sf_constant"
        except ValueError:
            pass

    return None, "unresolved_sf"


def extract_skill_data(filepath: Path) -> dict | None:
    """Extract authoritative data from a processed power JSON."""
    try:
        with open(filepath) as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if d.get("power_type") and d.get("power_type") != "active_skill":
        return None

    payloads = d.get("payloads", [])
    if not payloads:
        return None

    # Find primary candidate; fall back to highest rank_score
    primary = None
    for p in payloads:
        if p.get("primary_candidate"):
            primary = p
            break
    if primary is None:
        # Pick the payload with highest rank_score that has a damage table_id of 34
        ranked = []
        for p in payloads:
            dmg = p.get("damage", {})
            if dmg.get("table_id") == 34:
                ranked.append((p.get("rank_score", 0), p))
        if ranked:
            ranked.sort(key=lambda x: -x[0])
            primary = ranked[0][1]

    if primary is None:
        return None

    coeff, source = extract_payload_coefficient(primary)
    if coeff is None:
        return None

    # Count damage payloads (number of hits)
    damage_payload_count = sum(
        1 for p in payloads
        if p.get("damage", {}).get("table_id") == 34
        and p.get("damage", {}).get("formula", "")
    )

    # Also collect all coefficients
    all_coeffs = []
    for p in payloads:
        c, _ = extract_payload_coefficient(p)
        if c is not None:
            all_coeffs.append(c)

    return {
        "coefficient": coeff,
        "primary_source": source,
        "hit_count": damage_payload_count,
        "all_coefficients": all_coeffs,
        "max_coefficient": max(all_coeffs) if all_coeffs else coeff,
    }


def main() -> None:
    if not POWERS_DIR.exists():
        print(f"ERROR: {POWERS_DIR} not found")
        return

    files = sorted(POWERS_DIR.glob("*.json"))
    print(f"Processing {len(files)} processed power JSON files...")

    results: dict[str, dict] = {}
    skipped_no_payload = 0
    skipped_no_coeff = 0
    for fp in files:
        data = extract_skill_data(fp)
        if data:
            key = fp.stem  # filename without .json
            results[key] = data
        elif fp.exists():
            skipped_no_coeff += 1

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nExtracted {len(results)} skills with coefficients")
    print(f"Skipped: {skipped_no_coeff}")
    print(f"\nSample skills (vs old extraction):")

    # Compare against d4data_coefficients.json
    old_path = Path(__file__).parent / "d4data_coefficients.json"
    old_coeffs = {}
    if old_path.exists():
        with open(old_path) as f:
            old_coeffs = json.load(f)

    samples = ["Necromancer_Blight", "Necromancer_Sever", "Necromancer_BoneSpear",
               "Necromancer_BloodWave", "Sorcerer_Hydra", "Sorcerer_IceBlades",
               "Sorcerer_FrozenOrb", "Sorcerer_Inferno", "Spiritborn_Eagle_Core",
               "Barbarian_Whirlwind", "Paladin_HeavensFury", "Druid_Hurricane"]
    print(f"{'Skill':40s} {'NEW':>10s} {'OLD':>10s} {'NEW hits':>10s}")
    for s in samples:
        new = results.get(s, {})
        old = old_coeffs.get(s, {})
        nc = f"{new.get('coefficient', '?')}"
        oc = f"{old.get('coefficient', '?')}"
        nh = f"{new.get('hit_count', '?')}"
        print(f"  {s:38s} {nc:>10s} {oc:>10s} {nh:>10s}")


if __name__ == "__main__":
    main()
