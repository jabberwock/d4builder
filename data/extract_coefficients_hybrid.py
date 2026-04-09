#!/usr/bin/env python3
"""
Hybrid coefficient extractor that uses BOTH:
  - temp/powers/<name>.json (processed payloads with primary_candidate flag)
  - temp/d4data/json/base/meta/Power/<name>.pow.json (raw SF definitions)

For each skill:
  1. Load processed JSON to find which payload is the primary damage candidate
  2. Load d4data JSON to get the SF map for resolving the coefficient
  3. Match the primary payload's payload_id to a payload in d4data
  4. Resolve the SF chain to get the numeric coefficient

Output: data/coefficients_hybrid.json (replaces d4data_coefficients.json)
"""

import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from extract_coefficients_d4data import resolve_sf_chain

PROCESSED_DIR = Path("/Users/michael/code/d4builder/temp/powers")
D4DATA_DIR = Path("/Users/michael/code/d4builder/temp/d4data/json/base/meta/Power")
OUT = Path(__file__).parent / "coefficients_hybrid.json"


def get_primary_payload_index(processed_data: dict) -> int | None:
    """Find the index of the primary damage payload from processed data."""
    payloads = processed_data.get("payloads", [])
    for p in payloads:
        if p.get("primary_candidate"):
            return p.get("payload_index")
    # Fallback: highest rank_score with damage table_id == 34
    best = None
    best_score = -100
    for p in payloads:
        dmg = p.get("damage", {})
        if dmg.get("table_id") != 34:
            continue
        score = p.get("rank_score", 0)
        if score > best_score:
            best_score = score
            best = p.get("payload_index")
    return best


def build_sf_map_from_d4data(d4_data: dict) -> dict[str, str]:
    """Build SF lookup from d4data Power JSON."""
    sf_map = {}
    for i, sf in enumerate(d4_data.get("ptScriptFormulas", [])):
        if isinstance(sf, dict):
            tf = sf.get("tFormula", {})
            if isinstance(tf, dict):
                val = tf.get("value", "")
                if val:
                    sf_map[f"SF_{i}"] = val
    return sf_map


def extract_payload_scalar(d4_data: dict, payload_index: int) -> str | None:
    """Get the tHitpointScalar value for a specific payload by index."""
    payloads = d4_data.get("arPayloads", [])
    if payload_index < 0 or payload_index >= len(payloads):
        return None
    p = payloads[payload_index]
    if not isinstance(p, dict):
        return None
    tdam = p.get("tDamage", {})
    if not isinstance(tdam, dict):
        return None
    scalar = tdam.get("tHitpointScalar", {})
    if isinstance(scalar, dict):
        return scalar.get("value")
    return None


def extract_skill(power_name: str) -> dict | None:
    """Hybrid extraction for one skill."""
    processed_path = PROCESSED_DIR / f"{power_name}.json"
    d4data_path = D4DATA_DIR / f"{power_name}.pow.json"

    if not d4data_path.exists():
        return None

    # Load d4data (always available, has full SF info)
    try:
        with open(d4data_path) as f:
            d4_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    sf_map = build_sf_map_from_d4data(d4_data)

    # Determine primary payload index
    primary_idx = None
    if processed_path.exists():
        try:
            with open(processed_path) as f:
                processed = json.load(f)
            primary_idx = get_primary_payload_index(processed)
        except (json.JSONDecodeError, OSError):
            pass

    # Walk all payloads in d4data and resolve coefficients
    payloads = d4_data.get("arPayloads", [])
    coeffs_with_idx: list[tuple[int, float, int]] = []  # (idx, coeff, table_id)
    for idx, p in enumerate(payloads):
        if not isinstance(p, dict):
            continue
        tdam = p.get("tDamage", {})
        if not isinstance(tdam, dict):
            continue
        scalar = tdam.get("tHitpointScalar", {})
        if not isinstance(scalar, dict):
            continue
        scalar_val = scalar.get("value", "")
        if not scalar_val:
            continue
        coeff, tid = resolve_sf_chain(scalar_val, sf_map)
        if coeff is not None and coeff > 0 and tid == 34:
            coeffs_with_idx.append((idx, coeff, tid))

    if not coeffs_with_idx:
        return None

    # Pick primary: if processed told us which index, use it; else use first damage
    primary_coeff = None
    if primary_idx is not None:
        for idx, c, _ in coeffs_with_idx:
            if idx == primary_idx:
                primary_coeff = c
                break
    if primary_coeff is None:
        primary_coeff = coeffs_with_idx[0][1]

    all_coeffs = [c for _, c, _ in coeffs_with_idx]
    return {
        "coefficient": primary_coeff,
        "hit_count": len(all_coeffs),
        "max_coefficient": max(all_coeffs),
        "all_coefficients": all_coeffs,
    }


def main() -> None:
    if not D4DATA_DIR.exists():
        print(f"ERROR: {D4DATA_DIR} not found")
        return

    files = sorted(D4DATA_DIR.glob("*.pow.json"))
    print(f"Processing {len(files)} d4data Power JSON files...")

    results: dict[str, dict] = {}
    for fp in files:
        power_name = fp.name.replace(".pow.json", "")
        data = extract_skill(power_name)
        if data:
            results[power_name] = data

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nExtracted {len(results)} skills with coefficients to {OUT}")

    # Compare against the old extraction
    old_path = Path(__file__).parent / "d4data_coefficients.json"
    if old_path.exists():
        with open(old_path) as f:
            old = json.load(f)
        # Find differences
        diffs = []
        for k, v in results.items():
            if k in old:
                old_c = old[k].get("coefficient", 0)
                new_c = v.get("coefficient", 0)
                if abs(old_c - new_c) > 0.01:
                    diffs.append((k, old_c, new_c))
        diffs.sort(key=lambda x: -abs(x[2] - x[1]))
        print(f"\n{len(diffs)} skills changed coefficient:")
        for k, oc, nc in diffs[:20]:
            change = "↑" if nc > oc else "↓"
            print(f"  {k:45s} {oc:>6.2f} → {nc:>6.2f}  {change}")
        # New skills not in old
        new_skills = set(results.keys()) - set(old.keys())
        print(f"\n{len(new_skills)} new skills extracted (not in old):")
        for k in sorted(new_skills)[:10]:
            print(f"  {k}: coeff={results[k].get('coefficient')}")
        # Lost skills
        lost = set(old.keys()) - set(results.keys())
        print(f"\n{len(lost)} skills in old but missing in new:")

    samples = ["Necromancer_Blight", "Necromancer_Sever", "Necromancer_BoneSpear",
               "Necromancer_BloodWave", "Sorcerer_FrozenOrb", "Sorcerer_IceShards",
               "Sorcerer_IceBlades", "Sorcerer_Hydra", "Spiritborn_Eagle_Core",
               "Barbarian_Whirlwind", "Paladin_HeavensFury", "Druid_Hurricane",
               "Druid_GrizzlyRage", "Sorcerer_Inferno"]
    print(f"\n{'Skill':45s} {'Coeff':>8s} {'Hits':>6s} {'Max':>8s}")
    for s in samples:
        r = results.get(s, {})
        c = f"{r.get('coefficient', 0):.2f}" if r.get('coefficient') else "?"
        h = f"{r.get('hit_count', 0)}" if r.get('hit_count') else "?"
        mx = f"{r.get('max_coefficient', 0):.2f}" if r.get('max_coefficient') else "?"
        print(f"  {s:43s} {c:>8s} {h:>6s} {mx:>8s}")


if __name__ == "__main__":
    main()
