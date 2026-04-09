#!/usr/bin/env python3
"""
Extract authoritative damage coefficients from d4data Power JSONs.

The d4data extracted JSONs in temp/d4data/json/base/meta/Power/*.pow.json contain:
  - ptScriptFormulas: indexed list of SF formulas (numeric values + ternary expressions)
  - arPayloads: damage payload definitions with tHitpointScalar referencing SF_N

This extractor walks each Power JSON, resolves SF chains to find the per-payload
damage coefficient, and writes a per-skill coefficient + hit-count table.

Output: data/d4data_coefficients.json
  {
    "Necromancer_BoneSpear": {
      "coefficient": 1.3,        # primary payload coefficient
      "hit_count": 4,            # number of damage payloads
      "max_coefficient": 1.3,    # highest single payload
      "all_coefficients": [1.3, 1.3, 0.25, 0.25, 0.0],
      "all_table_ids": [34, 34, 34, 34],
    }
  }
"""

import json
import re
import os
from pathlib import Path

POWER_DIR = Path("/Users/michael/code/d4builder/temp/d4data/json/base/meta/Power")
PROCESSED_DIR = Path("/Users/michael/code/d4builder/temp/powers")
OUT = Path(__file__).parent / "d4data_coefficients.json"


def get_primary_payload_index(power_name: str) -> int | None:
    """Look up the primary_candidate payload index from temp/powers/<name>.json."""
    processed_path = PROCESSED_DIR / f"{power_name}.json"
    if not processed_path.exists():
        return None
    try:
        with open(processed_path) as f:
            d = json.load(f)
        for p in d.get("payloads", []):
            if p.get("primary_candidate"):
                return p.get("payload_index")
        # Fall back to highest rank score with damage table
        best = None
        best_score = -100
        for p in d.get("payloads", []):
            dmg = p.get("damage", {})
            if dmg.get("table_id") != 34:
                continue
            score = p.get("rank_score", 0)
            if score > best_score:
                best_score = score
                best = p.get("payload_index")
        return best
    except (json.JSONDecodeError, OSError):
        return None


def _balanced_split_ternary(s: str) -> tuple[str, str, str] | None:
    """
    Split a ternary expression respecting parentheses.
    Returns (condition, true_branch, false_branch) or None if not a ternary.
    """
    # Find the top-level '?' (not inside parens)
    depth = 0
    qpos = -1
    for i, c in enumerate(s):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "?" and depth == 0:
            qpos = i
            break
    if qpos == -1:
        return None

    # Find matching ':' at same depth
    depth = 0
    cpos = -1
    for i in range(qpos + 1, len(s)):
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == ":" and depth == 0:
            cpos = i
            break
    if cpos == -1:
        return None

    cond = s[:qpos].strip()
    true_b = s[qpos + 1 : cpos].strip()
    false_b = s[cpos + 1 :].strip()
    # Strip outer parens
    for branch in (cond, true_b, false_b):
        pass
    return cond, true_b, false_b


def _strip_outer_parens(s: str) -> str:
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        # Make sure these are matching outer parens
        depth = 0
        matched = True
        for i, c in enumerate(s):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0 and i < len(s) - 1:
                    matched = False
                    break
        if matched:
            s = s[1:-1].strip()
        else:
            break
    return s


def resolve_sf_chain(scalar: str, sf_map: dict[str, str], depth: int = 0,
                     prefer_table: int = 34) -> tuple[float | None, int | None]:
    """
    Resolve an SF reference chain to a numeric coefficient and table id.
    Returns (coefficient, table_id) or (None, None) if unresolvable.

    prefer_table: Which Table(N, ...) reference to prefer in ternary branches.
                  34 for damage, 35 for cooldowns.
    """
    if depth > 12:
        return None, None
    if not scalar:
        return None, None
    s = _strip_outer_parens(scalar.strip())

    # Plain number
    try:
        return float(s), None
    except ValueError:
        pass

    # SF reference (just "SF_N")
    if re.match(r"^SF_\d+$", s):
        resolved = sf_map.get(s, "")
        if not resolved or resolved == s:
            return None, None
        return resolve_sf_chain(resolved, sf_map, depth + 1, prefer_table)

    # Ternary at top level — prefer the branch that produces a meaningful value.
    parts = _balanced_split_ternary(s)
    if parts:
        _cond, true_b, false_b = parts
        prefer_str = f"Table({prefer_table}"
        # First try the branch containing the preferred Table reference
        for branch in (true_b, false_b):
            if prefer_str in branch:
                result = resolve_sf_chain(branch, sf_map, depth + 1, prefer_table)
                if result[0] is not None and result[0] != 0:
                    return result
        # Then try any branch that contains a Table(...) reference
        for branch in (true_b, false_b):
            if "Table(" in branch:
                result = resolve_sf_chain(branch, sf_map, depth + 1, prefer_table)
                if result[0] is not None and result[0] != 0:
                    return result
        # Fallback: try both branches, prefer non-zero values
        results = []
        for branch in (true_b, false_b):
            r = resolve_sf_chain(branch, sf_map, depth + 1, prefer_table)
            if r[0] is not None:
                results.append(r)
        # Prefer non-zero
        nonzero = [r for r in results if r[0] != 0]
        if nonzero:
            return nonzero[0]
        if results:
            return results[0]
        return None, None

    # "N * Table(M, ...)" — direct numeric coefficient
    m = re.match(r"(-?\d+\.?\d*)\s*\*\s*Table\((\d+)", s)
    if m:
        return float(m.group(1)), int(m.group(2))

    # "SF_N * Table(M, ...)"
    m = re.match(r"(SF_\d+)\s*\*\s*Table\((\d+)", s)
    if m:
        sub_coeff, _ = resolve_sf_chain(m.group(1), sf_map, depth + 1)
        return sub_coeff, int(m.group(2))

    # "(SF_N) * Table(M, ...)" — wrapped SF
    m = re.match(r"\((SF_\d+)\)\s*\*\s*Table\((\d+)", s)
    if m:
        sub_coeff, _ = resolve_sf_chain(m.group(1), sf_map, depth + 1)
        return sub_coeff, int(m.group(2))

    # Pure division: "SF_A / SF_B" or "SF_A / N"
    m = re.match(r"^(SF_\d+|\d+\.?\d*)\s*/\s*(SF_\d+|\d+\.?\d*)$", s)
    if m:
        a, _ = resolve_sf_chain(m.group(1), sf_map, depth + 1)
        b, _ = resolve_sf_chain(m.group(2), sf_map, depth + 1)
        if a is not None and b is not None and b != 0:
            return a / b, None

    # Pure multiplication: "SF_A * SF_B" or "SF_A * N"
    m = re.match(r"^(SF_\d+|\d+\.?\d*)\s*\*\s*(SF_\d+|\d+\.?\d*)$", s)
    if m:
        a, _ = resolve_sf_chain(m.group(1), sf_map, depth + 1)
        b, _ = resolve_sf_chain(m.group(2), sf_map, depth + 1)
        if a is not None and b is not None:
            return a * b, None

    # SF * (1 + something) — keep the SF coefficient and ignore the multiplier
    m = re.match(r"^(SF_\d+)\s*\*\s*\(", s)
    if m:
        return resolve_sf_chain(m.group(1), sf_map, depth + 1)

    # Compound expression — try to extract any leading number
    m = re.match(r"(-?\d+\.?\d*)", s)
    if m:
        try:
            return float(m.group(1)), None
        except ValueError:
            pass

    return None, None


