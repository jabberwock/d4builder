#!/usr/bin/env python3
"""
Replace fabricated efficiency_score and math_justification in build JSON files
with real data sourced from DiabloTools/d4data game file coefficients.

- efficiency_score: set to null (requires full stat calculator to compute)
- math_justification: replaced with real skill coefficients from skill_coefficients table
"""

import json
import sqlite3
import glob
from pathlib import Path

DB_PATH = Path(__file__).parent / "d4_stats.db"
BUILDS_DIR = Path(__file__).parent.parent / "webapp" / "public" / "data" / "builds"

# Skills that are damage-dealing (not buffs/passives/utility)
# Used to identify relevant coefficients for the build
BUFF_SKILLS = {
    "Rallying Cry", "War Cry", "Challenging Shout", "Iron Skin", "Wrath of the Berserker",
    "Call of the Ancients", "Blood Howl", "Cyclone Armor", "Earthen Bulwark",
    "Debilitating Roar", "Grizzly Rage", "Blood Mist", "Decrepify", "Bone Prison",
    "Dark Shroud", "Concealment", "Cold Imbuement", "Shadow Imbuement", "Poison Imbuement",
    "Smoke Grenade", "Flame Shield", "Ice Armor", "Frost Nova", "Unstable Currents",
    "Deep Freeze", "Teleport", "Purify", "Holy Shield", "Heaven's Fury",
}


def load_coefficients(db_path: Path) -> dict[str, float]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    result = {}
    for row in cur.execute("SELECT skill_name, coefficient, damage_bucket FROM skill_coefficients WHERE coefficient IS NOT NULL"):
        result[row[0]] = {"coeff": row[1], "bucket": row[2]}
    conn.close()
    return result


def get_build_skills(build: dict) -> list[dict]:
    """Extract all active skills from a build, flattened."""
    skills = []
    for tier, items in build.get("skills", {}).items():
        if isinstance(items, list):
            for s in items:
                if isinstance(s, dict) and "name" in s:
                    skills.append({"name": s["name"], "rank": s.get("rank", 1), "tier": tier})
    return skills


def build_real_math(build: dict, coeff_map: dict) -> str:
    """Generate math_justification based on real game coefficients."""
    skills = get_build_skills(build)
    cls = build.get("class", "unknown")

    # Find skills with known coefficients
    skill_coeffs = []
    for s in skills:
        name = s["name"]
        if name in coeff_map and name not in BUFF_SKILLS:
            c = coeff_map[name]
            skill_coeffs.append({
                "name": name,
                "coeff": c["coeff"],
                "bucket": c["bucket"],
                "tier": s["tier"],
                "rank": s["rank"],
            })

    if not skill_coeffs:
        return "Coefficient data pending — skill_coefficients table incomplete for this class."

    # Sort: primary/core tier first, then by coefficient
    tier_order = {"basic": 4, "core": 3, "ultimate": 2, "key_passive": 1}
    skill_coeffs.sort(key=lambda x: (-tier_order.get(x["tier"], 0), -x["coeff"]))

    lines = [
        "Skill damage coefficients sourced from DiabloTools/d4data (S12 game files):",
        "",
    ]

    for s in skill_coeffs:
        pct = s["coeff"] * 100
        lines.append(f"  {s['name']}: {pct:.0f}% weapon damage ({s['bucket']}) — rank {s['rank']}")

    # Note on what efficiency_score needs
    lines += [
        "",
        "Full efficiency score requires: attack speed, crit chance, crit damage,",
        "vulnerability uptime, multiplicative aspect bonuses, and paragon scaling.",
        "These inputs are not yet available in the stat engine.",
    ]

    return "\n".join(lines)


def main():
    coeff_map = load_coefficients(DB_PATH)
    print(f"Loaded {len(coeff_map)} coefficients from DB")

    build_files = sorted(BUILDS_DIR.glob("*.json"))
    print(f"Processing {len(build_files)} build files\n")

    for fpath in build_files:
        build = json.loads(fpath.read_text())

        old_score = build.get("efficiency_score")
        old_math = build.get("math_justification", "")[:60]

        # Replace fabricated values
        build["efficiency_score"] = None
        build["math_justification"] = build_real_math(build, coeff_map)

        fpath.write_text(json.dumps(build, indent=2, ensure_ascii=False) + "\n")
        print(f"  {fpath.name}")
        print(f"    efficiency_score: {old_score} → null")
        print(f"    math: '{old_math}...' → real coefficients")

    print(f"\nDone. {len(build_files)} builds updated.")


if __name__ == "__main__":
    main()
