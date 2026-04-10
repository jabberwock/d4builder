#!/usr/bin/env python3
"""
Parse Diablo 4 skill damage data.
Uses resolved_coefficients.json (built from .pow files cross-referenced with Maxroll)
as the single source of truth for damage coefficients.

Outputs:
  1. data/game_powers.json — full extracted data for re-ingestion
  2. Updates skill_damage table in d4_stats.db
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "d4_stats.db"
COEFFICIENTS_PATH = Path(__file__).parent / "resolved_coefficients.json"
OUTPUT_JSON = Path(__file__).parent / "game_powers.json"

SKILL_RANK_BONUS = [1.0, 1.0, 1.10, 1.20, 1.30, 1.40, 1.50, 1.60, 1.70, 1.80]


def main():
    if not COEFFICIENTS_PATH.exists():
        raise FileNotFoundError(f"Missing {COEFFICIENTS_PATH} — run coefficient resolver first")

    with open(COEFFICIENTS_PATH) as f:
        coefficients = json.load(f)
    print(f"Loaded {len(coefficients)} resolved coefficients")

    conn = sqlite3.connect(str(DB_PATH))

    # Map .pow power names to DB power_names
    rows = conn.execute(
        "SELECT power_name, display_name, class FROM skills "
        "WHERE display_name IS NOT NULL AND display_name != ''"
    ).fetchall()

    by_power_lower = {r[0].lower(): r[0] for r in rows}
    display_for = {r[0]: r[1] for r in rows}

    mapped = []
    for pow_name, coeff in coefficients.items():
        db_pname = by_power_lower.get(pow_name.lower())
        if not db_pname:
            # Try without _NEW/_OLD
            for variant in [pow_name.replace("_NEW", ""), pow_name.replace("_OLD", "")]:
                db_pname = by_power_lower.get(variant.lower())
                if db_pname:
                    break
        if db_pname:
            mapped.append((db_pname, display_for.get(db_pname, pow_name), coeff))

    print(f"Mapped to DB: {len(mapped)} skills")

    # Build damage rows for ranks 1-7
    damage_rows = []
    for db_pname, dname, coeff in mapped:
        for rank in range(1, 8):
            bonus = SKILL_RANK_BONUS[rank]
            damage_pct = round(coeff * bonus * 100, 2)
            damage_rows.append((db_pname, rank, damage_pct))

    # Write JSON backup
    output = {
        "source": "resolved_coefficients.json (Maxroll + .pow cross-reference)",
        "skill_rank_bonus": {str(i): v for i, v in enumerate(SKILL_RANK_BONUS)},
        "skills": [
            {"power_name": pn, "display_name": dn, "coefficient": c}
            for pn, dn, c in sorted(mapped, key=lambda x: x[0])
        ],
    }
    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {OUTPUT_JSON}")

    # Update DB — full wipe and replace
    cursor = conn.cursor()
    cursor.execute("DELETE FROM skill_damage")
    cursor.executemany(
        "INSERT INTO skill_damage (power_name, rank, damage_pct) VALUES (?, ?, ?)",
        damage_rows,
    )
    conn.commit()

    total = cursor.execute("SELECT COUNT(*) FROM skill_damage").fetchone()[0]
    distinct = cursor.execute("SELECT COUNT(DISTINCT power_name) FROM skill_damage").fetchone()[0]
    print(f"skill_damage: {total} rows ({distinct} skills)")

    # Per-class breakdown
    class_counts = cursor.execute("""
        SELECT substr(power_name, 1, instr(power_name, '_')-1) as cls,
               COUNT(DISTINCT power_name)
        FROM skill_damage GROUP BY cls ORDER BY cls
    """).fetchall()
    print("\nPer-class:")
    for cls, count in class_counts:
        print(f"  {cls}: {count}")

    # Sanity: top 10 by rank-7 damage
    print("\nTop 10 by rank-7 damage_pct:")
    top = cursor.execute(
        "SELECT sd.power_name, s.display_name, sd.damage_pct "
        "FROM skill_damage sd JOIN skills s ON sd.power_name = s.power_name "
        "WHERE sd.rank=7 ORDER BY sd.damage_pct DESC LIMIT 10"
    ).fetchall()
    for pname, dname, dpct in top:
        coeff = dpct / 160.0
        print(f"  {dname:30s} dpct={dpct:8.1f}  coeff={coeff:.4f}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
