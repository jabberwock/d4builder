#!/usr/bin/env python3
"""
Diablo 4 Build Optimizer
Reads from d4_stats.db, writes results to optimizer_results.db.

Scoring model (max-roll assumption):
  base_skill_score = damage_pct@rank7 / max(cooldown_base, 1.0)
  aspect_multiplier = 1.0 + sum(max_value/100 for linked affixes)
  temper_multiplier = 1.0 + sum(max_value/100 for matching tempers)
  skill_score = base_skill_score * aspect_multiplier * temper_multiplier
  rune_score = proc_rate * inv_value  (proc_rate = offering_gain / offering_cost)
  rune_bonus = sum(top-2 runeword scores) / 100.0
  build_score = sum(skill_scores) + rune_bonus
"""

import sqlite3
import json
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

DB_IN          = Path(__file__).parent / "d4_stats.db"
DB_OUT         = Path(__file__).parent / "optimizer_results.db"
MORPHS_PATH    = Path(__file__).parent / "skill_morphs.json"
MAXROLL_PATH = (lambda: __import__("_maxroll").MAXROLL_PATH)()
DUNGEONS_PATH  = Path(__file__).parent / "nightmare_dungeons.json"

# Load morph data once at module level
_MORPHS: dict = json.load(open(MORPHS_PATH)) if MORPHS_PATH.exists() else {}

# ---------------------------------------------------------------------------
# Build aspect tag index from Maxroll JSON (FILTER_Flex_CLASS_SkillName tags)
# Maps legendary_* internal_name -> list of associated skill name words (lowercase)
# ---------------------------------------------------------------------------
def _build_maxroll_aspect_index() -> dict[str, list[str]]:
    """
    Parse Maxroll game data JSON to extract skill-to-aspect associations.
    Each legendary affix with FILTER_Flex_CLASS_SkillName tags in its tags list
    gets a mapping: internal_name -> ['skill', 'name', 'words', ...]
    """
    if not MAXROLL_PATH.exists():
        return {}
    try:
        data = json.load(open(MAXROLL_PATH))
    except (json.JSONDecodeError, OSError):
        return {}

    # Maxroll affixes are in data["affixes"] as a dict of {id: affix_object}
    affixes = data.get("affixes") or data.get("Affixes") or {}
    if isinstance(affixes, list):
        affixes = {str(i): a for i, a in enumerate(affixes)}

    index: dict[str, list[str]] = {}
    flex_re = re.compile(r'FILTER_Flex_\w+?_([A-Z][A-Za-z0-9]+)$')

    for iname, affix in affixes.items():
        # In Maxroll JSON the dict key IS the internal name
        if not isinstance(iname, str) or not iname.lower().startswith("legendary_"):
            continue
        tags = affix.get("tags") or []
        skill_words: list[str] = []
        for tag in tags:
            m = flex_re.match(tag)
            if m:
                # CamelCase → space-separated words, lowercase
                raw = m.group(1)
                words = re.sub(r'([A-Z])', r' \1', raw).strip().lower()
                skill_words.append(words)
        if skill_words:
            index[iname.lower()] = skill_words
    return index

_ASPECT_TAG_INDEX: dict[str, list[str]] = _build_maxroll_aspect_index()

# ---------------------------------------------------------------------------
# Load nightmare dungeon data (d4planner.io coordinates)
# Maps aspect internal_name (lowercase) -> dungeon dict
# ---------------------------------------------------------------------------
def _load_dungeon_index() -> dict[str, dict]:
    """
    Load nightmare_dungeons.json and return a lookup:
      aspect_internal_name_lower -> dungeon entry dict
    """
    if not DUNGEONS_PATH.exists():
        return {}
    try:
        raw = json.load(open(DUNGEONS_PATH))
        dungeons = raw.get("dungeons", raw) if isinstance(raw, dict) else raw
    except (json.JSONDecodeError, OSError):
        return {}
    index: dict[str, dict] = {}
    for d in dungeons:
        asp = (d.get("aspect") or "").lower()
        if asp:
            index[asp] = d
    return index

_DUNGEON_INDEX: dict[str, dict] = _load_dungeon_index()

TOP_N_PER_SPEC = 5
MAX_OTHER_CANDIDATES = 25   # pre-rank top N before combinatorics


@dataclass
class ScoringData:
    """Bundles all DB-derived lookup tables used during scoring."""
    skills:          dict
    damage_map:      dict
    cooldowns:       dict
    aspect_bonuses:  dict
    temper_bonuses:  dict
    # New: extended recommendation tables
    affix_rows:      list = field(default_factory=list)   # raw (internal_name, display_name, max_value, category)
    temper_rows:     list = field(default_factory=list)   # raw tempering_recipes rows
    item_rows:       list = field(default_factory=list)   # raw items rows (unique only, pre-filtered)
    gem_lookup:      dict = field(default_factory=dict)   # gem_type -> {quality -> row_dict}


# How many regular passives to include in the output
TOP_PASSIVES = 10


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_skills(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT power_name, display_name, class, is_passive, "
        "skill_tags, primary_tag FROM skills"
    ).fetchall()
    skills: dict[str, dict] = {}
    for power_name, display_name, cls, is_passive, skill_tags, primary_tag in rows:
        skills[power_name] = {
            "power_name": power_name,
            "display_name": display_name or power_name,
            "class": cls or "",
            "is_passive": bool(is_passive),
            "skill_tags": skill_tags or "",
            "primary_tag": primary_tag or "",
        }
    return skills


def load_skill_damage(conn: sqlite3.Connection) -> dict[str, float]:
    """Return damage_pct per skill at rank 5 (active skill cap)."""
    rows = conn.execute(
        "SELECT power_name, damage_pct FROM skill_damage WHERE rank = 5"
    ).fetchall()
    return {pname: float(dpct) for pname, dpct in rows if dpct is not None}


def load_cooldowns(conn: sqlite3.Connection) -> dict[str, float]:
    """Return cooldown_base per skill (NULL/0 → treated as 1.0 in scoring)."""
    rows = conn.execute(
        "SELECT power_name, cooldown_base FROM skill_cooldowns"
    ).fetchall()
    cd: dict[str, float] = {}
    for pname, cb in rows:
        if cb is not None:
            try:
                cd[pname] = float(cb)
            except (TypeError, ValueError):
                pass
    return cd


def load_aspect_bonuses(conn: sqlite3.Connection) -> dict[str, float]:
    """
    Sum max_value/100 for all affixes whose display_name text contains the skill
    display_name as a substring (case-insensitive).

    Returns: skill_display_name (lower) -> cumulative bonus sum
    Note: aspect_multiplier = 1.0 + bonus_sum
    """
    affix_rows = conn.execute(
        "SELECT internal_name, display_name, max_value FROM affixes "
        "WHERE max_value IS NOT NULL AND max_value > 0"
    ).fetchall()
    skill_rows = conn.execute(
        "SELECT display_name FROM skills WHERE display_name IS NOT NULL AND display_name != ''"
    ).fetchall()
    skill_names = {row[0].lower() for row in skill_rows if len(row[0]) > 3}

    # Build: lower(skill_display_name) -> sum of max_value/100 from matching affixes
    bonus: dict[str, float] = {}
    for _iname, disp, mv in affix_rows:
        if not disp:
            continue
        disp_lower = disp.lower()
        for sname in skill_names:
            if sname in disp_lower:
                bonus[sname] = bonus.get(sname, 0.0) + float(mv) / 100.0
    return bonus


