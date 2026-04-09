#!/usr/bin/env python3
"""
Extract passive skill effects from temp/d4data/json/base/meta/Power/*.pow.json.

The d4data files contain ptScriptFormulas as a list where the index IS the SF
number. Each entry has tFormula.value which is the authoritative formula text
(constants, ternaries, references to other SFs/affixes/skills).

This is the AUTHORITATIVE source for SF resolution — no binary parsing, no
maxroll regex. We just walk the SF chain and pull the leading numeric constant.

Output: data/passive_effects_d4data.json
"""

import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from extract_coefficients_d4data import resolve_sf_chain

POWER_DIR = Path("/Users/michael/code/d4builder/temp/d4data/json/base/meta/Power")
OUT = Path(__file__).parent / "passive_effects_d4data.json"


def build_sf_map(power_data: dict) -> dict[str, str]:
    """Build SF_N → formula text from ptScriptFormulas list (index = SF number)."""
    sf_map: dict[str, str] = {}
    for i, sf in enumerate(power_data.get("ptScriptFormulas", [])):
        if isinstance(sf, dict):
            tf = sf.get("tFormula", {})
            if isinstance(tf, dict):
                val = tf.get("value", "")
                if val:
                    sf_map[f"SF_{i}"] = val
    return sf_map


def is_passive_file(power_data: dict, filename: str) -> bool:
    """Determine if a power file represents a passive skill."""
    # 1. Filename indicates passive/talent
    if "Passive" in filename or "Talent" in filename:
        return True
    # 2. Has the bIsPassive flag set
    if power_data.get("bIsPassive"):
        return True
    return False


def extract_passive_data(filepath: Path) -> dict | None:
    """Extract authoritative passive data from one d4data Power JSON."""
    try:
        with open(filepath) as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    name = filepath.name.replace(".pow.json", "")

    if not is_passive_file(d, name):
        return None

    sf_map = build_sf_map(d)
    if not sf_map:
        return None

    # Resolve all SF entries to their numeric leading constants
    resolved: dict[str, dict] = {}
    for sf_name, formula in sf_map.items():
        coeff, table_id = resolve_sf_chain(formula, sf_map)
        if coeff is not None:
            resolved[sf_name] = {
                "value": coeff,
                "table_id": table_id,
                "formula": formula,
            }

    if not resolved:
        return None

    # Determine the "primary" effect SFs by looking for non-zero, non-trivial values
    # that appear in interesting positions (referenced by buff durations,
    # conditional damage modifiers, etc.)
    primary_effects = []
    for sf_name, info in sorted(resolved.items(), key=lambda x: int(x[0].split("_")[1])):
        v = info["value"]
        if v == 0 or v == 1:
            continue  # trivial constants
        primary_effects.append({
            "sf": sf_name,
            "value": v,
            "formula": info["formula"][:80],
        })

    # Also list any references to PowerTag, Affix, SkillRank — these tell us
    # what this passive depends on (gives us a "category" for the passive)
    references: list[str] = []
    for sf_name, formula in sf_map.items():
        for m in re.finditer(r"PowerTag\.(\w+)", formula):
            references.append(f"power:{m.group(1)}")
        for m in re.finditer(r"Affix(?:_Value_\d+)?#?(\w+)", formula):
            references.append(f"affix:{m.group(1)}")
        for m in re.finditer(r"SkillRank\(SNO\.Power\.(\w+)", formula):
            references.append(f"skill:{m.group(1)}")

    return {
        "power_name": name,
        "is_passive": True,
        "all_sfs": {k: v["value"] for k, v in resolved.items()},
        "primary_effects": primary_effects,
        "references": list(set(references))[:20],
    }


def main() -> None:
    if not POWER_DIR.exists():
        print(f"ERROR: {POWER_DIR} not found")
        return

    files = sorted(POWER_DIR.glob("*.pow.json"))
    print(f"Processing {len(files)} d4data Power files...")

    results: dict[str, dict] = {}
    for fp in files:
        data = extract_passive_data(fp)
        if data:
            results[data["power_name"]] = data

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nExtracted {len(results)} passives with resolved SF values to {OUT}")
    print()

    # Verify against known examples
    samples = [
        ("Sorcerer_Talent_Cold_T3_N1", "Shatter", 0.45),
        ("Barbarian_Talent_Warlord_T5_N1", "Walking Arsenal", 0.15),
        ("Necromancer_Talent_Caster_T5_N1", "Ossified Essence", 0.007),
        ("Paladin_Talent_KeyPassive_2", "Path of the Penitent", 0.12),
        ("Spiritborn_Talent_KeyPassive_1", "Vital Strikes", 1.0),
        ("Druid_Talent_Hybrid_T5_N2", "Nature's Fury", 0.4),
        ("Rogue_Talent_Cunning_T5_N2", "Momentum", 0.06),
    ]
    print(f"{'Passive':30s} {'Expected':>10s} {'SF Values':>30s}")
    for power_name, name, expected in samples:
        r = results.get(power_name)
        if not r:
            print(f"  {name:28s} {expected:>10.3f} NOT FOUND")
            continue
        sfs = r["all_sfs"]
        # Show first few SF values
        sf_str = ", ".join(f"SF_{k.split('_')[1]}={v:.3f}" for k, v in list(sfs.items())[:4])
        print(f"  {name:28s} {expected:>10.3f}  {sf_str}")


if __name__ == "__main__":
    main()
