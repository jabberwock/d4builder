#!/usr/bin/env python3
"""
Extract skill metadata from Maxroll descriptions:
- Hit count (projectiles, slashes, explosions)
- Duration (for summons/conjurations/DoTs)
- Max stack count (for summons)
- Skill role (summon, conjuration, dot, buff, instant)
- Multiplicative damage interactions (e.g. "50%[x] to Frozen")

Writes to skill_metadata.json keyed by power_name.
"""

import json
import re
from pathlib import Path

MAXROLL = (lambda: __import__("_maxroll").MAXROLL_PATH)()
OUT = Path(__file__).parent / "skill_metadata.json"


def clean(s: str) -> str:
    """Strip Maxroll markup."""
    if not s:
        return ""
    s = re.sub(r"\{[^}]*\}", "", s)
    s = re.sub(r"\[[^\]]*\|[^\]]*\]", "X", s)  # [formula|fmt] -> X
    s = re.sub(r"\\\[", "[", s).replace("\\]", "]")
    return s.replace("\n", " ").strip()


# ─── Role classification ────────────────────────────────────────────────────

def classify_role(name: str, desc: str, primary_tag: str, tags: str) -> str:
    """Classify a skill into one of: summon, conjuration, dot, buff, channel, instant."""
    desc_l = desc.lower()
    pt = primary_tag or ""
    t = tags or ""

    # DoT/zone effects come first — many "summon a serpent/wave" zones are DoTs, not pets
    is_zone = any(p in desc_l for p in [
        "burns enemies for", "burning enemies for", "bleeds enemies for",
        "poisons enemies for", "continually deal", "continually constrict",
        "continually freezes", "frostbite damage", "tick"
    ])
    has_over = " over " in desc_l and "seconds" in desc_l
    if is_zone or has_over:
        # Override summon classification — these are DoTs even if they "summon" something
        return "dot"

    if "Conjuration" in pt or "conjuration" in desc_l:
        return "conjuration"
    if "Summoning" in pt or "Companion" in pt or "Minion" in pt:
        return "summon"
    if "summon" in desc_l[:80]:
        return "summon"
    if "channel" in desc_l or "Channeled" in t:
        return "channel"
    if any(p in desc_l for p in ["become immune", "barrier", "fortify", "gain", "increase", "buff", "lasts"]):
        if "Defensive" in pt or "Aura" in pt or "Subterfuge" in pt:
            return "buff"
    return "instant"


# ─── Hit count extraction ───────────────────────────────────────────────────

def extract_hit_count(desc: str) -> int:
    """
    Extract the number of damage instances per cast.
    e.g. "Launch 5 shards" → 5, "explodes for X then deals Y" → 2
    """
    desc_l = desc.lower()
    hits = 1

    # "Launch/Hurl/Fire N <thing>" — generic projectile count (any noun after the verb+number)
    m = re.search(r"(?:launch|fire|shoot|throw|hurl|cast|unleash|summon|create|conjure|spawn|release|wield)(?:\s+(?:a|an|the))?\s*(?:barrage|volley|wave|salvo|burst|array)?\s*(?:of\s+)?(\d+)\s+\w+", desc_l)
    if m:
        try:
            hits = max(hits, int(m.group(1)))
        except ValueError:
            pass

    # "Barrage/volley of N"
    m = re.search(r"(?:barrage|volley|wave|salvo|burst|array)\s+of\s+(\d+)", desc_l)
    if m:
        try:
            hits = max(hits, int(m.group(1)))
        except ValueError:
            pass

    # "N hits", "N strikes", "N attacks"
    m = re.search(r"(\d+)\s+(?:hits|strikes|attacks|slashes|swings|blasts|waves|charges)", desc_l)
    if m:
        try:
            hits = max(hits, int(m.group(1)))
        except ValueError:
            pass

    # "N-headed hydra" → multi-target
    m = re.search(r"(\d+)-headed", desc_l)
    if m:
        hits = max(hits, int(m.group(1)))

    # Explosion + initial hit pattern
    if "explod" in desc_l and "initial" not in desc_l:
        hits += 1

    # Pierces (multi-target) — multiplies projectile count, not absolute floor
    if "pierc" in desc_l:
        hits = hits * 3  # each projectile hits ~3 enemies on average

    # Returns (passes through twice)
    if re.search(r"return(?:s|ing)?", desc_l):
        hits = int(hits * 1.5)

    # Bouncing/ricochets
    if "ricochet" in desc_l or "bounce" in desc_l:
        hits = int(hits * 2)

    return hits


