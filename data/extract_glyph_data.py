#!/usr/bin/env python3
"""
Extract structured glyph metadata from maxroll's paragonGlyphs.

For each glyph, parse its 3 affix names to determine:
  - Required main stat (Intelligence/Strength/Dexterity/Willpower)
  - Damage categories it boosts (e.g., conjuration_damage, damage_to_vulnerable)
  - Special tags (legendary node bonus, multiplicative damage, etc.)

Output: data/glyph_data.json
  {
    "Rare_005_Intelligence_Main": {
      "name": "Conjurer",
      "main_stat": "Intelligence",
      "boosts": ["conjuration_damage"],
      "damage_types": ["conjuration"],
      "skill_tag_match": "Conjuration",
      "rarity": "rare"
    },
    ...
  }
"""

import json
import re
from pathlib import Path

MAXROLL = (lambda: __import__("_maxroll").MAXROLL_PATH)()
OUT = Path(__file__).parent / "glyph_data.json"


def parse_affix_name(name: str) -> dict:
    """
    Parse an affix name like 'ConjurationDamage_Intelligence_Main' or
    'MultDmgPercentBySkillTag_Conjuration_Legendary' into structured info.
    """
    parts = name.split("_")
    info: dict = {"raw": name}

    # Stat detection (Intelligence/Strength/Dexterity/Willpower)
    for stat in ("Intelligence", "Strength", "Dexterity", "Willpower"):
        if stat in parts:
            info["stat"] = stat
            break

    # Side: Main / Side / Generic / Legendary
    for side in ("Main", "Side", "Generic", "Legendary"):
        if side in parts:
            info["side"] = side
            break

    # Detect damage type / category from the leading words
    # Common patterns:
    #   ConjurationDamage_*  → boosts conjuration
    #   DamageToVulnerable_* → +damage to vulnerable
    #   FurySkillCritDamage_* → fury skill crit damage
    #   MultDmgPercentBySkillTag_<TAG>_Legendary → multiplicative damage to a tag
    leading = parts[0]
    info["category"] = leading

    # Skill tag from MultDmgPercentBySkillTag pattern
    if "MultDmgPercentBySkillTag" in leading or (leading == "MultDmgPercentBySkillTag" and len(parts) > 1):
        if len(parts) > 1:
            info["skill_tag"] = parts[1]

    # Specific keyword/element targets
    keywords = {
        "Vulnerable": "vulnerable",
        "Burning": "burning",
        "Bleeding": "bleeding",
        "Poisoned": "poisoned",
        "Stunned": "stunned",
        "Chilled": "chilled",
        "Frozen": "frozen",
        "Slowed": "slowed",
        "CCed": "cc",
        "CC": "cc",
        "Berserk": "berserking",
        "Crackling": "crackling_energy",
    }
    for kw, tag in keywords.items():
        if kw in leading:
            info["target"] = tag
            break

    # Damage type / element
    elements = {
        "Fire": "fire", "Cold": "cold", "Lightning": "lightning",
        "Shadow": "shadow", "Poison": "poison", "Physical": "physical",
        "Holy": "holy",
    }
    for elem, tag in elements.items():
        if elem in leading:
            info["element"] = tag
            break

    # Skill class
    skill_classes = ["Conjuration", "Fury", "Companion", "Hunter",
                     "Mastery", "Subterfuge", "Imbuement", "Cutthroat",
                     "Marksman", "Sword", "Mace", "Axe", "Polearm",
                     "Trap", "Werewolf", "Werebear", "Earth", "Storm"]
    for sc in skill_classes:
        if sc in leading:
            info["skill_class"] = sc.lower()
            break

    return info


def extract_glyph_metadata(glyph_id: str, glyph_data: dict) -> dict:
    """Build structured metadata for one glyph."""
    affixes = glyph_data.get("affixes", [])
    parsed_affixes = [parse_affix_name(a) if isinstance(a, str) else {} for a in affixes]

    info: dict = {
        "id": glyph_data.get("id"),
        "name": glyph_data.get("name", glyph_id),
        "rarity": "rare",
        "affixes_raw": affixes,
        "parsed_affixes": parsed_affixes,
    }

    # Roll up high-level info
    main_stats = set()
    targets = set()
    elements = set()
    skill_classes = set()
    skill_tags = set()
    has_legendary = False

    for pa in parsed_affixes:
        if pa.get("stat"):
            main_stats.add(pa["stat"])
        if pa.get("target"):
            targets.add(pa["target"])
        if pa.get("element"):
            elements.add(pa["element"])
        if pa.get("skill_class"):
            skill_classes.add(pa["skill_class"])
        if pa.get("skill_tag"):
            skill_tags.add(pa["skill_tag"])
        if pa.get("side") == "Legendary":
            has_legendary = True

    if main_stats:
        info["main_stats"] = sorted(main_stats)
    if targets:
        info["targets"] = sorted(targets)
    if elements:
        info["elements"] = sorted(elements)
    if skill_classes:
        info["skill_classes"] = sorted(skill_classes)
    if skill_tags:
        info["skill_tags"] = sorted(skill_tags)
    info["has_legendary_bonus"] = has_legendary

    return info


def main() -> None:
    if not MAXROLL.exists():
        print(f"ERROR: {MAXROLL} not found")
        return

    with open(MAXROLL) as f:
        md = json.load(f)

    glyphs = md.get("paragonGlyphs", {})
    if not glyphs:
        print("No glyphs found in maxroll data")
        return

    result = {}
    for gid, gdata in glyphs.items():
        if not isinstance(gdata, dict):
            continue
        result[gid] = extract_glyph_metadata(gid, gdata)

    with open(OUT, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Extracted {len(result)} glyph definitions to {OUT}")
    print("\nSample glyphs:")
    for sample_id in ("Rare_005_Intelligence_Main", "Rare_001_Intelligence_Main"):
        if sample_id in result:
            print(f"\n{sample_id}:")
            for k, v in result[sample_id].items():
                if k not in ("affixes_raw", "parsed_affixes"):
                    print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