def load_temper_bonuses(conn: sqlite3.Connection) -> dict[str, float]:
    """
    Parse tempering_recipes.affix_key (JSON array of strings).
    Match entries of pattern "Tempered_*_Skill_<Class>_<SkillName>_*"
    against skill power_name suffix (power_name.split("_", 1)[1] if class_ prefix).

    Returns: skill_power_name -> sum of max_value/100 for matching temper entries
    """
    rows = conn.execute(
        "SELECT recipe_name, affix_key, max_value FROM tempering_recipes "
        "WHERE affix_key IS NOT NULL AND max_value IS NOT NULL"
    ).fetchall()

    # Build flat list: (skill_name_segment_lower, max_value) for all temper keys
    temper_entries: list[tuple[str, float]] = []
    for _recipe, affix_key_json, mv in rows:
        try:
            keys = json.loads(affix_key_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(keys, list):
            continue
        for key in keys:
            # Only match skill-specific tempers: pattern contains "_Skill_"
            parts = key.split("_")
            try:
                skill_idx = parts.index("Skill") + 1  # index after "Skill"
                # parts[skill_idx] = Class abbrev (Barb, Rogue, etc.)
                # parts[skill_idx+1] = SkillName segment
                if skill_idx + 1 < len(parts):
                    seg = parts[skill_idx + 1].lower()
                    temper_entries.append((seg, float(mv)))
            except (ValueError, IndexError):
                continue

    # Now match against each skill's power_name
    skill_rows = conn.execute("SELECT power_name FROM skills").fetchall()
    bonus: dict[str, float] = {}
    for (pname,) in skill_rows:
        # Extract "short name" from power_name: e.g. "Barbarian_Whirlwind" -> "whirlwind"
        parts = pname.split("_", 1)
        short = parts[1].lower() if len(parts) > 1 else pname.lower()
        total = 0.0
        for seg, mv in temper_entries:
            if seg == short or short.startswith(seg) or seg.startswith(short):
                total += mv / 100.0
        if total > 0.0:
            bonus[pname] = total
    return bonus


def load_affixes(conn: sqlite3.Connection) -> list[tuple]:
    """Load all affixes with display_name for recommendation use."""
    return conn.execute(
        "SELECT internal_name, display_name, max_value, affix_category "
        "FROM affixes WHERE display_name IS NOT NULL AND max_value IS NOT NULL AND max_value > 0"
    ).fetchall()


def load_temper_rows(conn: sqlite3.Connection) -> list[tuple]:
    """Load all tempering_recipes rows."""
    return conn.execute(
        "SELECT recipe_name, display_name, class, item_type, affix_key, max_value, category "
        "FROM tempering_recipes WHERE max_value IS NOT NULL"
    ).fetchall()


def load_item_rows(conn: sqlite3.Connection) -> list[tuple]:
    """
    Load unique items, pre-filtered to remove placeholders and test entries.
    Returns (item_name, display_name, item_type, usable_by_class, affixes_json).
    """
    return conn.execute(
        "SELECT item_name, display_name, item_type, usable_by_class, affixes "
        "FROM items "
        "WHERE magic_type = 'unique' "
        "  AND display_name IS NOT NULL "
        "  AND display_name NOT LIKE '%[PH]%' "
        "  AND display_name NOT LIKE '%(PH)%' "
        "  AND display_name NOT LIKE '%test%' "
        "  AND display_name NOT LIKE '%Test%' "
        "  AND display_name NOT LIKE '%MeganS%' "
    ).fetchall()


def load_gem_lookup(conn: sqlite3.Connection) -> dict:
    """Return {gem_type: {quality: row_dict}} from gems table."""
    rows = conn.execute(
        "SELECT gem_name, display_name, gem_type, quality, "
        "weapon_bonus, armor_bonus, jewelry_bonus FROM gems"
    ).fetchall()
    result: dict = {}
    for gem_name, display_name, gem_type, quality, wb, ab, jb in rows:
        result.setdefault(gem_type, {})[quality] = {
            "gem_name": gem_name,
            "display_name": display_name,
            "gem_type": gem_type,
            "quality": quality,
            "weapon_bonus": wb,
            "armor_bonus": ab,
            "jewelry_bonus": jb,
        }
    return result


def load_runes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT rune_name, display_name, rune_type, runic_amount, "
        "offering_gain, offering_cost "
        "FROM runes"
    ).fetchall()
    runes = []
    for rname, dname, rtype, amt, gain, cost in rows:
        if rname.lower().startswith("test_"):
            continue
        runes.append({
            "rune_name": rname,
            "display_name": dname or rname,
            "rune_type": rtype or "",
            "runic_amount": float(amt) if amt is not None else 0.0,
            "offering_gain": float(gain) if gain is not None else None,
            "offering_cost": float(cost) if cost is not None else None,
        })
    return runes


def load_specializations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT class, specialization_name, mechanic_type, "
        "required_skill_tags, generator_tags, spender_tags "
        "FROM specializations"
    ).fetchall()
    specs = []
    for cls, name, mtype, req, gen, spend in rows:
        specs.append({
            "class": cls,
            "name": name,
            "mechanic_type": mtype or "",
            "required_skill_tags": req or "",
            "generator_tags": gen or "",
            "spender_tags": spend or "",
        })
    return specs


# ---------------------------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------------------------

def has_tag(skill: dict, tag: str) -> bool:
    return tag in skill["skill_tags"] or tag in skill["primary_tag"]


def is_basic(skill: dict) -> bool:
    return has_tag(skill, "Skill_Basic") or has_tag(skill, "Skill_Primary_Basic")


def is_ultimate(skill: dict) -> bool:
    return has_tag(skill, "Skill_Primary_Ultimate")


def is_core(skill: dict) -> bool:
    return has_tag(skill, "Skill_Primary_Core")


def matches_tag_list(skill: dict, tag_csv: str) -> bool:
    """Return True if skill has ANY tag in a comma-separated list."""
    if not tag_csv or tag_csv == "any":
        return True
    for tag in tag_csv.split(","):
        tag = tag.strip()
        if tag and has_tag(skill, tag):
            return True
    return False


# ---------------------------------------------------------------------------
# Rune optimizer
# ---------------------------------------------------------------------------

INVOCATION_VALUE = {
    # damage
    'Tec': 3.0, 'Kry': 3.0, 'Tal': 3.0, 'Ton': 3.0, 'Yom': 3.0, 'Tzic': 3.0,
    # cc
    'Tun': 2.5, 'Thul': 2.5, 'Wat': 2.5,
    # buff
    'Ohm': 2.0, 'Lac': 2.0, 'Vex': 2.0, 'Xal': 2.0, 'Qax': 2.0, 'Xan': 2.0,
    'Mot': 2.0, 'Ner': 2.0,
    # resource/cd
    'Eom': 1.5, 'Zec': 1.5, 'Lum': 1.5, 'Ceh': 1.5,
    # mobility
    'Jah': 1.0, 'Qua': 1.0,
    # default
}


def score_runeword(ritual: dict, invocation: dict) -> float:
    if not ritual['offering_gain'] or not invocation['offering_cost']:
        return 0.0
    proc_rate = ritual['offering_gain'] / invocation['offering_cost']
    inv_value = INVOCATION_VALUE.get(invocation['display_name'], 1.0)
    return proc_rate * inv_value


def best_two_runewords(
    rituals: list[dict],
    invocations: list[dict],
) -> list[tuple[float, dict, dict]]:
    """Score all pairs, pick top 2 with no rune reuse."""
    pairs = []
    for r in rituals:
        for i in invocations:
            score = score_runeword(r, i)
            pairs.append((score, r, i))
    pairs.sort(key=lambda x: -x[0])

    chosen = []
    used: set[str] = set()
    for score, r, i in pairs:
        rn = r['display_name']
        inv = i['display_name']
        if rn not in used and inv not in used:
            chosen.append((score, r, i))
            used.add(rn)
            used.add(inv)
        if len(chosen) == 2:
            break
    return chosen


def best_rune_pairs(
    runes: list[dict],
) -> tuple[dict | None, dict | None, float]:
    """
    Two runeword slots, each = (ritual, invocation).
    Pick 4 distinct runes (2 ritual + 2 invocation) maximising proc_rate * inv_value.
    No rune may appear in both pairs.
    """
    rituals    = [r for r in runes if r["rune_type"] == "ritual"]
    invocations = [r for r in runes if r["rune_type"] == "invocation"]

    if not rituals or not invocations:
        return None, None, 0.0

    chosen = best_two_runewords(rituals, invocations)

    if not chosen:
        return None, None, 0.0

    def to_dict(score: float, r: dict, i: dict) -> dict:
        return {
            "ritual": r["display_name"],
            "invocation": i["display_name"],
            "score": round(score, 6),
        }

    p1 = to_dict(*chosen[0])
    p2 = to_dict(*chosen[1]) if len(chosen) > 1 else None
    total_score = sum(c[0] for c in chosen)
    return p1, p2, total_score


# ---------------------------------------------------------------------------
# Skill scoring
# ---------------------------------------------------------------------------

def score_skill(pname: str, sd: ScoringData) -> float:
    """Compute final skill score."""
    skill = sd.skills.get(pname)
    if not skill:
        return 0.0

    damage_pct = sd.damage_map.get(pname, 0.0)
    cd = sd.cooldowns.get(pname, 0.0) or 0.0
    cd_divisor = max(cd, 1.0)

    base = damage_pct / cd_divisor

    # aspect_multiplier: 1.0 + sum(max_value/100) for affixes mentioning this skill
    disp_lower = skill["display_name"].lower()
    aspect_mult = 1.0 + sd.aspect_bonuses.get(disp_lower, 0.0)

    # temper_multiplier: 1.0 + sum(max_value/100) for matching tempers
    temper_mult = 1.0 + sd.temper_bonuses.get(pname, 0.0)

    return base * aspect_mult * temper_mult


# Tags that define a skill's damage school/archetype.
# Skills with overlapping SCHOOL_TAGS form coherent builds.
SCHOOL_TAGS: frozenset[str] = frozenset([
    # Sorcerer elements
    'Skill_Fire', 'Skill_Lightning', 'Skill_Cold',
    # Necromancer schools
    'Skill_Blood', 'Skill_Bone', 'Skill_Corruption',
    # Rogue archetypes
    'Skill_Marksman', 'Skill_Cutthroat', 'Skill_Trap', 'Skill_Shadow',
    # Barbarian physical schools
    'Skill_Bludgeoning', 'Skill_Channeled', 'Skill_Bleeding', 'Skill_Slam',
    # Spiritborn guardian spirits
    'Skill_Spirit_Soil', 'Skill_Spirit_Sky', 'Skill_Spirit_Forest', 'Skill_Spirit_Plains',
    # Paladin oaths
    'Skill_Disciple', 'Skill_Divine', 'Skill_Juggernaut', 'Skill_Zealot',
    # General
    'Skill_Physical', 'Skill_Primary_Summoning',
])