def extract_power_coefficients(filepath: Path) -> dict | None:
    """Extract coefficient data from a single Power JSON file."""
    try:
        with open(filepath) as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Build SF lookup
    sfs = d.get("ptScriptFormulas", [])
    sf_map: dict[str, str] = {}
    for i, sf in enumerate(sfs):
        if isinstance(sf, dict):
            formula = sf.get("tFormula", {})
            if isinstance(formula, dict):
                val = formula.get("value", "")
                if val:
                    sf_map[f"SF_{i}"] = val

    # Look up primary payload index from temp/powers/ processed analysis
    power_name = filepath.name.replace(".pow.json", "")
    primary_idx = get_primary_payload_index(power_name)

    # Walk payloads, tracking index to match against primary
    payloads = d.get("arPayloads", [])
    indexed_coeffs: list[tuple[int, float, int]] = []  # (idx, coeff, table_id)
    for idx, p in enumerate(payloads):
        if not isinstance(p, dict):
            continue
        tdam = p.get("tDamage", {})
        if not isinstance(tdam, dict):
            continue
        scalar_obj = tdam.get("tHitpointScalar", {})
        if not isinstance(scalar_obj, dict):
            continue
        scalar = scalar_obj.get("value", "")
        if not scalar:
            continue
        coeff, tid = resolve_sf_chain(scalar, sf_map)
        if coeff is not None and coeff > 0:
            indexed_coeffs.append((idx, coeff, tid))

    if not indexed_coeffs:
        return None

    # Filter to only damage table coefficients (Table 34)
    damage_indexed = [(i, c) for i, c, t in indexed_coeffs if t == 34 or t is None]

    if not damage_indexed:
        return None

    damage_coeffs = [c for _, c in damage_indexed]

    # Pick primary: if we have a primary index, use that; else use first
    primary_coeff = damage_coeffs[0]
    if primary_idx is not None:
        for idx, c in damage_indexed:
            if idx == primary_idx:
                primary_coeff = c
                break

    return {
        "coefficient": primary_coeff,
        "hit_count": len(damage_coeffs),
        "max_coefficient": max(damage_coeffs),
        "all_coefficients": damage_coeffs,
        "total_coefficient": sum(damage_coeffs),  # sum of all damage payloads
    }


def main():
    if not POWER_DIR.exists():
        print(f"ERROR: {POWER_DIR} not found")
        return

    results: dict[str, dict] = {}
    files = sorted(POWER_DIR.glob("*.pow.json"))
    print(f"Processing {len(files)} Power JSON files...")

    for fp in files:
        # Use the base name (without .pow.json) as the key
        key = fp.name.replace(".pow.json", "")
        data = extract_power_coefficients(fp)
        if data:
            results[key] = data

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nWrote {len(results)} skills with coefficients to {OUT}")

    # Show some samples for verification
    samples = ["Necromancer_BoneSpear", "Necromancer_Blight", "Necromancer_Sever",
               "Necromancer_BloodWave", "Sorcerer_FrozenOrb", "Sorcerer_IceShards",
               "Barbarian_Whirlwind", "Paladin_Condemn", "Paladin_HeavensFury",
               "Spiritborn_Plains_Mobility2"]
    print("\nSample coefficients:")
    for s in samples:
        if s in results:
            r = results[s]
            print(f"  {s:40s} coeff={r['coefficient']}  hits={r['hit_count']}  max={r['max_coefficient']}")
        else:
            print(f"  {s:40s} NOT FOUND")


if __name__ == "__main__":
    main()
