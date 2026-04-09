#!/usr/bin/env python3
"""
Extract passive skill effects from maxroll data.

Maxroll's skills entries for passive skills have a `buffs` array where each
buff contains the actual mechanical effect description. This is the missing
link for properly scoring passives in the optimizer.

Output: data/passive_effects.json
  {
    "Barbarian_Bash_Passive": {
      "name": "Bash",
      "buffs": [
        {
          "name": "Bash Two-Hand Criticals",
          "desc": "At 4, your next Core or Weapon Mastery Skill will Overpower."
        }
      ],
      "extracted_effects": [
        {"tag": "grants_overpower_proc", "value": 1.5}
      ]
    }
  }
"""

import json
import re
import sqlite3
from pathlib import Path

MAXROLL = (lambda: __import__("_maxroll").MAXROLL_PATH)()
DB = Path(__file__).parent / "d4_stats.db"
OUT = Path(__file__).parent / "passive_effects.json"


def clean(s: str) -> str:
    """
    Strip Maxroll markup from descriptions while preserving evaluatable
    leading constants from formula expressions.

    Maxroll descriptions use [<expr>|<format>|] for parameterized values.
    For passive scoring, we want the leading numeric constant from <expr>.
    Example: '[(0.45+Sorc_Shatter_Damage)*100|%|]' → '45%' (0.45 * 100)
    Example: '[12*X|%[x]|]' → '12%' (the first numeric)
    """
    if not s:
        return ""
    s = re.sub(r"\{[^}]*\}", "", s)

    # Replace [<expr>|<format>|] with the resolved leading constant + format hint
    def resolve_token(m: re.Match) -> str:
        expr = m.group(1) or ""
        fmt = m.group(2) or ""
        # Try to find a leading number in the expression
        num_match = re.search(r"(-?\d+(?:\.\d+)?)", expr)
        if not num_match:
            return "X" + fmt
        val = float(num_match.group(1))
        # If expression contains *100, the number is a fraction (0.45 * 100 = 45)
        if "*100" in expr or "* 100" in expr:
            val *= 100
        # Format as integer if whole
        val_str = str(int(val)) if val == int(val) else f"{val:.1f}"
        return val_str + fmt

    s = re.sub(r"\[([^|\]]*)\|([^|\]]*)\|\]", resolve_token, s)
    # Decode \[ and \] back to brackets for [x] and [+] markers
    s = s.replace("\\[", "[").replace("\\]", "]")
    return s.replace("\r", "").replace("\n", " ").strip()