# Synergy multiplier per overlapping school tag with the primary skill.
# e.g. SYNERGY_PER_TAG=2.0 → 1 overlap = 3x, 2 overlaps = 5x.
SYNERGY_PER_TAG = 2.0


def _school_tags(skill: dict) -> frozenset[str]:
    """Return the school/archetype tags for a skill."""
    tags = (skill.get("skill_tags") or "").split(",")
    return frozenset(t.strip() for t in tags if t.strip() in SCHOOL_TAGS)


def build_score_breakdown(
    combo: list[str],
    sd: ScoringData,
    spec_multipliers: dict[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    """
    Score a 6-skill combo.

    Synergy system: identify the primary damage skill (highest-scoring non-basic,
    non-ultimate).  Any other skill that shares school tags with the primary gets
    a strong multiplier, rewarding elemental/archetype coherence.
    """
    # Identify primary: highest raw score among non-basic, non-ultimate skills
    non_support = [
        p for p in combo
        if p in sd.skills and not is_basic(sd.skills[p]) and not is_ultimate(sd.skills[p])
    ]
    primary = max(non_support, key=lambda p: score_skill(p, sd), default=None)
    primary_school = _school_tags(sd.skills[primary]) if primary and primary in sd.skills else frozenset()

    breakdown: dict[str, float] = {}
    total = 0.0
    for pname in combo:
        s = score_skill(pname, sd)
        if spec_multipliers and pname in spec_multipliers:
            s *= spec_multipliers[pname]

        # Synergy bonus: skills sharing the primary's school tags are rewarded
        if primary_school and pname != primary and pname in sd.skills:
            overlap = len(_school_tags(sd.skills[pname]) & primary_school)
            if overlap:
                s *= (1.0 + SYNERGY_PER_TAG * overlap)

        display = sd.skills[pname]["display_name"] if pname in sd.skills else pname
        breakdown[display] = round(s, 6)
        total += s
    return total, breakdown


# ---------------------------------------------------------------------------
# Class-level optimization
# ---------------------------------------------------------------------------

def active_class_skills(
    cls: str, skills: dict
) -> list[dict]:
    """Active (non-passive) skills for a class only — no Generic/Mercenary leakage."""
    return [
        s for s in skills.values()
        if s["class"] == cls
        and not s["is_passive"]
        and s["display_name"]
        and not s["display_name"].startswith("{c_red}")
        and not s["display_name"].startswith("(PH)")
        and not s["display_name"].startswith("(DNS)")
    ]


def pre_score_skills(
    pool: list[dict],
    sd: ScoringData,
) -> list[tuple[float, dict]]:
    """Return [(score, skill_dict), ...] sorted descending."""
    scored = [(score_skill(s["power_name"], sd), s) for s in pool]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _best_in_tier(
    tier_skills: list[dict],
    school: frozenset[str],
    sd: ScoringData,
    exclude: set[str],
) -> dict | None:
    """Pick best skill in a tier. Prefer school match, else highest score."""
    available = [s for s in tier_skills if s["power_name"] not in exclude]
    if not available:
        return None
    if school:
        matching = [s for s in available if _school_tags(s) & school]
        if matching:
            return max(matching, key=lambda s: score_skill(s["power_name"], sd))
    return max(available, key=lambda s: score_skill(s["power_name"], sd))


def optimize_class_spec(
    cls: str,
    spec: dict,
    sd: ScoringData,
    runes: list[dict],
) -> list[dict]:
    """
    Archetype-driven build generation.

    For each Core skill (the build's identity):
      1. Extract its school tags (Fire, Lightning, Blood, Cutthroat, etc.)
      2. Pick 1 Basic that matches the school (or best generic)
      3. Pick 1 Ultimate that matches
      4. Fill remaining 3 slots from other tiers, preferring school matches
      5. Score the complete build with synergy bonuses
    """
    pool = active_class_skills(cls, sd.skills)
    if not pool:
        return []

    mtype = spec["mechanic_type"]

    # Spec-specific multipliers
    spec_multipliers: dict[str, float] = {}
    if cls == "Rogue" and mtype == "combo_points":
        # Per-skill Combo Points scaling at 3 stacks (max)
        _CP_SCALING: dict[str, float] = {
            "Rogue_Barrage":          1.60,  # +20% per point
            "Rogue_RapidFire":        1.39,  # +13% per point
            "Rogue_PenetratingShot":  1.90,  # +30% per point
            "Rogue_Flurry":           1.75,  # +25% per point
            "Rogue_TwistingBlades":   1.90,  # +30% per point
        }
        for s in pool:
            if is_core(s):
                pname = s["power_name"]
                spec_multipliers[pname] = _CP_SCALING.get(pname, 1.39)
    elif cls == "Rogue" and mtype == "inner_sight":
        # Inner Sight: unlimited Energy for 4s every ~15s.
        # High-cost Core skills benefit most — boost Core skills that
        # normally drain resources fast (effectively removes resource gating).
        for s in pool:
            if is_core(s):
                spec_multipliers[s["power_name"]] = 1.30  # resource-free burst window
    elif cls == "Rogue" and mtype == "preparation":
        # Preparation: casting Ultimate resets all other cooldowns + 15% DR.
        # Heavily favors builds that lean on Ultimate + cooldown skills.
        for s in pool:
            if is_ultimate(s):
                spec_multipliers[s["power_name"]] = 1.50  # Ultimate is the engine
            elif has_tag(s, "Skill_Primary_Defensive") or has_tag(s, "Skill_Primary_Weapon_Mastery"):
                # Cooldown skills get reset by Ultimate — more uptime
                cd = sd.cooldowns.get(s["power_name"], 0.0) or 0.0
                if cd > 0:
                    spec_multipliers[s["power_name"]] = 1.25
    elif cls == "Paladin":
        # Each oath boosts matching skills and penalizes other-oath skills.
        _OATH_TAG: dict[str, str] = {
            "oaths_disciple":   "Skill_Disciple",
            "oaths_judicator":  "Skill_Divine",
            "oaths_juggernaut": "Skill_Juggernaut",
            "oaths_zealot":     "Skill_Zealot",
        }
        _ALL_OATH_TAGS = set(_OATH_TAG.values())
        oath_tag = _OATH_TAG.get(mtype)
        if oath_tag:
            for s in pool:
                tags = s["skill_tags"]
                if oath_tag in tags:
                    spec_multipliers[s["power_name"]] = 1.50
                elif any(t in tags for t in _ALL_OATH_TAGS - {oath_tag}):
                    # Skill belongs to a different oath — penalize
                    spec_multipliers[s["power_name"]] = 0.50
    elif cls == "Spiritborn":
        # Each guardian spirit boosts matching skills and penalizes other spirits.
        _SPIRIT_TAG: dict[str, str] = {
            "guardian_centipede": "Skill_Spirit_Soil",
            "guardian_eagle":    "Skill_Spirit_Sky",
            "guardian_gorilla":  "Skill_Spirit_Forest",
            "guardian_jaguar":   "Skill_Spirit_Plains",
        }
        _ALL_SPIRIT_TAGS = set(_SPIRIT_TAG.values())
        spirit_tag = _SPIRIT_TAG.get(mtype)
        if spirit_tag:
            for s in pool:
                tags = s["skill_tags"]
                if spirit_tag in tags:
                    spec_multipliers[s["power_name"]] = 1.50
                elif any(t in tags for t in _ALL_SPIRIT_TAGS - {spirit_tag}):
                    # Skill belongs to a different spirit — penalize
                    spec_multipliers[s["power_name"]] = 0.40
    elif cls == "Necromancer" and mtype == "book_sacrifice":
        # Full-sacrifice: no minions, passive buffs. Favor direct damage skills.
        # Sacrifice bonuses: +10% Crit, +20% Vulnerable, +30% Overpower,
        # +15% Attack Speed, +35% Crit Damage — all scale personal damage.
        for s in pool:
            if is_core(s) or is_ultimate(s):
                spec_multipliers[s["power_name"]] = 1.40
            # Penalize summon-synergy skills (they lose their minions)
            if "Skill_Primary_Summoning" in s["skill_tags"]:
                spec_multipliers[s["power_name"]] = 0.60
    elif cls == "Necromancer" and mtype == "book_summon":
        # Full-summon: keep all minions, favor summon-synergy skills.
        for s in pool:
            if "Skill_Primary_Summoning" in s["skill_tags"]:
                spec_multipliers[s["power_name"]] = 1.40

    # Group skills by tier
    by_tier: dict[str, list[dict]] = {}
    for s in pool:
        tier = s["primary_tag"].replace("Skill_Primary_", "")
        by_tier.setdefault(tier, []).append(s)

    core_skills = by_tier.get("Core", [])
    if not core_skills:
        return []

    # Sort Core skills by raw score descending — each one anchors an archetype
    core_skills.sort(key=lambda s: score_skill(s["power_name"], sd), reverse=True)

    # Tiers that are neither Basic/Core/Ultimate — fill slots from these
    fill_tiers = [t for t in by_tier if t not in ("Basic", "Core", "Ultimate")]

    results: list[tuple[float, list[str], dict[str, float]]] = []
    seen_combos: set[tuple[str, ...]] = set()

    for anchor in core_skills:
        school = _school_tags(anchor)
        used: set[str] = {anchor["power_name"]}
        combo: list[dict] = [anchor]

        # 1 Basic — school match preferred
        basic = _best_in_tier(by_tier.get("Basic", []), school, sd, used)
        if basic:
            combo.insert(0, basic)
            used.add(basic["power_name"])

        # 1 Ultimate — school match preferred
        ult = _best_in_tier(by_tier.get("Ultimate", []), school, sd, used)
        if ult:
            used.add(ult["power_name"])

        # Fill remaining slots from other tiers (school match preferred)
        # Sort tiers by best available score so the strongest tiers fill first
        tier_order = sorted(
            fill_tiers,
            key=lambda t: max(
                (score_skill(s["power_name"], sd) for s in by_tier[t]
                 if s["power_name"] not in used),
                default=0,
            ),
            reverse=True,
        )
        mid_skills: list[dict] = []
        for tier_name in tier_order:
            best = _best_in_tier(by_tier[tier_name], school, sd, used)
            if best:
                mid_skills.append(best)
                used.add(best["power_name"])

        # Also consider a 2nd Core skill from the same school
        second_core = _best_in_tier(by_tier["Core"], school, sd, used)
        if second_core:
            mid_skills.append(second_core)
            used.add(second_core["power_name"])

        # Sort mid skills by score, take enough to fill 6 slots total
        mid_skills.sort(key=lambda s: score_skill(s["power_name"], sd), reverse=True)
        slots_needed = 6 - len(combo) - (1 if ult else 0)
        combo.extend(mid_skills[:slots_needed])

        # Ultimate goes last
        if ult:
            combo.append(ult)

        if len(combo) < 6:
            continue  # not enough skills for a full build

        combo = combo[:6]

        # Deduplicate
        combo_key = tuple(sorted(s["power_name"] for s in combo))
        if combo_key in seen_combos:
            continue
        seen_combos.add(combo_key)

        pnames = [s["power_name"] for s in combo]
        total, bd = build_score_breakdown(pnames, sd, spec_multipliers)
        results.append((total, pnames, bd))

    results.sort(key=lambda x: x[0], reverse=True)
    top = results[:TOP_N_PER_SPEC]

    # Rune bonus
    pair1, pair2, rune_total = best_rune_pairs(runes)
    rune_bonus = rune_total / 100.0

    # Sorcerer enchantment bonus
    all_scored = pre_score_skills(pool, sd)
    enchant_bonus = 0.0
    if cls == "Sorcerer" and mtype == "enchantment_slots":
        if top:
            bar_set = set(top[0][1])
            non_bar = [s for s in all_scored if s[1]["power_name"] not in bar_set
                       and not is_basic(s[1])][:2]
            enchant_bonus = len(non_bar) * 0.15 * (top[0][0] if top[0][0] else 0.0)

    # Pre-compute recommendations that are the same for all builds in this spec
    # (Gear + gems are class-level; aspects/tempers/merc vary per skill_bar combo)
    gem_rec = select_gems(
        top[0][0] if top else 0.0,
        cls,
        sd,
    )

    builds = []
    for rank, (score, combo, breakdown) in enumerate(top, start=1):
        final_score = score + rune_bonus + (enchant_bonus if rank == 1 else 0.0)
        key_passive, passives = select_passives(cls, combo, sd)
        skill_names = [sd.skills[p]["display_name"] for p in combo if p in sd.skills]
        skill_upgrades = build_skill_upgrades(skill_names, sd)

        # Collect skill_tags for all skills in this combo
        combo_skill_tags = [
            sd.skills[p]["skill_tags"]
            for p in combo
            if p in sd.skills
        ]

        # Feature 1: Aspect recommendations
        aspects_rec = select_aspects(cls, skill_names, sd)

        # Feature 2: Temper recommendations
        tempers_rec = select_tempers(cls, skill_names, sd)

        # Feature 4: Gear recommendations
        gear_rec = select_gear(cls, skill_names, sd)

        # Feature 5: Mercenary
        merc_rec = select_mercenary(cls, combo_skill_tags, final_score)

        # Feature 6: Nightmare dungeons
        nm_rec = select_nightmare_dungeons(aspects_rec, sd)

        # Feature 7: Specialization detail
        spec_detail = {
            "name": spec["name"],
            "mechanic_type": spec["mechanic_type"],
        }

        # Feature 8: Class-specific specialization recommendations
        class_mechanic: dict = {}
        if cls == "Druid":
            class_mechanic = select_druid_boons(skill_names, sd)
        elif cls == "Necromancer":
            class_mechanic = select_necro_book(mtype, skill_names, sd)

        builds.append({
            "class": cls,
            "specialization": spec["name"],
            "rank": rank,
            "build_score": round(final_score, 6),
            "skill_bar": skill_names,
            "skill_bar_power_names": combo,
            "skill_upgrades": skill_upgrades,
            "passives": passives,
            "key_passive": key_passive,
            "aspects": [],
            "tempers": [],
            "rune_pair_1": pair1,
            "rune_pair_2": pair2,
            "score_breakdown": breakdown,
            # New recommendation fields
            "aspects_recommended":  aspects_rec,
            "tempers_recommended":  tempers_rec,
            "gems_recommended":     gem_rec,
            "gear_recommended":     gear_rec,
            "mercenary":            merc_rec,
            "specialization_detail": spec_detail,
            "class_mechanic":       class_mechanic,
            "nightmare_dungeons":   nm_rec,
        })

    return builds


# ---------------------------------------------------------------------------
# Passive selection
# ---------------------------------------------------------------------------

def select_passives(
    cls: str,
    skill_bar_power_names: list[str],
    sd: ScoringData,
) -> tuple[str | None, list[str]]:
    """
    Pick passives for a build based on tag overlap with the active skill bar.

    Scoring: 1 point per shared tag between passive and any active skill.
    Key passives are selected separately (pick the one with most overlap).

    Returns: (key_passive_display_name | None, [regular_passive_display_names])
    """
    # Collect all tags present in the active skill bar
    active_tags: set[str] = set()
    for pname in skill_bar_power_names:
        skill = sd.skills.get(pname)
        if not skill:
            continue
        for tag in skill["skill_tags"].split(","):
            t = tag.strip()
            if t:
                active_tags.add(t)
        if skill["primary_tag"]:
            active_tags.add(skill["primary_tag"])

    # Prefixes that indicate non-playable / seasonal / item powers
    JUNK_PREFIXES = (
        "s0", "s1",          # past seasons (S01-S10)
        "x1_",               # generic expansion key passives (wrong class context)
        "legendary_", "legendary", "Legendary_",
        "pants_", "ring_", "amulet_", "helm_", "chest_", "boots_",
        "gloves_", "offhand_", "weapon_",
        "paragonglyph_",
        "power_rune_",
    )

    def is_junk_power(pname: str) -> bool:
        low = pname.lower()
        return any(low.startswith(p.lower()) for p in JUNK_PREFIXES)

    # Passives for this class only (no Generic bleed-in for regular passives)
    candidates = [
        s for s in sd.skills.values()
        if s["class"] == cls
        and s["is_passive"]
        and s["display_name"]
        and not s["display_name"].startswith("{c_red}")
        and not s["display_name"].startswith("(PH)")
        and s["display_name"].strip()
        and not is_junk_power(s["power_name"])
    ]

    def is_key_passive(skill: dict) -> bool:
        """Detect key passives: explicit tag, naming convention, or high-tier node."""
        if "Key Passive" in skill["skill_tags"]:
            return True
        pname = skill["power_name"]
        # Paladin uses _KeyPassive_ in power_name
        if "_KeyPassive_" in pname:
            return True
        # Most classes: highest tier = T5 or T3 (Sorcerer)
        if "_T5_" in pname:
            return True
        if skill["class"] == "Sorcerer" and "_T3_" in pname:
            return True
        return False

    key_passive_best: tuple[int, dict] | None = None
    regular: list[tuple[int, dict]] = []

    for p in candidates:
        p_tags: set[str] = set()
        for tag in p["skill_tags"].split(","):
            t = tag.strip()
            if t:
                p_tags.add(t)

        overlap = len(p_tags & active_tags)

        if is_key_passive(p):
            if key_passive_best is None or overlap > key_passive_best[0]:
                key_passive_best = (overlap, p)
        else:
            regular.append((overlap, p))

    regular.sort(key=lambda x: (-x[0], x[1]["display_name"]))

    key_name = key_passive_best[1]["display_name"] if key_passive_best else None
    # Only include passives that have at least 1 overlapping tag
    def looks_like_code(name: str) -> bool:
        """True if display_name is an internal code rather than a real name."""
        if not name:
            return True
        # Internal codes have underscores and no spaces, or match known junk patterns
        if "_" in name and " " not in name:
            return True
        return False

    top_regular = [
        p["display_name"]
        for score, p in regular[:TOP_PASSIVES]
        if not looks_like_code(p["display_name"])
    ]
    return key_name, top_regular


# ---------------------------------------------------------------------------
# Morph / enhanced skill selection
# ---------------------------------------------------------------------------

def pick_morph(base_name: str, sd: ScoringData) -> dict:
    """
    For a base skill name, return its enhanced + chosen morph using skill_morphs.json.
    Morph is chosen by whichever has higher aspect_bonus + temper_bonus.
    Returns dict: {enhanced, morph} (morph may be None for ultimates).
    """
    entry = _MORPHS.get(base_name)
    if not entry:
        return {"enhanced": None, "morph": None}

    enhanced = entry.get("enhanced")
    m1 = entry.get("morph_1")
    m2 = entry.get("morph_2")

    if not m1:
        return {"enhanced": enhanced, "morph": None}
    if not m2:
        return {"enhanced": enhanced, "morph": m1}

    # Score morphs by aspect+temper bonus on their display name
    def morph_score(name: str) -> float:
        if not name:
            return 0.0
        low = name.lower()
        return sd.aspect_bonuses.get(low, 0.0) + sum(
            v for k, v in sd.temper_bonuses.items()
            if low in k.lower() or k.lower() in low
        )

    chosen = m1 if morph_score(m1) >= morph_score(m2) else m2
    return {"enhanced": enhanced, "morph": chosen}


def build_skill_upgrades(skill_bar: list[str], sd: ScoringData) -> list[dict]:
    """
    For each skill in the bar, return {name, enhanced, morph} dicts.
    """
    result = []
    for name in skill_bar:
        upgrades = pick_morph(name, sd)
        result.append({
            "name":     name,
            "enhanced": upgrades["enhanced"],
            "morph":    upgrades["morph"],
        })
    return result


# ---------------------------------------------------------------------------
# Recommendation helpers — pre-built indexes for O(1) lookups
# ---------------------------------------------------------------------------

# Gear slot -> item_type strings that map to it
SLOT_TO_ITEM_TYPES: dict[str, list[str]] = {
    "Helm":    ["Helm"],
    "Chest":   ["ChestArmor"],
    "Gloves":  ["Gloves"],
    "Boots":   ["Boots"],
    "Legs":    ["Legs"],
    "Ring":    ["Ring"],
    "Amulet":  ["Amulet"],
    "Weapon":  [
        "Axe", "Axe2H", "Bow", "Crossbow2H", "Dagger", "Flail",
        "Glaive", "Mace", "Mace2H", "Polearm", "Quarterstaff",
        "Scythe", "Scythe2H", "Staff", "Sword", "Sword2H", "Wand",
    ],
    "Offhand": ["Focus", "FocusBookOffHand", "OffHandTotem", "Shield"],
}

# Aspect output slot order
ASPECT_SLOTS = ["Helm", "Chest", "Gloves", "Boots", "Legs", "Ring1", "Ring2", "Amulet", "Weapon"]

# Strip D4 markup tags from display_name for text matching
_MARKUP_RE = re.compile(r'\{[^}]*\}|\[.*?\]')

def _strip_markup(text: str) -> str:
    return _MARKUP_RE.sub('', text).lower()


def _build_affix_display_index(affix_rows: list[tuple]) -> dict[str, tuple]:
    """
    Build {internal_name -> (display_name_clean, max_value, category)}.
    Used for O(1) lookup when scoring items.
    """
    idx: dict[str, tuple] = {}
    for internal_name, display_name, max_value, category in affix_rows:
        clean = _strip_markup(display_name) if display_name else ""
        idx[internal_name] = (clean, float(max_value) if max_value else 0.0, category)
    return idx


# ---------------------------------------------------------------------------
# Feature 1: Aspect recommendations
# ---------------------------------------------------------------------------

_CLASS_ASPECT_PREFIX: dict[str, str] = {
    "Barbarian":   "legendary_barb_",
    "Druid":       "legendary_druid_",
    "Necromancer": "legendary_necro_",
    "Paladin":     "legendary_paladin_",
    "Rogue":       "legendary_rogue_",
    "Sorcerer":    "legendary_sorc_",
    "Spiritborn":  "legendary_spiritborn_",
    "Warlock":     "legendary_warlock_",
}


def select_aspects(cls: str, build_skill_names: list[str], sd: ScoringData) -> list[dict]:
    """
    Use Maxroll JSON tag index to find legendary aspects linked to active skills.
    Falls back to display_name substring matching restricted to class-specific aspects.
    Outputs short clean aspect names (not description text).
    Returns list of {slot, aspect_name, max_value}.
    """
    skill_name_lower = [s.lower() for s in build_skill_names if len(s) > 3]
    if not skill_name_lower:
        return []

    cls_prefix = _CLASS_ASPECT_PREFIX.get(cls, "legendary_")

    candidates: list[tuple] = []  # (match_score, max_value, display_name, internal_name)
    seen_internal: set[str] = set()

    # Build a quick lookup: affix internal_name (lowercase) -> (display_name, max_value)
    # Only legendary_ affixes — their display_name is a short aspect name, not description text
    affix_lookup: dict[str, tuple] = {}
    for internal_name, display_name, max_value, _cat in sd.affix_rows:
        if internal_name and internal_name.lower().startswith("legendary_"):
            affix_lookup[internal_name.lower()] = (display_name, float(max_value) if max_value else 0.0)

    if _ASPECT_TAG_INDEX:
        # Primary path: tag-based matching via Maxroll FILTER_Flex_ tags
        # Restrict to this class's aspects (or generic "legendary_generic_" aspects)
        generic_prefix = "legendary_generic_"
        for iname_lower, skill_words_list in _ASPECT_TAG_INDEX.items():
            if iname_lower in seen_internal or iname_lower not in affix_lookup:
                continue
            # Only match class-specific or generic aspects
            if not (iname_lower.startswith(cls_prefix) or iname_lower.startswith(generic_prefix)):
                continue
            display_name, mv = affix_lookup[iname_lower]
            if not display_name:
                continue
            # Count how many active skill names match any tag's words
            score = 0
            for tag_words in skill_words_list:
                if any(sname in tag_words or tag_words in sname for sname in skill_name_lower):
                    score += 1
            if score > 0:
                candidates.append((score + 1, mv, display_name, iname_lower))  # +1 to beat fallback
                seen_internal.add(iname_lower)

    # Supplement (or sole path if no tag index): substring match on display_name for class-specific
    # legendary_ affixes. Display names are short aspect names — no description noise.
    if len(candidates) < 6:
        for internal_name, display_name, max_value, _cat in sd.affix_rows:
            if not display_name or internal_name in seen_internal:
                continue
            # Restrict to this class's legendary aspects (avoids cross-class contamination)
            if not internal_name.lower().startswith(cls_prefix):
                continue
            # Skip dev/placeholder entries
            if any(marker in display_name for marker in ("(DNS)", "(DO NOT SHIP)", "(PH)", "DO NOT SHIP")):
                continue
            clean = _strip_markup(display_name)
            if any(sname in clean for sname in skill_name_lower):
                candidates.append((1, float(max_value) if max_value else 0.0, display_name, internal_name.lower()))
                seen_internal.add(internal_name)

    # Sort by match score desc, then max_value desc
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    top6 = candidates[:6]

    result = []
    for i, (_, mv, disp, _iname) in enumerate(top6):
        slot = ASPECT_SLOTS[i] if i < len(ASPECT_SLOTS) else f"Slot{i+1}"
        clean_disp = _MARKUP_RE.sub('', disp).strip()
        result.append({
            "slot": slot,
            "aspect_name": clean_disp,
            "max_value": mv,
        })
    return result


# ---------------------------------------------------------------------------
# Feature 1b: Nightmare dungeon recommendations
# ---------------------------------------------------------------------------

def select_nightmare_dungeons(aspects_rec: list[dict], sd: ScoringData) -> list[dict]:
    """
    For each recommended aspect, look up the nightmare dungeon that drops it.
    Uses the dungeon index (aspect internal_name → dungeon).
    Returns up to 5 dungeons with name, zone, region, and d4planner.io x/y coords.
    """
    if not _DUNGEON_INDEX or not aspects_rec:
        return []

    # Build a reverse lookup: display_name (lower) -> internal_name
    # from the affix table (already loaded in sd.affix_rows)
    disp_to_internal: dict[str, str] = {}
    for internal_name, display_name, _mv, _cat in sd.affix_rows:
        if display_name and internal_name.lower().startswith("legendary_"):
            disp_to_internal[display_name.lower()] = internal_name.lower()

    dungeons: list[dict] = []
    seen_names: set[str] = set()

    for asp in aspects_rec:
        asp_name = (asp.get("aspect_name") or "").lower()
        internal = disp_to_internal.get(asp_name)
        if not internal:
            continue
        dungeon = _DUNGEON_INDEX.get(internal)
        if not dungeon or dungeon["name"] in seen_names:
            continue
        seen_names.add(dungeon["name"])
        dungeons.append({
            "name":    dungeon["name"],
            "zone":    dungeon["zone"],
            "region":  dungeon["region"],
            "aspect":  asp.get("aspect_name"),
            "x":       round(dungeon["x"], 1),
            "y":       round(dungeon["y"], 1),
        })
        if len(dungeons) >= 5:
            break

    return dungeons


# ---------------------------------------------------------------------------
# Feature 2: Temper recommendations
# ---------------------------------------------------------------------------

def select_tempers(cls: str, build_skill_names: list[str], sd: ScoringData) -> dict:
    """
    For each tempering category (Offensive, Defensive, Weapons, Mobility):
    - Filter recipes matching the build class (or null/generic class)
    - Among those, prefer recipes whose affix_key JSON contains skill name fragments
    - Pick top recipe per category by max_value
    Returns {offensive, defensive, weapon, mobility} with recipe display_name values.
    """
    skill_lower = [s.lower() for s in build_skill_names if len(s) > 3]
    target_categories = {
        "offensive": "Offensive",
        "defensive": "Defensive",
        "weapon":    "Weapons",
        "mobility":  "Mobility",
    }
    best: dict[str, tuple] = {}  # output_key -> (score, max_value, display_name)

    for (recipe_name, display_name, recipe_cls, item_type_csv,
         affix_key_json, max_value, category) in sd.temper_rows:
        # Class filter: recipe class must match build class or be null/empty
        if recipe_cls and recipe_cls != cls:
            continue
        if not category:
            continue
        # Exclude retired/unreleased recipes
        if display_name and ("(Legacy)" in display_name or "(DNS)" in display_name):
            continue

        # Find which output key this category maps to
        out_key = None
        for k, cat in target_categories.items():
            if category.strip().lower() == cat.lower():
                out_key = k
                break
        if out_key is None:
            continue

        # Score: count skill name fragments in affix_key JSON
        # Use word-boundary matching to avoid false positives
        # (e.g. "blast" should not match "blasting explosives")
        skill_match_score = 0
        if affix_key_json and skill_lower:
            try:
                keys = json.loads(affix_key_json)
                if isinstance(keys, list):
                    # Tempered keys look like "Tempered_Size_Skill_Barb_Whirlwind_Tier3"
                    # Extract the segment after "_Skill_<Class>_" — that's the skill name
                    affix_skill_segs: set[str] = set()
                    for key in keys:
                        parts = key.split("_")
                        try:
                            si = parts.index("Skill") + 1
                            if si + 1 < len(parts):
                                # parts[si] is class abbreviation, parts[si+1] is skill name
                                affix_skill_segs.add(parts[si + 1].lower())
                        except (ValueError, IndexError):
                            continue
                    # Now match exact-segment to compressed skill name
                    for sname in skill_lower:
                        compact = sname.replace(" ", "").replace("'", "")
                        if compact in affix_skill_segs:
                            skill_match_score += 1
            except (json.JSONDecodeError, TypeError):
                pass

        mv = float(max_value) if max_value else 0.0
        current = best.get(out_key)
        # Prefer higher skill match score, then higher max_value
        if current is None or (skill_match_score, mv) > (current[0], current[1]):
            best[out_key] = (skill_match_score, mv, display_name or recipe_name)

    return {
        "offensive": best["offensive"][2] if "offensive" in best else None,
        "defensive": best["defensive"][2] if "defensive" in best else None,
        "weapon":    best["weapon"][2]    if "weapon"    in best else None,
        "mobility":  best["mobility"][2]  if "mobility"  in best else None,
    }


# ---------------------------------------------------------------------------
# Feature 3: Gem recommendations
# ---------------------------------------------------------------------------

def select_gems(build_score: float, cls: str, sd: ScoringData) -> dict:
    """
    Deterministic gem recommendations by role. Royal quality preferred.
    Returns {weapon: display_name, armor: display_name, jewelry: display_name}.
    """
    gl = sd.gem_lookup
    quality = "royal"

    def gem_name(gem_type: str) -> str | None:
        row = gl.get(gem_type, {}).get(quality)
        if row:
            return row["display_name"]
        # Fallback to grand
        row = gl.get(gem_type, {}).get("grand")
        return row["display_name"] if row else None

    # Weapon: Emerald (Vulnerable damage) for most builds
    # Ruby = overpower damage (Barbarian high-life builds)
    weapon_gem = gem_name("ruby") if cls == "Barbarian" else gem_name("emerald")

    # Armor: Ruby = max life (survivability)
    armor_gem = gem_name("ruby")

    # Jewelry: Skull = all resistance (generic), Diamond = all stats
    # Diamond preferred for builds where all stats matter more
    jewelry_gem = gem_name("skull")

    return {
        "weapon":  weapon_gem,
        "armor":   armor_gem,
        "jewelry": jewelry_gem,
    }


# ---------------------------------------------------------------------------
# Feature 4: BIS gear recommendations
# ---------------------------------------------------------------------------

def select_gear(cls: str, build_skill_names: list[str], sd: ScoringData) -> dict:
    """
    For each gear slot, find the best unique item:
    - usable_by_class is empty (all classes) or contains build class
    - item_type matches slot
    - scored by count of item affix display_names containing any active skill name
    Returns {Helm: display_name, Chest: ..., etc}.

    Uses a pre-built affix display index to avoid O(n²) per item.
    """
    skill_lower = [s.lower() for s in build_skill_names if len(s) > 3]

    # Build internal_name -> clean_display lookup from affix_rows (O(n) once)
    affix_disp: dict[str, str] = {}
    for internal_name, display_name, _mv, _cat in sd.affix_rows:
        if display_name:
            affix_disp[internal_name] = _strip_markup(display_name)

    # Build slot -> item_type set for quick membership test
    item_type_to_slot: dict[str, str] = {}
    for slot, types in SLOT_TO_ITEM_TYPES.items():
        for itype in types:
            item_type_to_slot[itype] = slot

    # Score all items, collect best per slot
    best_per_slot: dict[str, tuple] = {}  # slot -> (score, display_name)

    for item_name, display_name, item_type, usable_by_class, affixes_json in sd.item_rows:
        # Class filter
        if usable_by_class:
            if cls not in usable_by_class.split(","):
                continue

        slot = item_type_to_slot.get(item_type)
        if slot is None:
            continue

        # Score: count affixes whose display_name contains any skill name
        score = 0
        if affixes_json and skill_lower:
            try:
                affix_list = json.loads(affixes_json)
                for iname in affix_list:
                    clean = affix_disp.get(iname, "")
                    if clean and any(sname in clean for sname in skill_lower):
                        score += 1
            except (json.JSONDecodeError, TypeError):
                pass

        current = best_per_slot.get(slot)
        if current is None or score > current[0]:
            best_per_slot[slot] = (score, display_name)

    return {slot: info[1] for slot, info in best_per_slot.items()}


# ---------------------------------------------------------------------------
# Feature 5a: Druid Spirit Boon selection
# ---------------------------------------------------------------------------

# Boons organized by animal spirit (4 spirits × 4 boons each)
_DRUID_BOONS: dict[str, list[dict]] = {
    "Deer": [
        {"name": "Prickleskin",       "tags": ["thorns", "armor"],           "offense": 0, "defense": 2},
        {"name": "Gift of the Stag",  "tags": ["spirit", "resource"],        "offense": 0, "defense": 0, "resource": 3},
        {"name": "Wariness",          "tags": ["elite", "damage_reduction"], "offense": 0, "defense": 2},
        {"name": "Advantageous Beast", "tags": ["cc_reduction"],             "offense": 0, "defense": 1},
    ],
    "Eagle": [
        {"name": "Scythe Talons",   "tags": ["critical"],         "offense": 3, "defense": 0},
        {"name": "Iron Feather",    "tags": ["life", "survival"],  "offense": 0, "defense": 3},
        {"name": "Swooping Attacks", "tags": ["attack_speed"],     "offense": 2, "defense": 0},
        {"name": "Avian Wrath",     "tags": ["critical", "damage"], "offense": 4, "defense": 0},
    ],
    "Wolf": [
        {"name": "Pack Leader",  "tags": ["companion", "cooldown"], "offense": 1, "defense": 0, "companion": 3},
        {"name": "Energize",     "tags": ["spirit", "resource"],    "offense": 0, "defense": 0, "resource": 2},
        {"name": "Bolster",      "tags": ["fortify", "defensive"],  "offense": 0, "defense": 2},
        {"name": "Calamity",     "tags": ["ultimate", "duration"],  "offense": 2, "defense": 0, "ultimate": 3},
    ],
    "Snake": [
        {"name": "Obsidian Slam",          "tags": ["earth", "overpower"],   "offense": 3, "defense": 0},
        {"name": "Overload",               "tags": ["nature", "lightning"],  "offense": 2, "defense": 0},
        {"name": "Masochistic",            "tags": ["shapeshifting", "heal"], "offense": 0, "defense": 2},
        {"name": "Calm Before the Storm",  "tags": ["nature", "ultimate", "cooldown"], "offense": 1, "defense": 0, "ultimate": 2},
    ],
}


def select_druid_boons(build_skill_names: list[str], sd: ScoringData) -> dict:
    """
    Pick optimal 4+1 spirit boons for a Druid build.
    Score each boon by relevance to active skill tags, then pick best per spirit.
    Bond with the spirit whose top-2 boons outscore all others.
    Returns: {boons: [{spirit, name}], bonded_spirit: str}
    """
    # Collect tags from active skills
    active_tags: set[str] = set()
    for name in build_skill_names:
        for pname, skill in sd.skills.items():
            if skill["display_name"] == name:
                for t in skill["skill_tags"].split(","):
                    active_tags.add(t.strip().lower())
                break

    has_companion = any("companion" in t for t in active_tags)
    has_earth = any("earth" in t for t in active_tags)
    has_nature = any("nature" in t for t in active_tags)
    has_shapeshifting = any("shapeshifting" in t for t in active_tags)
    has_ultimate = True  # builds always have an ultimate

    def score_boon(boon: dict) -> float:
        s = boon.get("offense", 0) * 2.0 + boon.get("defense", 0) * 1.0
        if has_companion and "companion" in boon["tags"]:
            s += boon.get("companion", 0) * 2.0
        if has_earth and "earth" in boon["tags"]:
            s += 3.0
        if has_nature and "nature" in boon["tags"]:
            s += 2.0
        if has_shapeshifting and "shapeshifting" in boon["tags"]:
            s += 2.0
        if has_ultimate and "ultimate" in boon["tags"]:
            s += boon.get("ultimate", 0) * 1.5
        s += boon.get("resource", 0) * 1.0
        return s

    # Pick best boon from each spirit
    picks: dict[str, tuple[str, float]] = {}  # spirit -> (boon_name, score)
    spirit_scores: dict[str, list[tuple[float, str]]] = {}

    for spirit, boons in _DRUID_BOONS.items():
        scored = [(score_boon(b), b["name"]) for b in boons]
        scored.sort(key=lambda x: -x[0])
        spirit_scores[spirit] = scored
        picks[spirit] = (scored[0][1], scored[0][0])

    # Bond with spirit whose top-2 boons have highest combined score
    best_bond = max(
        spirit_scores.keys(),
        key=lambda sp: spirit_scores[sp][0][0] + spirit_scores[sp][1][0],
    )

    result_boons = []
    for spirit in ["Deer", "Eagle", "Wolf", "Snake"]:
        result_boons.append({"spirit": spirit, "name": picks[spirit][0]})

    # Bonded spirit gets a 2nd boon
    second_boon = spirit_scores[best_bond][1][1]
    result_boons.append({"spirit": best_bond, "name": second_boon})

    return {"boons": result_boons, "bonded_spirit": best_bond}


# ---------------------------------------------------------------------------
# Feature 5b: Necromancer Book of the Dead selection
# ---------------------------------------------------------------------------

_NECRO_SACRIFICE_BONUSES: dict[str, list[dict]] = {
    "Warriors": [
        {"subtype": "Skirmishers", "bonus": "+10% Critical Hit Chance",    "offense": 3},
        {"subtype": "Defenders",   "bonus": "+25% Resistance to All",      "offense": 0},
        {"subtype": "Reapers",     "bonus": "+25% Shadow Damage",          "offense": 2},
    ],
    "Mages": [
        {"subtype": "Shadow",  "bonus": "+20% Essence Regen, +20 Max Essence", "offense": 1},
        {"subtype": "Cold",    "bonus": "+20% Vulnerable Damage",               "offense": 3},
        {"subtype": "Bone",    "bonus": "+30% Overpower Damage",                "offense": 2},
    ],
    "Golem": [
        {"subtype": "Bone",  "bonus": "+15% Attack Speed",          "offense": 2},
        {"subtype": "Blood", "bonus": "+20% Maximum Life",          "offense": 0},
        {"subtype": "Iron",  "bonus": "+35% Critical Strike Damage", "offense": 4},
    ],
}


def select_necro_book(mtype: str, build_skill_names: list[str], sd: ScoringData) -> dict:
    """
    Pick Book of the Dead choices for a Necromancer build.
    For sacrifice strategy: pick highest offense sacrifice bonus per minion type.
    For summon strategy: pick minion subtypes that complement the build.
    Returns: {strategy, warriors, mages, golem} with choice details.
    """
    if mtype == "book_sacrifice":
        choices = {}
        for category, options in _NECRO_SACRIFICE_BONUSES.items():
            best = max(options, key=lambda o: o["offense"])
            choices[category.lower()] = {
                "action": "sacrifice",
                "subtype": best["subtype"],
                "bonus": best["bonus"],
            }
        return {"strategy": "Full Sacrifice", **choices}
    else:
        # Summon: pick minion types that provide utility
        # Check if build has corpse/bone skills for synergy
        skill_lower = " ".join(build_skill_names).lower()
        has_bone = "bone" in skill_lower or "corpse" in skill_lower
        has_shadow = "shadow" in skill_lower or "blight" in skill_lower

        warriors = "Reapers" if has_shadow else "Skirmishers"
        mages = "Bone" if has_bone else ("Shadow" if has_shadow else "Cold")
        golem = "Bone" if has_bone else "Blood"

        return {
            "strategy": "Full Summon",
            "warriors": {"action": "summon", "subtype": warriors},
            "mages":    {"action": "summon", "subtype": mages},
            "golem":    {"action": "summon", "subtype": golem},
        }


# ---------------------------------------------------------------------------
# Feature 5c: Mercenary recommendation
# ---------------------------------------------------------------------------

MERCENARY_SYNERGIES = {
    "Aldkin": {
        "description": "CursedChild — Haunt, Field of Languish, Terrify. Best for Vulnerable builds.",
        "skills": ["Haunt", "Field of Languish", "Chain of Souls", "Flame Surge",
                   "Storm of Fire", "Wave of Flame", "Terrify"],
    },
    "Raheir": {
        "description": "ShieldBearer — Shield Charge, Provoke, Bastion. Universal tank support.",
        "skills": ["Shield Charge", "Ground Slam", "Provoke", "Crater", "Bastion", "Shield Throw"],
    },
    "Varyana": {
        "description": "BerserkerCrone — Cleave, Whirlwind, Shockwave. Best for Barbarian Overpower.",
        "skills": ["Cleave", "Shockwave", "Bloodthirst", "Whirlwind", "Earth Breaker", "Ancient Harpoons"],
    },
    "Subo": {
        "description": "BountyHunter — Wire Trap, Molotov, Trip Mines. Best for Rogue/ranged.",
        "skills": ["Wire Trap", "Molotov", "Cover Fire", "Trip Mines", "Snipe", "Explosive Charge"],
    },
}


def select_mercenary(cls: str, skill_tags: list[str], build_score: float) -> dict:
    """
    Score mercenaries based on build synergy. Returns {primary, reason}.
    skill_tags: combined skill_tags strings from all active skills.
    """
    scores: dict[str, int] = {m: 0 for m in MERCENARY_SYNERGIES}

    # Aldkin: +3 if build has Vulnerable keyword skills
    combined_tags = " ".join(skill_tags)
    if "Keyword_Vulnerable" in combined_tags or "Vulnerable" in combined_tags:
        scores["Aldkin"] += 3

    # Varyana: +2 for Barbarian (Overpower synergy)
    if cls == "Barbarian":
        scores["Varyana"] += 2

    # Raheir: +1 if high cooldown skills (build_score > 300) or universal default
    if build_score > 300:
        scores["Raheir"] += 1
    scores["Raheir"] += 1  # universal default

    # Subo: +2 for Rogue
    if cls == "Rogue":
        scores["Subo"] += 2

    best_merc = max(scores, key=lambda m: scores[m])
    reason = MERCENARY_SYNERGIES[best_merc]["description"]

    return {"primary": best_merc, "reason": reason}


# ---------------------------------------------------------------------------
# Output DB
# ---------------------------------------------------------------------------

def init_output_db(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS optimizer_results")
    conn.execute("""
        CREATE TABLE optimizer_results (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            class                TEXT,
            specialization       TEXT,
            rank                 INTEGER,
            build_score          REAL,
            skill_bar            TEXT,
            skill_upgrades       TEXT,
            passives             TEXT,
            key_passive          TEXT,
            aspects              TEXT,
            tempers              TEXT,
            rune_pair_1          TEXT,
            rune_pair_2          TEXT,
            score_breakdown      TEXT,
            aspects_recommended  TEXT,
            tempers_recommended  TEXT,
            gems_recommended     TEXT,
            gear_recommended     TEXT,
            mercenary            TEXT,
            specialization_detail TEXT,
            class_mechanic       TEXT,
            nightmare_dungeons   TEXT,
            global_rank          INTEGER,
            tier                 TEXT
        )
    """)
    conn.commit()


def write_builds(conn: sqlite3.Connection, builds: list[dict]) -> None:
    for b in builds:
        conn.execute(
            "INSERT INTO optimizer_results "
            "(class, specialization, rank, build_score, skill_bar, skill_upgrades, passives, key_passive, "
            "aspects, tempers, rune_pair_1, rune_pair_2, score_breakdown, "
            "aspects_recommended, tempers_recommended, gems_recommended, gear_recommended, "
            "mercenary, specialization_detail, class_mechanic, nightmare_dungeons) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                b["class"],
                b["specialization"],
                b["rank"],
                b["build_score"],
                json.dumps(b["skill_bar"]),
                json.dumps(b["skill_upgrades"]),
                json.dumps(b["passives"]),
                b["key_passive"],
                json.dumps(b["aspects"]),
                json.dumps(b["tempers"]),
                json.dumps(b["rune_pair_1"]),
                json.dumps(b["rune_pair_2"]),
                json.dumps(b["score_breakdown"]),
                json.dumps(b.get("aspects_recommended", [])),
                json.dumps(b.get("tempers_recommended", {})),
                json.dumps(b.get("gems_recommended", {})),
                json.dumps(b.get("gear_recommended", {})),
                json.dumps(b.get("mercenary", {})),
                json.dumps(b.get("specialization_detail", {})),
                json.dumps(b.get("class_mechanic", {})),
                json.dumps(b.get("nightmare_dungeons", {})),
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Classes where each specialization row is a SEPARATE optimizer run
# (mutually exclusive specs that change skill weighting).
# Other classes run once per mechanic_type (Barbarian Arsenal, Sorcerer Enchantments).
_INDIVIDUAL_SPEC_CLASSES: dict[str, dict[str, str]] = {
    # class -> {spec_name_lower: synthetic_mechanic_type}
    "Paladin": {
        "disciple":   "oaths_disciple",
        "judicator":  "oaths_judicator",
        "juggernaut": "oaths_juggernaut",
        "zealot":     "oaths_zealot",
    },
    "Spiritborn": {
        "centipede (primary)": "guardian_centipede",
        "eagle (primary)":     "guardian_eagle",
        "gorilla (primary)":   "guardian_gorilla",
        "jaguar (primary)":    "guardian_jaguar",
    },
}

# Necromancer runs two macro-strategies instead of 18 individual rows
_NECRO_STRATEGIES: list[dict] = [
    {
        "class": "Necromancer",
        "name": "Sacrifice All",
        "mechanic_type": "book_sacrifice",
        "required_skill_tags": "",
        "generator_tags": "",
        "spender_tags": "",
    },
    {
        "class": "Necromancer",
        "name": "Summon All",
        "mechanic_type": "book_summon",
        "required_skill_tags": "",
        "generator_tags": "",
        "spender_tags": "",
    },
]


def pick_specs_per_class(specs: list[dict]) -> dict[str, list[dict]]:
    """
    Return {class: [spec_dicts]} for optimizer runs.

    - Classes in _INDIVIDUAL_SPEC_CLASSES get one run per named spec.
    - Necromancer gets two synthetic runs (summon vs sacrifice).
    - Other classes get one run per unique mechanic_type (first row wins).
    """
    by_class: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}

    for spec in specs:
        cls = spec["class"]
        name_lower = spec["name"].lower()

        # Paladin / Spiritborn: each named spec is its own run
        if cls in _INDIVIDUAL_SPEC_CLASSES:
            mapping = _INDIVIDUAL_SPEC_CLASSES[cls]
            if name_lower in mapping:
                synthetic_mtype = mapping[name_lower]
                seen.setdefault(cls, set())
                if synthetic_mtype not in seen[cls]:
                    run_spec = dict(spec)
                    run_spec["mechanic_type"] = synthetic_mtype
                    by_class.setdefault(cls, []).append(run_spec)
                    seen[cls].add(synthetic_mtype)
            continue

        # Necromancer: skip individual book_of_the_dead rows (handled below)
        if cls == "Necromancer":
            continue

        # Default: one run per mechanic_type
        mtype = spec["mechanic_type"]
        seen.setdefault(cls, set())
        if mtype not in seen[cls]:
            by_class.setdefault(cls, []).append(spec)
            seen[cls].add(mtype)

    # Inject Necromancer macro-strategies
    by_class["Necromancer"] = list(_NECRO_STRATEGIES)

    return by_class


def main() -> None:
    if not DB_IN.exists():
        raise FileNotFoundError(f"Source DB not found: {DB_IN}")

    print(f"Reading from: {DB_IN}")
    conn = sqlite3.connect(str(DB_IN))

    sd = ScoringData(
        skills         = load_skills(conn),
        damage_map     = load_skill_damage(conn),
        cooldowns      = load_cooldowns(conn),
        aspect_bonuses = load_aspect_bonuses(conn),
        temper_bonuses = load_temper_bonuses(conn),
        affix_rows     = load_affixes(conn),
        temper_rows    = load_temper_rows(conn),
        item_rows      = load_item_rows(conn),
        gem_lookup     = load_gem_lookup(conn),
    )
    runes     = load_runes(conn)
    all_specs = load_specializations(conn)
    conn.close()

    print(
        f"Loaded {len(sd.skills)} skills, "
        f"{len(sd.damage_map)} with damage data, "
        f"{len(sd.cooldowns)} cooldowns, "
        f"{sum(1 for v in sd.aspect_bonuses.values() if v > 0)} skills with aspect links, "
        f"{sum(1 for v in sd.temper_bonuses.values() if v > 0)} skills with temper bonuses, "
        f"{len(runes)} runes, "
        f"{len(all_specs)} specialization rows, "
        f"{len(sd.affix_rows)} affixes, "
        f"{len(sd.temper_rows)} temper recipes, "
        f"{len(sd.item_rows)} unique items"
    )

    specs_by_class = pick_specs_per_class(all_specs)
    classes = sorted(specs_by_class.keys())
    print(f"Classes to optimize: {', '.join(classes)}\n")


    out_conn = sqlite3.connect(str(DB_OUT))
    init_output_db(out_conn)

    all_top_builds: list[dict] = []
    total_rows = 0

    for cls in classes:
        cls_specs = specs_by_class[cls]
        cls_top: dict[str, dict] = {}  # mechanic_type -> best build

        for spec in cls_specs:
            mtype = spec["mechanic_type"]
            print(f"  [{cls}] spec={spec['name']} ({mtype})")
            builds = optimize_class_spec(cls, spec, sd, runes)
            if builds:
                write_builds(out_conn, builds)
                total_rows += len(builds)
                if mtype not in cls_top or builds[0]["build_score"] > cls_top[mtype]["build_score"]:
                    cls_top[mtype] = builds[0]
                print(f"    -> {len(builds)} builds, top score: {builds[0]['build_score']:.4f}")
            else:
                print("    -> No valid builds found.")

        if cls_top:
            best = max(cls_top.values(), key=lambda b: b["build_score"])
            all_top_builds.append(best)

    # Compute global ranks and tiers
    all_rows = out_conn.execute(
        "SELECT id, build_score FROM optimizer_results ORDER BY build_score DESC"
    ).fetchall()
    total = len(all_rows)
    for i, (row_id, _score) in enumerate(all_rows):
        grank = i + 1
        pct = grank / total
        if pct <= 0.10:
            tier = "S"
        elif pct <= 0.30:
            tier = "A"
        elif pct <= 0.60:
            tier = "B"
        else:
            tier = "C"
        out_conn.execute(
            "UPDATE optimizer_results SET global_rank = ?, tier = ? WHERE id = ?",
            (grank, tier, row_id),
        )
    out_conn.commit()
    out_conn.close()

    # Summary
    print("\n" + "=" * 65)
    print("TOP BUILD PER CLASS")
    print("=" * 65)
    for b in all_top_builds:
        print(f"\n{b['class']}  [{b['specialization']}]")
        print(f"  Score : {b['build_score']:.4f}")
        print(f"  Skills: {', '.join(b['skill_bar'])}")
        breakdown = b["score_breakdown"]
        top_skill = max(breakdown, key=breakdown.get) if breakdown else None
        if top_skill:
            print(f"  MVP   : {top_skill} ({breakdown[top_skill]:.2f})")
        if b["rune_pair_1"]:
            p1 = b["rune_pair_1"]
            p2 = b["rune_pair_2"]
            pair1_str = f"[{p1['ritual']} + {p1['invocation']} score={p1['score']:.3f}]"
            pair2_str = (f"[{p2['ritual']} + {p2['invocation']} score={p2['score']:.3f}]"
                         if p2 else "[none]")
            print(f"  Runes : {pair1_str}  {pair2_str}")

    print(f"\nTotal rows written to optimizer_results.db: {total_rows}")
    print(f"Results at: {DB_OUT}")


if __name__ == "__main__":
    main()
