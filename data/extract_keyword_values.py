#!/usr/bin/env python3
"""
Extract numeric keyword values from skill_tags_data table.
Parses descriptions like "Vulnerable enemies take 20% increased damage"
and "Berserking grants [25|%x|] increased damage" into structured data.

Output: data/keyword_values.json
  {
    "Keyword_Vulnerable": {
      "name": "Vulnerable",
      "damage_amp_pct": 20,
      "type": "multiplicative",
      "applies_to": "enemy"
    },
    ...
  }
"""

import json
import re
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "d4_stats.db"
OUT = Path(__file__).parent / "keyword_values.json"


def clean(s: str) -> str:
    """Strip Maxroll markup."""
    if not s:
        return ""
    s = re.sub(r"\{[^}]*\}", "", s)
    return s.replace("\n", " ").strip()


def parse_value(value_str: str, format_str: str) -> tuple[float, str]:
    """Parse a [VALUE|FORMAT|] expression. Format like '%x' = multiplicative, '%+' = additive."""
    try:
        val = float(value_str)
    except (ValueError, TypeError):
        return 0.0, "unknown"
    if "%x" in format_str:
        return val, "multiplicative"
    if "%+" in format_str:
        return val, "additive"
    if "%" in format_str:
        return val, "percent"
    return val, "flat"


def extract_keyword_data(name: str, raw_desc: str) -> dict:
    """Parse a keyword description into structured data."""
    desc = clean(raw_desc)
    desc_l = desc.lower()
    info = {"name": name, "description": desc}

    # Look for [VALUE|FORMAT|] markers in raw description (preserves markup)
    markers = re.findall(r"\[(\d+(?:\.\d+)?)\|(%[xX+]?)\|\]", raw_desc)

    # Look for "X% increased damage" patterns in cleaned description
    m = re.search(r"(\d+)%\s*increased\s+damage", desc_l)
    if m:
        info["damage_amp_pct"] = int(m.group(1))
        info["damage_amp_type"] = "additive"

    m = re.search(r"take\s+(\d+)%\s*(?:increased\s+)?damage", desc_l)
    if m:
        info["enemy_damage_taken_pct"] = int(m.group(1))

    m = re.search(r"(\d+)%\s*(?:increased\s+)?attack\s*speed", desc_l)
    if m:
        info["attack_speed_pct"] = int(m.group(1))

    m = re.search(r"(\d+)%\s*(?:increased\s+)?movement\s*speed", desc_l)
    if m:
        info["movement_speed_pct"] = int(m.group(1))

    m = re.search(r"deal(?:s)?\s+(\d+)%\s*(?:less|reduced)\s*damage", desc_l)
    if m:
        info["enemy_damage_reduction_pct"] = int(m.group(1))

    m = re.search(r"(\d+)%\s*critical\s*strike\s*(?:chance|damage)", desc_l)
    if m:
        info["crit_pct"] = int(m.group(1))

    # Mark from raw markers (more reliable for [25|%x|] style values)
    for value, fmt in markers:
        val, vtype = parse_value(value, fmt)
        if vtype == "multiplicative" and "damage" in desc_l[:200]:
            info["damage_mult_pct"] = val
            info["damage_mult_type"] = "multiplicative"
        elif vtype == "additive" and "movement" in desc_l[:200]:
            info["movement_speed_pct"] = val

    # Categorize the keyword
    cats = []
    if "vulnerable" in name.lower() or "vulnerable" in desc_l[:100]:
        cats.append("debuff_damage")
    if "stun" in name.lower() or "freeze" in name.lower() or "daze" in name.lower():
        cats.append("crowd_control")
    if "barrier" in name.lower() or "fortify" in name.lower() or "immune" in name.lower():
        cats.append("defense")
    if "berserk" in name.lower() or "stealth" in name.lower():
        cats.append("buff")
    if "burn" in name.lower() or "poison" in name.lower() or "bleed" in name.lower() or "ignited" in name.lower():
        cats.append("dot")
    if cats:
        info["categories"] = cats

    return info


def main() -> None:
    if not DB.exists():
        print(f"ERROR: {DB} not found")
        return

    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT tag_name, display_name, description FROM skill_tags_data"
    ).fetchall()
    conn.close()

    keyword_data = {}
    for tag_name, display_name, raw_desc in rows:
        if not raw_desc:
            continue
        # Only keep keywords (skip Skill_Primary_*, Search_*, etc.)
        if not tag_name.startswith("Keyword_"):
            continue
        info = extract_keyword_data(display_name or tag_name, raw_desc)
        # Only keep entries with meaningful extracted data
        if len(info) > 2:  # more than just name + description
            keyword_data[tag_name] = info

    with open(OUT, "w") as f:
        json.dump(keyword_data, f, indent=2)

    print(f"Extracted {len(keyword_data)} keyword definitions to {OUT}")
    print("\nSample:")
    for name in ("Keyword_Vulnerable", "Keyword_Berserk", "Keyword_Weaken",
                 "Keyword_Overpower", "Keyword_Hunter", "Keyword_Ignited"):
        if name in keyword_data:
            d = keyword_data[name]
            print(f"\n{name}:")
            for k, v in d.items():
                if k != "description":
                    print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