# ─── Duration / cooldown extraction from formulas ───────────────────────────

def extract_duration_seconds(desc: str, raw_desc: str = "") -> float:
    """Pull skill duration in seconds from description (cleaned or raw)."""
    desc_l = desc.lower()
    raw_l = raw_desc.lower() if raw_desc else ""

    # First try to extract from raw formula expressions like "for [10*(...)|1|] seconds"
    if raw_l:
        # Match "for {markup}[N*..." where N is the base coefficient
        m = re.search(r"for\s*\{[^}]*\}\[(\d+(?:\.\d+)?)\s*\*", raw_l)
        if m:
            return float(m.group(1))
        # Match "for {markup}[N|" — pure number
        m = re.search(r"for\s*\{[^}]*\}\[(\d+(?:\.\d+)?)\s*\|", raw_l)
        if m:
            return float(m.group(1))
        m = re.search(r"lasts?\s*\{[^}]*\}\[(\d+(?:\.\d+)?)\s*\*", raw_l)
        if m:
            return float(m.group(1))

    # DoT pattern first: "X damage over N seconds" — prefer this for damage duration
    m = re.search(r"over\s+(\d+(?:\.\d+)?)\s*seconds?", desc_l)
    if m:
        return float(m.group(1))
    # Plain "for X seconds" patterns
    m = re.search(r"for\s+(\d+(?:\.\d+)?)\s*seconds?", desc_l)
    if m:
        return float(m.group(1))
    m = re.search(r"lasts?\s+(\d+(?:\.\d+)?)\s*seconds?", desc_l)
    if m:
        return float(m.group(1))
    return 0.0


def extract_cooldown_from_formula(formula) -> float:
    """Cooldowns in skill_cooldowns are like '20*Table(35,sLevel)' — coefficient is the base."""
    if formula is None or formula == "":
        return 0.0
    if isinstance(formula, (int, float)):
        return float(formula)
    formula = str(formula)
    m = re.match(r"(-?\d+\.?\d*)\s*\*\s*Table\(35", formula)
    if m:
        return float(m.group(1))
    # Plain number
    m = re.match(r"^(\d+(?:\.\d+)?)$", formula.strip())
    if m:
        return float(m.group(1))
    return 0.0


# ─── Max stack extraction (summons can have multiple active) ────────────────

def extract_max_stacks(desc: str) -> int:
    """
    "You may have up to N active at a time" → N
    """
    m = re.search(r"up to\s+(\d+)\s+\w+\s+active", desc.lower())
    if m:
        return int(m.group(1))
    return 1


# ─── Multiplicative damage interactions ─────────────────────────────────────

# Look for "X%[x]" multiplicative bonuses tied to enemy states
MULT_PATTERNS = [
    (r"(\d+)%\[?x\]?\s+(?:increased\s+)?damage\s+to\s+(frozen|chilled|stunned|vulnerable|burning|poisoned|bleeding|dazed)", "vs_state"),
    (r"(\d+)%\[?x\]?\s+critical\s+strike\s+damage", "crit_damage"),
    (r"(\d+)%\[?x\]?\s+(?:bonus\s+)?(?:lucky\s+hit|attack\s+speed|movement\s+speed)", "stat_buff"),
]


def extract_multiplicative_bonuses(desc: str) -> list[dict]:
    """Find [x] multiplicative damage bonuses in skill descriptions."""
    bonuses = []
    desc_l = desc.lower()
    for pat, kind in MULT_PATTERNS:
        for m in re.finditer(pat, desc_l):
            try:
                val = int(m.group(1))
                target = m.group(2) if len(m.groups()) > 1 else None
                bonuses.append({"kind": kind, "value_pct": val, "target": target})
            except (ValueError, IndexError):
                continue
    return bonuses