def extract_effect_tags(desc: str) -> list[dict]:
    """
    Heuristically extract mechanical effects from a passive description.
    Returns a list of {tag, value, description} entries.

    Recognizes Maxroll markers:
      [x] = multiplicative damage bonus (e.g. 12%[x] = 1.12 multiplier)
      [+] = additive bonus (e.g. 6%[+] = +6%)
    """
    if not desc:
        return []

    effects = []
    desc_l = desc.lower()

    # Multiplicative damage bonuses [x] — broader pattern matches "more X damage"
    for m in re.finditer(r"(\d+(?:\.\d+)?)%\s*\[x\]\s*(?:more\s+|increased\s+)?(?:\w+\s+)?damage(?:\s+to\s+(\w+))?", desc_l):
        try:
            value = float(m.group(1)) / 100.0
            target = m.group(2) if m.lastindex >= 2 else None
            effects.append({
                "tag": "damage_mult" + (f"_vs_{target}" if target else ""),
                "value": value,
                "type": "multiplicative",
            })
        except (ValueError, IndexError):
            pass

    # Damage explosion / proc effects: "explode for X% of damage"
    for m in re.finditer(r"explode(?:s)?\s+for\s+(\d+(?:\.\d+)?)%", desc_l):
        try:
            effects.append({
                "tag": "damage_proc_explode",
                "value": float(m.group(1)) / 100.0,
                "type": "proc",
            })
        except ValueError:
            pass

    # Damage proc on conditions: "deal X% of damage"
    for m in re.finditer(r"deals?\s+(\d+(?:\.\d+)?)%\s+(?:of\s+)?(?:the\s+)?damage", desc_l):
        try:
            effects.append({
                "tag": "damage_proc",
                "value": float(m.group(1)) / 100.0,
                "type": "proc",
            })
        except ValueError:
            pass

    # Free skill triggers: "X% chance to trigger a free <skill>"
    for m in re.finditer(r"(\d+(?:\.\d+)?)%\s+chance\s+to\s+(?:trigger|cast|unleash)", desc_l):
        try:
            effects.append({
                "tag": "free_proc_chance",
                "value": float(m.group(1)) / 100.0,
                "type": "proc",
            })
        except ValueError:
            pass

    # Multiplicative attack speed [x]
    for m in re.finditer(r"(\d+(?:\.\d+)?)%\s*\[x\]\s*(?:increased\s+)?attack speed", desc_l):
        try:
            effects.append({"tag": "attack_speed_mult", "value": float(m.group(1)) / 100.0, "type": "multiplicative"})
        except ValueError:
            pass

    # Additive bonuses [+]
    for m in re.finditer(r"(\d+(?:\.\d+)?)%\s*\[\+\]\s*(?:increased\s+)?attack speed", desc_l):
        try:
            effects.append({"tag": "attack_speed", "value": float(m.group(1)) / 100.0, "type": "additive"})
        except ValueError:
            pass

    for m in re.finditer(r"(\d+(?:\.\d+)?)%\s*\[\+\]\s*resource cost reduction", desc_l):
        try:
            effects.append({"tag": "resource_cost_reduction", "value": float(m.group(1)) / 100.0})
        except ValueError:
            pass

    for m in re.finditer(r"(\d+(?:\.\d+)?)%\s*\[\+\]\s*movement speed", desc_l):
        try:
            effects.append({"tag": "movement_speed", "value": float(m.group(1)) / 100.0})
        except ValueError:
            pass

    # Generic %damage without [x] marker (additive default)
    for m in re.finditer(r"(\d+(?:\.\d+)?)%\s*(?:more|increased)\s*damage(?:\s*to\s*(\w+))?", desc_l):
        try:
            value = float(m.group(1)) / 100.0
            target = m.group(2) if m.lastindex >= 2 else None
            tag = "damage" + (f"_vs_{target}" if target else "")
            if not any(e["tag"].startswith(tag.replace("damage", "damage_mult")) for e in effects):
                effects.append({"tag": tag, "value": value, "type": "additive"})
        except (ValueError, IndexError):
            pass

    # Crit chance/damage
    m = re.search(r"(\d+)%\s*critical strike chance", desc_l)
    if m:
        effects.append({"tag": "crit_chance", "value": int(m.group(1)) / 100.0})
    m = re.search(r"(\d+)%\s*critical strike damage", desc_l)
    if m:
        effects.append({"tag": "crit_damage", "value": int(m.group(1)) / 100.0})

    # Attack speed
    m = re.search(r"(\d+)%\s*attack speed", desc_l)
    if m:
        effects.append({"tag": "attack_speed", "value": int(m.group(1)) / 100.0})

    # Cooldown reduction
    m = re.search(r"(\d+)%\s*cooldown reduction", desc_l)
    if m:
        effects.append({"tag": "cooldown_reduction", "value": int(m.group(1)) / 100.0})

    # Resource generation
    m = re.search(r"generate(?:s)?\s+(\d+)\s+(\w+)", desc_l)
    if m:
        effects.append({
            "tag": "resource_gen",
            "value": int(m.group(1)),
            "resource": m.group(2),
        })

    # Lucky hit chance
    m = re.search(r"(\d+)%\s*lucky hit", desc_l)
    if m:
        effects.append({"tag": "lucky_hit", "value": int(m.group(1)) / 100.0})

    # State applications via passive
    if "vulnerable" in desc_l:
        effects.append({"tag": "applies_vulnerable", "value": 1.0})
    if "overpower" in desc_l:
        effects.append({"tag": "grants_overpower_proc", "value": 1.0})
    if "berserking" in desc_l:
        effects.append({"tag": "grants_berserking", "value": 1.0})
    if "fortify" in desc_l and "fortified" not in desc_l:
        effects.append({"tag": "grants_fortify", "value": 0.5})
    if "barrier" in desc_l:
        effects.append({"tag": "grants_barrier", "value": 0.5})

    # Damage reduction (defensive)
    m = re.search(r"(\d+)%\s*damage reduction", desc_l)
    if m:
        effects.append({"tag": "damage_reduction", "value": int(m.group(1)) / 100.0})

    return effects