# ─── Utility effect detection ───────────────────────────────────────────────
# Detect mechanical effects in descriptions for skills that don't deal direct damage
# but provide huge gameplay value (curses, CC, grouping, buffs).

UTILITY_EFFECT_PATTERNS: list[tuple[str, str, float]] = [
    # (regex pattern, effect tag, utility value)

    # === GLOBAL damage buffs (highest value — multiply ALL bar damage) ===
    (r"increasing your damage dealt", "global_damage_buff", 3.0),
    (r"deal\s+\d+%?\s*increased\s+damage", "global_damage_buff", 3.0),
    (r"berserking", "grants_berserking", 2.0),
    (r"all\s+\S+\s+damage\s+is\s+increased", "global_damage_buff", 3.0),
    # Aura passives — permanent buffs to allies/self
    (r"passive:.*?attack speed", "aura_attack_speed", 3.0),
    (r"passive:.*?critical strike", "aura_crit", 3.0),
    (r"passive:.*?armor and.*?resistance", "aura_defense", 2.5),
    (r"passive:.*?bonus on all resistances", "aura_defense", 2.5),
    (r"passive:.*?dealing.*?damage", "aura_dot", 2.0),
    (r"passive:.*?heal", "aura_heal", 2.0),
    (r"passive:.*?spirit", "aura_spirit", 2.0),

    # === Damage amplification (huge value) ===
    (r"deal\s+\S+\s+less\s+damage", "enemy_damage_reduction", 1.5),
    (r"take\s+\S+(?:\s+more|\s+increased)?\s+damage", "enemy_damage_amp", 1.5),
    (r"vulnerable", "applies_vulnerable", 1.2),
    (r"weaken", "applies_weaken", 1.0),

    # === Crowd control ===
    (r"\bstun(?:s|ned|ning)?\b", "applies_stun", 0.9),
    (r"\bfreez(?:e|es|ing|en)\b", "applies_freeze", 1.0),
    (r"\bdaze(?:s|d|ing)?\b", "applies_daze", 0.7),
    (r"\bslow(?:s|ed|ing)?(?:\s+by)?\b", "applies_slow", 0.5),
    (r"\bfear(?:s|ed|ing)?\b", "applies_fear", 0.6),
    (r"\bknock(?:back|down|s)?\b", "applies_knockback", 0.4),
    (r"\bimmobiliz(?:e|es|ed)\b", "applies_immobilize", 0.7),
    (r"\bchill(?:s|ed|ing)?\b", "applies_chill", 0.4),
    (r"\bunhinder(?:ed)?\b", "applies_unhinder", 0.3),
    (r"\btaunt(?:s|ed|ing)?\b", "applies_taunt", 0.5),

    # === Grouping (massively increases AoE value) ===
    (r"pull(?:s|ing|ed)?\s+(?:in\s+)?enemies", "groups_enemies", 1.3),
    (r"draws?\s+enemies", "groups_enemies", 1.3),

    # === Defensive buffs ===
    (r"barrier", "grants_barrier", 1.0),
    (r"fortify", "grants_fortify", 1.0),
    (r"become immune", "grants_immune", 1.5),
    (r"unstoppable", "grants_unstoppable", 0.6),
    (r"(\d+)% damage reduction", "damage_reduction_pct", 1.0),
    (r"reduces?\s+damage\s+taken", "damage_reduction", 0.9),
    (r"heal(?:s|ed|ing)?", "heals", 0.5),

    # === Offensive buffs ===
    (r"(\d+)%\s*attack speed", "grants_attack_speed", 1.0),
    (r"(\d+)%\s*movement speed", "grants_movement_speed", 0.6),
    (r"berserk", "grants_berserk", 1.0),
    (r"(\d+)%\s*critical strike chance", "grants_crit_chance", 1.2),
    (r"(\d+)%\s*critical strike damage", "grants_crit_damage", 1.2),
    (r"(\d+)%\s*more damage", "grants_more_damage", 1.5),
    (r"(\d+)%\s*increased damage", "grants_inc_damage", 0.8),

    # === Resource generation ===
    (r"generate(?:s)?\s+\d+\s+(?:essence|fury|spirit|mana|energy|resource)", "gens_resource", 0.5),
    (r"restore(?:s)?\s+(?:essence|fury|spirit|mana|energy|resource)", "restores_resource", 0.5),

    # === Class-specific mechanics ===
    (r"corpse(?:s)?", "creates_corpses", 0.4),
    (r"berserking", "grants_berserk", 1.0),
    (r"crackling energy", "grants_crackling", 0.8),
    (r"enchantment slot", "fills_enchant_slot", 0.6),
]


def extract_utility_effects(desc: str, raw_desc: str = "") -> list[dict]:
    """
    Detect utility effects from skill description.
    Returns a list of {tag, value} for each effect found.
    """
    effects = []
    desc_l = desc.lower()
    seen_tags: set[str] = set()
    for pat, tag, value in UTILITY_EFFECT_PATTERNS:
        if tag in seen_tags:
            continue
        if re.search(pat, desc_l):
            effects.append({"tag": tag, "value": value})
            seen_tags.add(tag)
    return effects


# ─── Main extraction ────────────────────────────────────────────────────────

def main():
    if not MAXROLL.exists():
        print(f"ERROR: {MAXROLL} not found")
        return

    with open(MAXROLL) as f:
        md = json.load(f)

    skills_mx = md.get("skills", {})
    metadata = {}

    for sid, s in skills_mx.items():
        name = s.get("name", "")
        if not name:
            continue
        raw_desc = s.get("desc", "")
        desc = clean(raw_desc)
        if not desc:
            continue

        primary_tag = s.get("primaryTag", "")
        cooldown_formula = s.get("cooldown", "")

        role = classify_role(name, desc, primary_tag, "")
        hits = extract_hit_count(desc)
        duration = extract_duration_seconds(desc, raw_desc)
        max_stacks = extract_max_stacks(desc)
        cooldown = extract_cooldown_from_formula(cooldown_formula)
        mult_bonuses = extract_multiplicative_bonuses(desc)
        utility_effects = extract_utility_effects(desc, raw_desc)

        metadata[sid] = {
            "name": name,
            "role": role,
            "hit_count": hits,
            "duration_sec": duration,
            "max_stacks": max_stacks,
            "cooldown_sec": cooldown,
            "mult_bonuses": mult_bonuses,
            "utility_effects": utility_effects,
            "primary_tag": primary_tag,
        }

    with open(OUT, "w") as f:
        json.dump(metadata, f, indent=2)

    # Print summary
    print(f"Extracted metadata for {len(metadata)} skills")
    role_counts: dict[str, int] = {}
    for m in metadata.values():
        role_counts[m["role"]] = role_counts.get(m["role"], 0) + 1
    print("Role breakdown:")
    for role, count in sorted(role_counts.items(), key=lambda x: -x[1]):
        print(f"  {role:15s} {count}")

    # Show some interesting examples
    print("\nSummon/Conjuration skills with stack info:")
    for sid, m in metadata.items():
        if m["role"] in ("summon", "conjuration") and m["max_stacks"] > 1:
            print(f"  {m['name']:25s} stacks={m['max_stacks']}  duration={m['duration_sec']}s  hits={m['hit_count']}")

    print("\nSkills with multi-hit:")
    for sid, m in metadata.items():
        if m["hit_count"] > 1 and m["role"] != "summon":
            print(f"  {m['name']:25s} hits={m['hit_count']}")

    print("\nSkills with multiplicative bonuses:")
    count = 0
    for sid, m in metadata.items():
        if m["mult_bonuses"]:
            print(f"  {m['name']:25s} {m['mult_bonuses']}")
            count += 1
            if count > 15:
                break


if __name__ == "__main__":
    main()