def main() -> None:
    if not MAXROLL.exists():
        print(f"ERROR: {MAXROLL} not found")
        return

    with open(MAXROLL) as f:
        md = json.load(f)

    skills_mx = md.get("skills", {})
    print(f"Scanning {len(skills_mx)} skills for passives...")

    results: dict[str, dict] = {}
    for sid, s in skills_mx.items():
        if not isinstance(s, dict):
            continue

        # Identify passives by either:
        # 1. Name contains _Passive (skill-attached passive proc)
        # 2. Name contains _Talent_ (class tree passive)
        # 3. category == 12 (Maxroll's class passive category)
        is_passive_by_name = "Passive" in sid or "_Talent_" in sid
        is_passive_by_category = s.get("category") == 12

        if not (is_passive_by_name or is_passive_by_category):
            continue

        # Top-level description (richest source for class tree passives)
        top_desc = clean(s.get("desc", ""))

        # Extract buff descriptions (for _Passive proc effects)
        buffs = s.get("buffs", [])
        useful_buffs = []
        if isinstance(buffs, list):
            for buff in buffs:
                if not isinstance(buff, dict):
                    continue
                buff_desc = clean(buff.get("desc", ""))
                buff_name = clean(buff.get("name", ""))
                if buff_desc or buff_name:
                    useful_buffs.append({
                        "id": buff.get("id"),
                        "name": buff_name,
                        "desc": buff_desc,
                    })

        # Skip if we have no useful text at all
        if not top_desc and not any(b.get("desc") for b in useful_buffs):
            continue

        # Extract effects from top-level desc + all buff descriptions
        all_effects = []
        if top_desc:
            all_effects.extend(extract_effect_tags(top_desc))
        for buff in useful_buffs:
            if buff.get("desc"):
                all_effects.extend(extract_effect_tags(buff["desc"]))

        # Deduplicate effects by tag (keep highest value)
        effect_map: dict[str, dict] = {}
        for eff in all_effects:
            tag = eff["tag"]
            if tag not in effect_map or eff.get("value", 0) > effect_map[tag].get("value", 0):
                effect_map[tag] = eff

        results[sid] = {
            "name": s.get("name") or sid,
            "primary_tag": s.get("primaryTag"),
            "category": s.get("category"),
            "desc": top_desc,
            "buffs": useful_buffs,
            "extracted_effects": list(effect_map.values()),
        }

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nExtracted {len(results)} passive skill definitions to {OUT}")
    print()

    # Show samples
    samples = [
        "Barbarian_Bash_Passive",
        "Necromancer_BoneSpear_Passive",
        "Sorcerer_FrozenOrb_Passive",
        "Druid_Pulverize_Passive",
        "Rogue_Flurry_Passive",
        "Paladin_ShieldBash_Passive",
    ]
    for sid in samples:
        r = results.get(sid)
        if r:
            print(f"=== {sid} ===")
            print(f"  buffs: {len(r['buffs'])}")
            for b in r["buffs"][:2]:
                print(f"    {b['name']}: {b['desc'][:120]}")
            print(f"  extracted_effects: {r['extracted_effects'][:5]}")
            print()


if __name__ == "__main__":
    main()
