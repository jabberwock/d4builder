#!/usr/bin/env python3
"""
Diablo 4 Build Optimizer v2 — Co-optimized scoring

Evaluates all C(n, 6) skill combinations per class per spec.
Each combo is scored with its best aspects, tempers, and paragon as one unit.
Outputs top 3 builds per class (best across all specs).

Scoring model (max-roll assumption):
  For each skill in the 6-skill bar:
    base       = damage_coefficient * SkillRankBonus[7] * 100
    cd_factor  = 1 / max(cooldown, 1.0)
    spec_mult  = spec-specific multiplier (e.g., Combo Points per-skill)
    aspect_mult = 1.0 + sum(max_value/100 for linked aspects)
    temper_mult = 1.0 + sum(max_value/100 for linked tempers)
    skill_score = base * cd_factor * spec_mult * aspect_mult * temper_mult

  synergy_bonus = reward school-tag overlap between skills
  paragon_bonus = sum of best 5 board legendary node scores
  rune_bonus    = top 2 runeword pair scores
  build_score   = sum(skill_scores) * (1 + synergy_bonus) + paragon_bonus + rune_bonus
"""

import itertools
import json
import os
import re
import sqlite3
import struct
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

# Import recommendation functions from v1 optimizer
from optimizer import (
    ScoringData as V1ScoringData,
    load_affixes, load_temper_rows, load_item_rows, load_gem_lookup,
    select_tempers, select_gems, select_gear,
    select_mercenary, select_nightmare_dungeons,
)

DB_IN      = Path(__file__).parent / "d4_stats.db"
DB_OUT     = Path(__file__).parent / "optimizer_results.db"
MORPHS_PATH = Path(__file__).parent / "skill_morphs.json"
MAXROLL_PATH = (lambda: __import__("_maxroll").MAXROLL_PATH)()
DUNGEONS_PATH = Path(__file__).parent / "nightmare_dungeons.json"
POWERS_DIR = Path(__file__).parent / "../temp/powers"
SKILL_METADATA_PATH = Path(__file__).parent / "skill_metadata.json"

TOP_N_PER_CLASS = 3
SKILL_RANK = 5  # active skills cap at rank 5
PASSIVE_RANK = 3  # passive skills cap at rank 3
SKILL_RANK_BONUS = [1.0, 1.0, 1.10, 1.20, 1.30, 1.40, 1.50, 1.60, 1.70, 1.80]
RANK_MULT = SKILL_RANK_BONUS[SKILL_RANK]  # 1.40 at rank 5

_MORPHS: dict = json.load(open(MORPHS_PATH)) if MORPHS_PATH.exists() else {}

# Skill metadata: hit counts, roles, durations, multiplicative bonuses
_SKILL_META: dict = {}
if SKILL_METADATA_PATH.exists():
    with open(SKILL_METADATA_PATH) as f:
        _meta_raw = json.load(f)
        # Index by display name (lowercased) for lookup
        _SKILL_META = {m["name"].lower(): m for m in _meta_raw.values() if m.get("name")}

# Authoritative keyword values extracted from skill_tags_data
KEYWORD_VALUES_PATH = Path(__file__).parent / "keyword_values.json"
_KEYWORD_VALUES: dict = {}
if KEYWORD_VALUES_PATH.exists():
    with open(KEYWORD_VALUES_PATH) as f:
        _KEYWORD_VALUES = json.load(f)

# Passive skill effects: authoritative table built from d4data SF values
# + maxroll desc semantic context. Falls back to legacy regex extraction.
PASSIVE_TABLE_PATH = Path(__file__).parent / "passive_table.json"
PASSIVE_EFFECTS_PATH = Path(__file__).parent / "passive_effects.json"
_PASSIVE_EFFECTS: dict = {}
if PASSIVE_TABLE_PATH.exists():
    with open(PASSIVE_TABLE_PATH) as f:
        _passive_table = json.load(f)
    # Convert to the same format as the legacy passive_effects.json:
    # { power_name: { name, extracted_effects: [{tag, value, type}] } }
    for sid, p in _passive_table.items():
        _PASSIVE_EFFECTS[sid] = {
            "name": p.get("name", sid),
            "extracted_effects": [
                {"tag": e["tag"], "value": e["value"], "type": e.get("type", "")}
                for e in p.get("effects", [])
                if e["tag"] != "unknown"
            ],
        }
elif PASSIVE_EFFECTS_PATH.exists():
    with open(PASSIVE_EFFECTS_PATH) as f:
        _PASSIVE_EFFECTS = json.load(f)

# Structured glyph metadata extracted from maxroll paragonGlyphs
GLYPH_DATA_PATH = Path(__file__).parent / "glyph_data.json"
_GLYPH_DATA: dict = {}
if GLYPH_DATA_PATH.exists():
    with open(GLYPH_DATA_PATH) as f:
        _GLYPH_DATA = json.load(f)
    # Build name → data lookup (display_name lowercased)
    _GLYPH_DATA_BY_NAME = {g["name"].lower(): g for g in _GLYPH_DATA.values() if g.get("name")}
else:
    _GLYPH_DATA_BY_NAME = {}


# ─── School tags for synergy scoring ─────────────────────────────────────────

SCHOOL_TAGS: frozenset[str] = frozenset([
    'Skill_Fire', 'Skill_Lightning', 'Skill_Cold',
    'Skill_Blood', 'Skill_Bone', 'Skill_Corruption',
    'Skill_Marksman', 'Skill_Cutthroat', 'Skill_Trap', 'Skill_Shadow',
    'Skill_Bludgeoning', 'Skill_Channeled', 'Skill_Bleeding', 'Skill_Slam',
    'Skill_Spirit_Soil', 'Skill_Spirit_Sky', 'Skill_Spirit_Forest', 'Skill_Spirit_Plains',
    'Skill_Disciple', 'Skill_Divine', 'Skill_Juggernaut', 'Skill_Zealot',
    'Skill_Physical', 'Skill_Primary_Summoning',
])


# ─── Data loading ────────────────────────────────────────────────────────────

@dataclass
class SkillInfo:
    power_name: str
    display_name: str
    cls: str
    is_passive: bool
    skill_tags: str
    primary_tag: str
    damage_coeff: float       # from game data .pow files
    cooldown: float
    resource_cost: int        # mana/fury/energy cost (0 = free/generator)
    lucky_hit: float          # lucky hit chance 0-100
    school_tags: frozenset[str]
    aspect_bonus: float       # sum of max_value/100 for linked aspects
    temper_bonus: float       # sum of max_value/100 for linked tempers
    is_generator: bool = False
    is_defensive: bool = False
    is_cc: bool = False
    is_aoe: bool = False
    is_ultimate: bool = False
    # Extended metadata from descriptions
    role: str = "instant"          # instant/dot/summon/conjuration/buff/channel
    hit_count: int = 1             # number of damage instances per cast
    duration_sec: float = 0.0      # for summons/DoTs
    max_stacks: int = 1             # max simultaneously active (summons)
    mult_bonuses: list = field(default_factory=list)  # [{kind, value_pct, target}]
    utility_effects: list = field(default_factory=list)  # [{tag, value}] from descriptions
    applies_states: frozenset = frozenset()  # states this skill applies (vulnerable, frozen, etc.)


def load_active_skills(conn: sqlite3.Connection) -> dict[str, SkillInfo]:
    """Load all active (non-passive) skills with damage, cooldown, aspect, temper data."""
    skill_rows = conn.execute(
        "SELECT power_name, display_name, class, is_passive, skill_tags, primary_tag "
        "FROM skills WHERE is_passive = 0 AND display_name IS NOT NULL "
        "AND display_name != '' "
        "AND display_name NOT LIKE '%%{c_red}%%' AND display_name NOT LIKE '%%(DNS)%%' "
        # Filter out _Override and Evade variants — they're alt forms, not real bar skills
        "AND power_name NOT LIKE '%%_Override%%' "
        "AND skill_tags NOT LIKE '%%Evade_Power%%'"
    ).fetchall()

    # Damage coefficients: prefer d4data extraction (authoritative SF resolution),
    # fall back to skill_damage table (rank 5 from build_db)
    damage_rows = conn.execute(
        "SELECT power_name, damage_pct FROM skill_damage WHERE rank = ?", (SKILL_RANK,)
    ).fetchall()
    damage_map = {r[0]: float(r[1]) for r in damage_rows}

    # Layer in authoritative d4data coefficients (resolved from .pow JSON SFs)
    d4data_path = Path(__file__).parent / "d4data_coefficients.json"
    d4data_hits: dict[str, int] = {}
    d4data_per_hit_coeff: dict[str, float] = {}
    if d4data_path.exists():
        with open(d4data_path) as f:
            d4data_coeffs = json.load(f)
        for pname, info in d4data_coeffs.items():
            coeff = info.get("coefficient", 0)
            hits = info.get("hit_count", 1)
            if coeff > 0:
                # Convert per-hit coefficient to damage_pct at rank 5
                damage_map[pname] = coeff * 100.0 * RANK_MULT
                d4data_hits[pname] = hits
                d4data_per_hit_coeff[pname] = coeff

    # Cooldowns: prefer d4data extraction (308 cooldowns vs 50 in DB)
    cd_rows = conn.execute(
        "SELECT power_name, cooldown_base FROM skill_cooldowns"
    ).fetchall()
    cd_map = {r[0]: float(r[1]) if r[1] else 0.0 for r in cd_rows}

    # Layer in authoritative d4data cooldowns
    d4data_cd_path = Path(__file__).parent / "d4data_cooldowns.json"
    if d4data_cd_path.exists():
        with open(d4data_cd_path) as f:
            d4data_cds = json.load(f)
        for pname, cd in d4data_cds.items():
            if cd > 0:
                cd_map[pname] = float(cd)

    # Aspect bonuses (pre-computed per skill display_name)
    aspect_bonus = _compute_aspect_bonuses(conn)

    # Temper bonuses (pre-computed per skill power_name)
    temper_bonus = _compute_temper_bonuses(conn)

    # Resource costs + lucky hit from Maxroll
    resource_costs, lucky_hits = _load_maxroll_skill_meta()

    skills = {}
    for pname, dname, cls, is_passive, stags, ptag in skill_rows:
        if not cls or cls == "Generic":
            continue
        # Clean placeholder prefixes from display names
        if dname and dname.startswith("(PH) "):
            dname = dname[5:]
        tags_list = [t.strip() for t in (stags or "").split(",") if t.strip()]
        school = frozenset(t for t in tags_list if t in SCHOOL_TAGS)
        dmg = damage_map.get(pname, 0.0)
        cd = cd_map.get(pname, 0.0)
        tags_str = stags or ""
        ptag_str = ptag or ""

        # Classify skill role
        is_gen = "Basic" in ptag_str
        is_def = ("Defensive" in ptag_str or "Keyword_Barrier" in tags_str
                  or "Keyword_Fortify" in tags_str or "Keyword_Immune" in tags_str
                  or "Subterfuge" in ptag_str  # Rogue defensive category
                  or "Search_DamageReduction" in tags_str
                  or "Skill_Aura" in tags_str  # Paladin auras provide defense
                  )
        is_cc = ("CrowdControl" in tags_str or "Search_CrowdControl" in tags_str
                 or "Keyword_Vulnerable" in tags_str
                 or "Keyword_Freeze" in tags_str or "Keyword_Daze" in tags_str
                 or "Keyword_Stun" in tags_str)
        is_aoe = ("AoE" in tags_str or "Skill_Channeled" in tags_str
                  or any(kw in (dname or "").lower() for kw in
                         ["storm", "wave", "rain", "explosion", "nova", "blizzard",
                          "hurricane", "tornado", "whirlwind", "maelstrom"]))
        is_ult = "Ultimate" in ptag_str

        # Load extended metadata from descriptions
        meta = _SKILL_META.get((dname or "").lower(), {})
        role = meta.get("role", "instant")
        hit_count = meta.get("hit_count", 1)
        # Override hit count from d4data extraction (counts actual damage payloads)
        if pname in d4data_hits:
            hit_count = max(hit_count, d4data_hits[pname])
        duration_sec = meta.get("duration_sec", 0.0)
        max_stacks = meta.get("max_stacks", 1)
        mult_bonuses = meta.get("mult_bonuses", [])
        utility_effects = meta.get("utility_effects", [])

        # Detect what states this skill applies (vulnerable, frozen, etc.)
        applies = set()
        if "Keyword_Vulnerable" in tags_str:
            applies.add("vulnerable")
        if "Keyword_Freeze" in tags_str:
            applies.add("frozen")
        if "Keyword_Chill" in tags_str:
            applies.add("chilled")
        if "Keyword_Stun" in tags_str:
            applies.add("stunned")
        if "Keyword_Daze" in tags_str:
            applies.add("dazed")
        if "Keyword_Weaken" in tags_str:
            applies.add("weakened")
        if "Keyword_Burning" in tags_str or "Skill_Fire" in tags_str:
            applies.add("burning")
        # Also pull state applications from description-extracted effects
        for eff in utility_effects:
            tag = eff.get("tag", "")
            if tag == "applies_vulnerable":
                applies.add("vulnerable")
            elif tag == "applies_freeze":
                applies.add("frozen")
            elif tag == "applies_chill":
                applies.add("chilled")
            elif tag == "applies_stun":
                applies.add("stunned")
            elif tag == "applies_daze":
                applies.add("dazed")
            elif tag == "applies_weaken":
                applies.add("weakened")
            elif tag == "applies_slow":
                applies.add("slowed")

        skills[pname] = SkillInfo(
            power_name=pname,
            display_name=dname or pname,
            cls=cls,
            is_passive=bool(is_passive),
            skill_tags=tags_str,
            primary_tag=ptag_str,
            damage_coeff=dmg,
            cooldown=cd,
            resource_cost=resource_costs.get(dname, 0),
            lucky_hit=lucky_hits.get(dname, 0.0),
            school_tags=school,
            aspect_bonus=aspect_bonus.get(dname.lower() if dname else "", 0.0),
            temper_bonus=temper_bonus.get(pname, 0.0),
            role=role,
            hit_count=hit_count,
            duration_sec=duration_sec,
            max_stacks=max_stacks,
            mult_bonuses=mult_bonuses,
            utility_effects=utility_effects,
            applies_states=frozenset(applies),
            is_generator=is_gen,
            is_defensive=is_def,
            is_cc=is_cc,
            is_aoe=is_aoe,
            is_ultimate=is_ult,
        )
    return skills


def _load_maxroll_skill_meta() -> tuple[dict[str, int], dict[str, float]]:
    """Load resource costs and lucky hit chances from Maxroll JSON."""
    if not MAXROLL_PATH.exists():
        return {}, {}
    with open(MAXROLL_PATH) as f:
        mdata = json.load(f)
    skills_mx = mdata.get("skills") or {}
    if isinstance(skills_mx, list):
        skills_mx = {str(i): s for i, s in enumerate(skills_mx)}

    costs: dict[str, int] = {}
    lucky_hits: dict[str, float] = {}
    for sid, s in skills_mx.items():
        name = s.get("name") or ""
        if not name:
            continue
        for c in (s.get("cost") or []):
            if isinstance(c, dict):
                cv = c.get("cost", 0)
                if isinstance(cv, (int, float)) and cv > 0:
                    costs[name] = int(cv)
                    break
        lh = s.get("combatEffectChance")
        if isinstance(lh, (int, float)) and lh > 0:
            lucky_hits[name] = float(lh)
    return costs, lucky_hits


ASPECT_SKILL_MAP_PATH = Path(__file__).parent / "aspect_skill_map.json"
ASPECT_SLOTS = ["Helm", "Chest", "Gloves", "Boots", "Legs", "Ring1", "Ring2", "Amulet", "Weapon"]
_MARKUP_RE = re.compile(r'\{[^}]*\}|\[.*?\]')

_CLASS_ASPECT_PREFIX: dict[str, str] = {
    "Barbarian": "legendary_barb_", "Druid": "legendary_druid_",
    "Necromancer": "legendary_necro_", "Paladin": "legendary_paladin_",
    "Rogue": "legendary_rogue_", "Sorcerer": "legendary_sorc_",
    "Spiritborn": "legendary_spiritborn_", "Warlock": "legendary_warlock_",
}


def select_aspects(
    cls: str, build_skill_names: list[str], affix_rows: list[tuple],
) -> list[dict]:
    """
    Recommend aspects for a build using description-based skill mapping.
    Returns list of {slot, aspect_name, max_value}.
    """
    if not ASPECT_SKILL_MAP_PATH.exists():
        return []

    with open(ASPECT_SKILL_MAP_PATH) as f:
        aspect_skill_map = json.load(f)

    skill_lower = set(s.lower() for s in build_skill_names if len(s) > 3)
    cls_prefix = _CLASS_ASPECT_PREFIX.get(cls, "legendary_")
    generic_prefix = "legendary_generic_"

    # Build affix lookup: internal_name -> (display_name, max_value)
    affix_lookup: dict[str, tuple[str, float]] = {}
    for internal_name, display_name, max_value, _cat in affix_rows:
        if internal_name and display_name:
            affix_lookup[internal_name.lower()] = (display_name, float(max_value) if max_value else 0.0)

    candidates: list[tuple[int, float, str, str]] = []  # (match_count, max_value, display_name, iname)

    for iname_lower, linked_skills in aspect_skill_map.items():
        # Only this class's aspects or generic
        if not (iname_lower.startswith(cls_prefix) or iname_lower.startswith(generic_prefix)):
            continue

        lookup = affix_lookup.get(iname_lower)
        if not lookup:
            continue
        display_name, mv = lookup

        if not display_name or any(m in display_name for m in ("(DNS)", "(DO NOT SHIP)", "(PH)")):
            continue

        # Count how many build skills this aspect supports
        match_count = sum(1 for s in linked_skills if s in skill_lower)
        if match_count > 0:
            candidates.append((match_count, mv, display_name, iname_lower))

    candidates.sort(key=lambda x: (-x[0], -x[1]))
    top = candidates[:len(ASPECT_SLOTS)]

    result = []
    for i, (_, mv, disp, _iname) in enumerate(top):
        clean_disp = _MARKUP_RE.sub('', disp).strip()
        result.append({
            "slot": ASPECT_SLOTS[i] if i < len(ASPECT_SLOTS) else f"Slot{i+1}",
            "aspect_name": clean_disp,
            "max_value": mv,
        })
    return result

def _compute_aspect_bonuses(conn: sqlite3.Connection) -> dict[str, float]:
    """
    Compute aspect bonus per skill using description-based mapping.
    Uses aspect_skill_map.json (built from Maxroll descriptions) to link
    legendary aspects to skills. Sums max_value/100 for all aspects that
    mention each skill.
    """
    # Load the description-based aspect-to-skill map
    if ASPECT_SKILL_MAP_PATH.exists():
        with open(ASPECT_SKILL_MAP_PATH) as f:
            aspect_skill_map = json.load(f)
    else:
        aspect_skill_map = {}

    # Load affix max_values from DB
    affix_rows = conn.execute(
        "SELECT internal_name, display_name, max_value FROM affixes "
        "WHERE max_value IS NOT NULL AND max_value > 0"
    ).fetchall()
    affix_mv: dict[str, float] = {}
    for iname, _disp, mv in affix_rows:
        if iname:
            affix_mv[iname.lower()] = float(mv)

    # Sum bonuses per skill name
    bonus: dict[str, float] = {}
    for iname_lower, skill_list in aspect_skill_map.items():
        mv = affix_mv.get(iname_lower, 0.0)
        if mv <= 0:
            continue
        for sname in skill_list:
            bonus[sname] = bonus.get(sname, 0.0) + mv / 100.0

    return bonus


def _compute_temper_bonuses(conn: sqlite3.Connection) -> dict[str, float]:
    """Sum max_value/100 for temper recipes matching each skill."""
    rows = conn.execute(
        "SELECT recipe_name, affix_key, max_value FROM tempering_recipes "
        "WHERE affix_key IS NOT NULL AND max_value IS NOT NULL"
    ).fetchall()

    temper_entries: list[tuple[str, float]] = []
    for _recipe, affix_key_json, mv in rows:
        try:
            keys = json.loads(affix_key_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(keys, list):
            continue
        for key in keys:
            parts = key.split("_")
            try:
                skill_idx = parts.index("Skill") + 1
                if skill_idx + 1 < len(parts):
                    seg = parts[skill_idx + 1].lower()
                    temper_entries.append((seg, float(mv)))
            except (ValueError, IndexError):
                continue

    skill_rows = conn.execute("SELECT power_name FROM skills").fetchall()
    bonus: dict[str, float] = {}
    for (pname,) in skill_rows:
        parts = pname.split("_", 1)
        short = parts[1].lower() if len(parts) > 1 else pname.lower()
        total = 0.0
        for seg, mv in temper_entries:
            if seg == short or short.startswith(seg) or seg.startswith(short):
                total += mv / 100.0
        if total > 0.0:
            bonus[pname] = total
    return bonus


def load_paragon_data(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """
    Load paragon legendary nodes with numeric bonuses from .pow files.
    Returns {class: [{name, display_name, tags, bonus_value, formula}]}
    """
    # Load from DB for tags and names
    nodes = conn.execute(
        "SELECT node_name, node_type, display_name, skill_tags "
        "FROM paragon_nodes WHERE node_type = 'legendary'"
    ).fetchall()

    # Parse .pow files for numeric values
    pow_bonuses = _parse_paragon_pow_files()

    result: dict[str, list[dict]] = {}
    for node_name, _ntype, display_name, skill_tags in nodes:
        # Determine class from node_name prefix
        cls = _class_from_paragon_name(node_name)
        if not cls:
            continue

        # Paragon bonus: tag overlap only (numeric .pow values unreliable)
        bonus = 0.0

        result.setdefault(cls, []).append({
            "name": node_name,
            "display_name": display_name or node_name,
            "tags": skill_tags or "",
            "bonus_value": bonus,
        })
    return result


def _class_from_paragon_name(name: str) -> str | None:
    prefixes = {
        "Barbarian": "Barbarian", "Druid": "Druid", "Necromancer": "Necromancer",
        "Paladin": "Paladin", "Rogue": "Rogue", "Sorcerer": "Sorcerer",
        "Spiritborn": "Spiritborn", "Warlock": "Warlock",
    }
    for prefix, cls in prefixes.items():
        if name.startswith(prefix):
            return cls
    return None


def _parse_paragon_pow_files() -> dict[str, float]:
    """Extract the best numeric coefficient from each Paragon .pow file."""
    if not POWERS_DIR.exists():
        return {}
    bonuses = {}
    for fname in os.listdir(POWERS_DIR):
        if not fname.startswith("Paragon_") or not fname.endswith(".pow"):
            continue
        path = os.path.join(POWERS_DIR, fname)
        with open(path, "rb") as f:
            data = f.read()
        strs = _extract_strings(data)

        # Find the largest numeric literal that looks like a multiplier
        best = 0.0
        for s in strs:
            try:
                v = float(s)
                if 0.01 < v < 100 and v > best:
                    best = v
            except ValueError:
                continue

        key = fname.replace(".pow", "")
        # Map pow filename to DB node_name
        # Paragon_Barb_Legendary_002 -> Barbarian_Legendary_002
        key = key.replace("Paragon_Barb_", "Barbarian_")
        key = key.replace("Paragon_Druid_", "Druid_")
        key = key.replace("Paragon_Necro_", "Necromancer_")
        key = key.replace("Paragon_Paladin_", "Paladin_")
        key = key.replace("Paragon_Rogue_", "Rogue_")
        key = key.replace("Paragon_Sorc_", "Sorcerer_")
        key = key.replace("Paragon_Spiritborn_", "Spiritborn_")
        if best > 0:
            bonuses[key] = best
    return bonuses


def _extract_strings(data: bytes) -> list[str]:
    strs = []
    current = b""
    for byte in data:
        if 32 <= byte < 127:
            current += bytes([byte])
        else:
            if len(current) >= 2:
                strs.append(current.decode("ascii"))
            current = b""
    return strs


def load_glyphs(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Load paragon glyphs per class."""
    rows = conn.execute(
        "SELECT glyph_name, display_name, usable_by_class, bonus_per_point, radius_bonus "
        "FROM paragon_glyphs"
    ).fetchall()
    result: dict[str, list[dict]] = {}
    for gname, dname, cls, bpp, rb in rows:
        if not cls:
            continue
        bonus = (float(bpp) if bpp else 0.0) + (float(rb) if rb else 0.0)
        result.setdefault(cls, []).append({
            "name": gname,
            "display_name": dname or gname,
            "bonus": bonus,
        })
    return result


def load_runes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT rune_name, display_name, rune_type, offering_gain, offering_cost "
        "FROM runes WHERE rune_name NOT LIKE 'test_%%'"
    ).fetchall()
    return [
        {
            "display_name": dname or rname,
            "rune_type": rtype or "",
            "offering_gain": float(gain) if gain else None,
            "offering_cost": float(cost) if cost else None,
        }
        for rname, dname, rtype, gain, cost in rows
    ]


def load_specializations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT class, specialization_name, mechanic_type, description, "
        "required_skill_tags, generator_tags, spender_tags "
        "FROM specializations"
    ).fetchall()
    return [
        {
            "class": cls, "name": name, "mechanic_type": mtype or "",
            "description": desc or "",
            "required_skill_tags": req or "", "generator_tags": gen or "",
            "spender_tags": spend or "",
        }
        for cls, name, mtype, desc, req, gen, spend in rows
    ]


# ─── Spec multipliers ────────────────────────────────────────────────────────

def compute_spec_multipliers(
    cls: str, mtype: str, pool: list[SkillInfo], cooldowns: dict[str, float],
) -> dict[str, float]:
    """Return {power_name: multiplier} for spec-adjusted skill scoring."""
    mults: dict[str, float] = {}

    if cls == "Rogue" and mtype == "combo_points":
        CP = {
            "Rogue_Barrage": 1.60, "Rogue_RapidFire": 1.39,
            "Rogue_PenetratingShot": 1.90, "Rogue_Flurry": 1.75,
            "Rogue_TwistingBlades": 1.90,
        }
        for s in pool:
            if "Skill_Primary_Core" in s.primary_tag:
                mults[s.power_name] = CP.get(s.power_name, 1.39)
    elif cls == "Rogue" and mtype == "inner_sight":
        for s in pool:
            if "Skill_Primary_Core" in s.primary_tag:
                mults[s.power_name] = 1.30
    elif cls == "Rogue" and mtype == "preparation":
        for s in pool:
            if "Skill_Primary_Ultimate" in s.primary_tag:
                mults[s.power_name] = 1.50
            elif s.cooldown > 0 and ("Defensive" in s.primary_tag or "Weapon_Mastery" in s.primary_tag):
                mults[s.power_name] = 1.25
    elif cls == "Paladin":
        # Paladin oaths give a primary path bonus but allow mixing skills from all paths
        oath_map = {
            "oaths_disciple": "Skill_Disciple", "oaths_judicator": "Skill_Divine",
            "oaths_juggernaut": "Skill_Juggernaut", "oaths_zealot": "Skill_Zealot",
        }
        tag = oath_map.get(mtype)
        if tag:
            for s in pool:
                if tag in s.skill_tags:
                    mults[s.power_name] = 1.25  # oath bonus
                # No penalty for non-primary oath skills
    elif cls == "Spiritborn":
        # Spiritborn picks a primary guardian spirit but uses skills from all 4
        # spirits freely. Primary spirit gets a modest bonus, others stay neutral.
        spirit_map = {
            "guardian_centipede": "Skill_Spirit_Soil", "guardian_eagle": "Skill_Spirit_Sky",
            "guardian_gorilla": "Skill_Spirit_Forest", "guardian_jaguar": "Skill_Spirit_Plains",
        }
        tag = spirit_map.get(mtype)
        if tag:
            for s in pool:
                if tag in s.skill_tags:
                    mults[s.power_name] = 1.20  # primary spirit bonus
                # No penalty for non-primary spirits
    elif cls == "Necromancer" and mtype == "book_sacrifice":
        for s in pool:
            if "Skill_Primary_Core" in s.primary_tag or "Skill_Primary_Ultimate" in s.primary_tag:
                mults[s.power_name] = 1.40
            if "Skill_Primary_Summoning" in s.skill_tags:
                mults[s.power_name] = 0.60
    elif cls == "Necromancer" and mtype == "book_summon":
        for s in pool:
            if "Skill_Primary_Summoning" in s.skill_tags:
                mults[s.power_name] = 1.40

    return mults


# ─── Scoring ──────────────────────────────────────────────────────────────────

# Tag-based utility scoring: score non-damage skills by their mechanical tags.
# Each tag contributes a base value. Skills with multiple utility tags stack.
# This replaces the brittle per-skill-name dictionary.
UTILITY_TAG_VALUES: dict[str, float] = {
    "Keyword_Barrier": 1.0,
    "Keyword_Fortify": 1.0,
    "Keyword_Immune": 1.5,
    "Keyword_Unstoppable": 0.6,
    "Keyword_Vulnerable": 0.8,      # applier utility
    "Keyword_Weaken": 0.7,
    "Keyword_Freeze": 0.8,
    "Keyword_Chill": 0.5,
    "Keyword_Stun": 0.6,
    "Keyword_Daze": 0.5,
    "Keyword_Berserk": 1.0,         # big damage steroid
    "Keyword_Blood_Orb": 0.4,
    "Keyword_Stealth": 0.6,
    "Skill_Mobility": 0.7,
    "Skill_Shout": 0.8,             # party-wide buff
    "Skill_Aura": 0.9,              # persistent party buff
}

# Category tags that mark skill role
CATEGORY_DEFENSIVE = {"Defensive", "Skill_Primary_Defensive"}
CATEGORY_ULTIMATE = {"Skill_Primary_Ultimate"}
CATEGORY_BASIC = {"Basic", "Skill_Primary_Basic"}

# Mechanical synergy chains: if a build has BOTH an applicator and an exploiter,
# the exploiter's damage is effectively multiplied in real gameplay.
# Each chain: (applicator_tag, exploiter_bonus_per_applicator)
# Values sourced from skill_tags_data via keyword_values.json where possible.
def _kw_pct(name: str, field: str = "damage_amp_pct", default: float = 0.10) -> float:
    """Get a keyword's % value from authoritative data, fallback to default."""
    info = _KEYWORD_VALUES.get(name, {})
    val = info.get(field, default * 100)
    return val / 100.0 if isinstance(val, (int, float)) else default

SYNERGY_CHAINS: list[tuple[str, str, float]] = [
    # (tag that applies the effect, tag that exploits it, bonus multiplier)
    ("Keyword_Vulnerable", "Search_Damage", _kw_pct("Keyword_Vulnerable", "damage_amp_pct", 0.20)),
    ("Keyword_Freeze", "Keyword_Chill", 0.15),           # freeze enables shatter
    ("Keyword_Chill", "Keyword_Freeze", 0.15),
    ("Keyword_Stun", "Search_CrowdControl", 0.10),
    ("Keyword_Daze", "Search_CrowdControl", 0.10),
    ("Keyword_Weaken", "Search_Damage", 0.20),           # weaken = -20% enemy damage
    ("Keyword_Barrier", "Keyword_Fortify", 0.10),
    ("Keyword_Berserk", "Search_AttackSpeed", _kw_pct("Keyword_Berserk", "damage_mult_pct", 0.25)),
    ("Keyword_Ignited", "Skill_Fire", _kw_pct("Keyword_Ignited", "damage_mult_pct", 2.0) * 0.2),  # very strong but rare
]

# Elemental tags for coherence scoring
ELEMENT_TAGS: dict[str, str] = {
    "Skill_Fire": "fire", "Skill_Cold": "cold", "Skill_Lightning": "lightning",
    "Skill_Shadow": "shadow", "Skill_Blood": "physical", "Skill_Bone": "physical",
    "Skill_Physical": "physical", "Skill_Bludgeoning": "physical",
    "Skill_Slashing": "physical", "Skill_Bleeding": "physical",
    "Skill_Poison": "poison", "Skill_Corruption": "shadow",
    "Skill_Marksman": "physical", "Skill_Cutthroat": "physical",
    "Search_Physical": "physical", "Search_Shadow": "shadow",
    "Search_Poison": "poison", "Search_Holy": "holy",
}


def score_skill(skill: SkillInfo, spec_mult: float) -> float:
    """
    Score a single skill. Damage skills use coefficient × modifiers.
    Non-damage skills (utility/defensive) scored by their mechanical tags
    so they compete fairly with damage skills.
    """
    base = skill.damage_coeff

    if base <= 0:
        # Utility scoring: combine tag values + description-extracted effects
        tags = set(t.strip() for t in skill.skill_tags.split(",") if t.strip())
        utility = sum(UTILITY_TAG_VALUES.get(t, 0.0) for t in tags)

        # Add description-extracted utility effects (curses, CC, grouping, buffs)
        utility += sum(eff.get("value", 0.0) for eff in skill.utility_effects)

        # Ultimate non-damage skills get a boost (they're powerful by design)
        if skill.is_ultimate:
            utility = max(utility, 1.5)

        # Aura/Shout skills without damage still have high game impact
        if any(t in tags for t in ("Skill_Aura", "Skill_Shout")):
            utility = max(utility, 0.8)

        # Defensive skills always have baseline value
        if skill.is_defensive:
            utility = max(utility, 0.6)

        # Curses always have meaningful value (damage amplification)
        if "Curse" in skill.primary_tag:
            utility = max(utility, 1.5)

        if utility <= 0:
            return 0.0

        # Scale utility to be comparable with damage scores.
        # Damage skills typically score 200-15000. A "1.0 utility" skill should
        # score roughly equivalent to a weak damage skill (~200-400).
        # Cap utility skills at ~50% of typical damage skill score so they
        # complement but don't dominate damage skills.
        UTILITY_SCALE = 250.0

        aspect_mult = 1.0 + skill.aspect_bonus
        temper_mult = 1.0 + skill.temper_bonus
        return utility * UTILITY_SCALE * spec_mult * aspect_mult * temper_mult

    # Damage skill scoring
    cd_factor = 1.0 / max(skill.cooldown, 1.0) if skill.cooldown > 0 else 1.0
    lh_bonus = 1.0 + (skill.lucky_hit / 200.0)
    aspect_mult = 1.0 + skill.aspect_bonus
    temper_mult = 1.0 + skill.temper_bonus

    # Multi-hit multiplier (Ice Shards = 5 shards, Frozen Orb = 3 hits, etc.)
    hit_mult = max(1, skill.hit_count)

    # Summon/conjuration DPS modeling: persistent attackers stack and tick over duration.
    # Effective DPS = base_per_hit × hits_during_uptime × stack_factor × uptime_fraction
    if skill.role in ("summon", "conjuration"):
        TICKS_PER_SEC = 1.0
        duration = skill.duration_sec if skill.duration_sec > 0 else 6.0
        ticks = TICKS_PER_SEC * duration
        # Diminishing stack returns: 1=1.0, 2=1.5, 3=1.8, 4=2.0, 6=2.2
        if skill.max_stacks > 1:
            stack_factor = 1.0 + 0.5 * (1 - 0.6 ** (skill.max_stacks - 1)) / (1 - 0.6)
        else:
            stack_factor = 1.0
        if skill.cooldown > 0:
            uptime = min(1.0, duration / skill.cooldown)
        else:
            uptime = 0.4  # charges + recharge time, ~40% effective uptime
        summon_mult = ticks * stack_factor * uptime
        # Cap at 4x (most builds achieve 2-4x via summons + multipliers)
        summon_mult = min(summon_mult, 4.0)
        hit_mult = max(hit_mult, summon_mult)
        cd_factor = 1.0

    # DoT modeling: ticks over duration, but uptime matters. Without a cooldown,
    # DoTs are limited by mana cost AND can only have N instances active at once.
    elif skill.role == "dot" and skill.duration_sec > 0:
        ticks = skill.duration_sec  # 1 tick/sec
        if skill.cooldown > 0:
            uptime = min(1.0, skill.duration_sec / skill.cooldown)
            dot_mult = ticks * uptime
        else:
            # Spammable DoT — limited by overlap. Assume ~1.5 instances active.
            dot_mult = ticks * 0.25
        # Cap at 3x — DoTs are good but not skill-bar carriers
        dot_mult = min(dot_mult, 3.0)
        hit_mult = max(hit_mult, dot_mult)
        cd_factor = 1.0

    damage_score = base * hit_mult * cd_factor * spec_mult * aspect_mult * temper_mult * lh_bonus

    # Aura skills (Skill_Primary_Aura) get additional value from their passive effects.
    # The active cast deals direct damage but the passive aura is the real value.
    if "Aura" in skill.primary_tag and skill.utility_effects:
        utility = sum(eff.get("value", 0.0) for eff in skill.utility_effects)
        if utility > 0:
            UTILITY_SCALE = 250.0
            damage_score += utility * UTILITY_SCALE * spec_mult * aspect_mult * temper_mult

    return damage_score


def mechanical_synergy(combo: list[SkillInfo]) -> float:
    """
    Score mechanical synergies between skills on the bar.
    Rewards builds where skills enable each other:
    - Vuln applicator + damage dealers = multiplicative damage
    - CC applicator + CC exploiter = crowd control chain
    - Defensive layering = barrier + fortify + immune
    - Elemental coherence = skills sharing element for aspect stacking
    """
    if not combo:
        return 0.0

    # Collect all tags across the bar
    all_tags_per_skill: list[set[str]] = []
    bar_tags: set[str] = set()
    for s in combo:
        tags = set(t.strip() for t in s.skill_tags.split(",") if t.strip())
        all_tags_per_skill.append(tags)
        bar_tags.update(tags)

    bonus = 0.0

    # 1. Synergy chains: applicator + exploiter
    for app_tag, exploit_tag, mult in SYNERGY_CHAINS:
        has_applicator = any(app_tag in tags for tags in all_tags_per_skill)
        if not has_applicator:
            continue
        # Count how many OTHER skills benefit (not the applicator itself)
        exploiter_count = 0
        for tags in all_tags_per_skill:
            if app_tag not in tags and exploit_tag in tags:
                exploiter_count += 1
        # Even skills without the explicit exploit tag benefit from vuln/weaken
        if app_tag in ("Keyword_Vulnerable", "Keyword_Weaken"):
            # All damage skills benefit from vuln/weaken
            damage_skills = sum(1 for s in combo if s.damage_coeff > 0)
            applicator_skills = sum(1 for tags in all_tags_per_skill if app_tag in tags)
            exploiter_count = max(exploiter_count, damage_skills - applicator_skills)
        bonus += exploiter_count * mult

    # 2. Elemental coherence: builds focused on 1-2 elements stack better
    element_counts: dict[str, int] = {}
    for s in combo:
        tags = set(t.strip() for t in s.skill_tags.split(",") if t.strip())
        elements_for_skill: set[str] = set()
        for t in tags:
            if t in ELEMENT_TAGS:
                elements_for_skill.add(ELEMENT_TAGS[t])
        for elem in elements_for_skill:
            element_counts[elem] = element_counts.get(elem, 0) + 1

    if element_counts:
        # Reward concentration: if 4+ skills share an element, big bonus
        max_elem_count = max(element_counts.values())
        unique_elements = len(element_counts)
        if max_elem_count >= 4:
            bonus += 0.30  # strong elemental focus
        elif max_elem_count >= 3:
            bonus += 0.15  # moderate focus
        # Penalize scattered elements (4+ different elements = unfocused)
        if unique_elements >= 4:
            bonus -= 0.10

    # 3. School tag overlap (original logic, reduced weight)
    school_counts: dict[str, int] = {}
    for s in combo:
        for t in s.school_tags:
            school_counts[t] = school_counts.get(t, 0) + 1
    overlap = sum(count - 1 for count in school_counts.values() if count > 1)
    bonus += min(overlap * 0.03, 0.30)

    # 4. Multiplicative bonus exploitation: skills with [x] bonuses against
    # states that other skills on the bar APPLY get a big multiplier.
    # E.g., Ice Shards has 50%[x] vs Frozen + build has Blizzard/Deep Freeze (frozen).
    applied_states: set[str] = set()
    for s in combo:
        applied_states.update(s.applies_states)

    for s in combo:
        for mb in s.mult_bonuses:
            if mb.get("kind") != "vs_state":
                continue
            target = mb.get("target", "")
            value = mb.get("value_pct", 0) / 100.0
            if target in applied_states:
                # The state IS being applied → the [x] bonus is real
                bonus += value * 0.5  # half-weight (assumes ~50% uptime)

    # 5. Damage multiplier ultimates (e.g. Unstable Currents)
    # These ultimates duplicate or boost casts from other skills.
    # Detect by name + role (since the mechanic is too complex to tag-detect).
    for s in combo:
        if s.is_ultimate and s.display_name in DAMAGE_MULT_ULTIMATES:
            cfg = DAMAGE_MULT_ULTIMATES[s.display_name]
            # Count qualifying skills on the bar
            qualifying = sum(
                1 for other in combo
                if other.power_name != s.power_name
                and any(req_tag in other.skill_tags for req_tag in cfg["requires_tag"])
            )
            if qualifying > 0:
                bonus += cfg["bonus_per_skill"] * qualifying

    # 6. Global damage buff skills (War Cry, auras, "increasing your damage dealt")
    # Each one adds a flat bonus to overall build damage.
    global_buff_count = 0
    berserking_count = 0
    aura_offense_count = 0
    aura_defense_count = 0
    for s in combo:
        for eff in s.utility_effects:
            tag = eff.get("tag", "")
            if tag == "global_damage_buff":
                global_buff_count += 1
            elif tag in ("grants_berserking", "grants_berserk"):
                berserking_count += 1
            elif tag in ("aura_attack_speed", "aura_crit"):
                aura_offense_count += 1
            elif tag == "aura_defense":
                aura_defense_count += 1
    if global_buff_count > 0:
        bonus += 0.25 * min(global_buff_count, 3)  # +25% per global buff, max 3
    if berserking_count > 0:
        bonus += 0.20 * min(berserking_count, 2)   # +20% berserking access
    if aura_offense_count > 0:
        bonus += 0.30 * min(aura_offense_count, 2)  # auras stack twice for max
    if aura_defense_count > 0:
        bonus += 0.10 * min(aura_defense_count, 2)

    return bonus


# Damage-multiplier ultimates: these duplicate casts or boost the whole bar
# during their uptime. Each adds a per-qualifying-skill bonus to the build score.
DAMAGE_MULT_ULTIMATES: dict[str, dict] = {
    "Unstable Currents": {
        # Duplicates Core/Conjuration/Mastery Shock skills
        "requires_tag": ["Skill_Lightning"],
        "bonus_per_skill": 0.20,  # ~20% effective damage per qualifying skill
    },
    "Wrath of the Berserker": {
        # Berserk + Unstoppable, doubles damage during uptime
        "requires_tag": ["Skill_Bludgeoning", "Skill_Slashing", "Skill_Physical"],
        "bonus_per_skill": 0.15,
    },
    "Call of the Ancients": {
        # Summons 3 ancients that deal damage independently
        "requires_tag": ["Skill_Bludgeoning", "Skill_Slashing", "Skill_Physical"],
        "bonus_per_skill": 0.10,
    },
    "Petrify": {
        # Stuns all + grants crit damage bonus
        "requires_tag": ["Skill_Nature_Magic"],
        "bonus_per_skill": 0.15,
    },
    "Grizzly Rage": {
        "requires_tag": ["Skill_Shapeshifting", "Skill_Bear"],
        "bonus_per_skill": 0.20,
    },
    "Lacerate": {
        "requires_tag": ["Skill_Werewolf", "Skill_Cutthroat"],
        "bonus_per_skill": 0.15,
    },
}


def survivability_score(combo: list[SkillInfo], purpose_cfg: dict) -> float:
    """
    Score defensive capability of a build.
    Each defensive LAYER multiplies independently (like real D4 defense).
    Multiple different defenses compound; stacking same type has diminishing returns.
    """
    layers: dict[str, float] = {}

    for s in combo:
        tags = set(t.strip() for t in s.skill_tags.split(",") if t.strip())

        if "Keyword_Barrier" in tags:
            layers["barrier"] = layers.get("barrier", 0) + 1
        if "Keyword_Fortify" in tags:
            layers["fortify"] = layers.get("fortify", 0) + 1
        if "Keyword_Immune" in tags:
            layers["immune"] = layers.get("immune", 0) + 1
        if s.is_defensive:
            layers["defensive"] = layers.get("defensive", 0) + 1
        if s.is_cc:
            layers["cc_defense"] = layers.get("cc_defense", 0) + 1
        if "Keyword_Unstoppable" in tags:
            layers["unstoppable"] = layers.get("unstoppable", 0) + 1
        if "Skill_Mobility" in tags:
            layers["mobility"] = layers.get("mobility", 0) + 1

    # Each unique layer type is a multiplier; stacking same type has diminishing value
    LAYER_VALUES = {
        "barrier": 0.12,
        "fortify": 0.12,
        "immune": 0.15,
        "defensive": 0.10,
        "cc_defense": 0.06,
        "unstoppable": 0.05,
        "mobility": 0.04,
    }

    total_mult = 1.0
    for layer_type, count in layers.items():
        base = LAYER_VALUES.get(layer_type, 0.05)
        # First instance of each type is full value, diminishing after
        value = base + base * 0.3 * max(0, count - 1)
        total_mult *= (1.0 + value)

    # No defenses at all: heavy penalty based on purpose
    has_any_defense = bool(layers)
    if not has_any_defense:
        return purpose_cfg.get("no_defensive_penalty", 0.50)

    return total_mult


# Load glyph scoring data
GLYPH_SCORING_PATH = Path(__file__).parent / "glyph_scoring.json"
_GLYPH_SCORING: dict = {}
if GLYPH_SCORING_PATH.exists():
    with open(GLYPH_SCORING_PATH) as _f:
        _GLYPH_SCORING = json.load(_f)

# Map skill tags to glyph damage type tags
_SKILL_TAG_TO_GLYPH_TAG: dict[str, list[str]] = {
    "Skill_Blood": ["physical"],
    "Skill_Bone": ["physical"],
    "Skill_Physical": ["physical"],
    "Skill_Fire": ["fire"],
    "Skill_Cold": ["cold"],
    "Skill_Lightning": ["lightning"],
    "Skill_Shadow": ["shadow"],
    "Skill_Corruption": ["shadow"],
    "Keyword_Vulnerable": ["vulnerable"],
    "Keyword_Overpower": ["overpower"],
    "Skill_Bludgeoning": ["physical"],
    "Skill_Slashing": ["physical"],
    "Skill_Bleeding": ["physical"],
    "Skill_Marksman": ["physical"],
    "Skill_Cutthroat": ["physical"],
    "Skill_Poison": ["poison"],
}


def score_paragon(
    combo: list[SkillInfo],
    boards: list[dict],
    glyphs: list[dict],
) -> tuple[float, list[dict]]:
    """
    Pick best 5 paragon boards and assign glyphs.
    Boards scored by tag overlap with skill bar.
    Glyphs scored by: main stat bonus + damage type match with build.
    """
    if not boards:
        return 0.0, []

    # Collect tags from skill bar
    bar_tags: set[str] = set()
    build_damage_types: set[str] = set()
    build_has_cc = False
    for s in combo:
        for t in s.skill_tags.split(","):
            t = t.strip()
            if t:
                bar_tags.add(t)
                for glyph_tag in _SKILL_TAG_TO_GLYPH_TAG.get(t, []):
                    build_damage_types.add(glyph_tag)
        if s.is_cc:
            build_has_cc = True
            build_damage_types.add("cc")
    build_damage_types.add("all_damage")  # universal always matches

    # Determine class from first skill
    cls = combo[0].cls if combo else ""

    # Score each board by tag overlap
    board_scores = []
    for board in boards:
        btags = set(t.strip() for t in board["tags"].split(",") if t.strip())
        overlap = len(btags & bar_tags)
        score = overlap * 0.5
        board_scores.append((score, board))

    board_scores.sort(key=lambda x: -x[0])
    top5 = board_scores[:5]

    # Build a set of skill class words that the build uses (for glyph matching)
    build_skill_classes: set[str] = set()
    for s in combo:
        ptag = s.primary_tag.lower()
        for kw in ("conjuration", "fury", "companion", "hunter", "mastery",
                   "subterfuge", "imbuement", "cutthroat", "marksman",
                   "trap", "werewolf", "werebear", "earth", "storm",
                   "blood", "bone", "shadow", "summoning", "minion",
                   "core", "basic", "ultimate", "defensive", "potency",
                   "focus", "valor", "justice", "aura"):
            if kw in ptag or kw in s.skill_tags.lower():
                build_skill_classes.add(kw)

    # Score glyphs for this build using structured glyph_data
    def glyph_score(g: dict) -> float:
        # Try the new structured data first
        gname_lower = g["display_name"].lower()
        new_data = _GLYPH_DATA_BY_NAME.get(gname_lower, {})

        if new_data:
            score = 0.5
            # Main stat glyphs are more impactful (every node in radius contributes)
            if new_data.get("main_stats"):
                score += 1.5
            # Skill classes match build's classes
            for sc in new_data.get("skill_classes", []):
                if sc in build_skill_classes:
                    score += 1.0
            # Skill tags (Conjuration, Fury, etc.) match build
            for st in new_data.get("skill_tags", []):
                if st.lower() in build_skill_classes:
                    score += 1.2
            # Element matches build damage types
            for elem in new_data.get("elements", []):
                if elem in build_damage_types:
                    score += 0.8
            # Targets (vulnerable, frozen, etc.) — these are state-based bonuses
            applied_states_set: set[str] = set()
            for s in combo:
                applied_states_set.update(s.applies_states)
            for tgt in new_data.get("targets", []):
                if tgt in applied_states_set:
                    score += 1.0
            # Legendary bonus presence is a strong signal
            if new_data.get("has_legendary_bonus"):
                score += 0.5
            return score

        # Fallback: legacy glyph_scoring.json
        info = _GLYPH_SCORING.get(gname_lower, {})
        if not info or info.get("class") != cls:
            return g["bonus"]
        score = 0.5
        if info.get("main_stat"):
            score += 1.0
        leg_tags = info.get("legendary_tags", [])
        tag_match = len(set(leg_tags) & build_damage_types)
        score += tag_match * 0.8
        return score

    scored_glyphs = sorted(glyphs, key=glyph_score, reverse=True)

    assignments = []
    used_glyphs: set[str] = set()
    total = 0.0

    for board_score, board in top5:
        total += board_score
        glyph_pick = None
        for g in scored_glyphs:
            if g["name"] not in used_glyphs:
                glyph_pick = g
                used_glyphs.add(g["name"])
                total += glyph_score(g)
                break
        assignments.append({
            "board": board["display_name"],
            "glyph": glyph_pick["display_name"] if glyph_pick else None,
        })

    return total, assignments


# Build purposes with their scoring weight profiles
BUILD_PURPOSES = {
    "pit": {
        "label": "Pit Push",
        "description": "Max single-target DPS with survivability for high-tier Pits",
        "no_defensive_penalty": 0.35,   # you WILL die without defense in high pits
        "aoe_weight": 0.3,              # some AoE for trash, but single target is king
        "single_target_weight": 1.0,    # full weight on single target
        "movement_weight": 0.0,
        "resource_no_gen_penalty": 0.65,
        "must_have_defensive": True,
        "survivability_weight": 1.2,    # survival matters a LOT in high pits
    },
    "speed": {
        "label": "Speed Farm",
        "description": "Fast AoE clear for Helltides, Nightmare Dungeons, bounties",
        "no_defensive_penalty": 0.85,   # can get away with less defense
        "aoe_weight": 1.0,              # AoE is the whole point
        "single_target_weight": 0.3,    # bosses matter less
        "movement_weight": 0.25,        # movement skills are huge for speed
        "resource_no_gen_penalty": 0.80,
        "must_have_defensive": False,
        "survivability_weight": 0.6,    # survive enough, don't over-invest
    },
    "leveling": {
        "label": "Leveling",
        "description": "Smooth 1-60 progression, no gear dependency",
        "no_defensive_penalty": 0.40,   # undergeared = need defense
        "aoe_weight": 0.7,              # balanced: need both AoE and single target
        "single_target_weight": 0.7,
        "movement_weight": 0.10,
        "resource_no_gen_penalty": 0.35, # MUST have generator while leveling
        "must_have_defensive": True,
        "must_have_generator": True,
        "survivability_weight": 1.0,
    },
    "mythic": {
        "label": "Mythic Unique",
        "description": "Endgame builds leveraging mythic unique item synergies",
        "no_defensive_penalty": 0.40,
        "aoe_weight": 0.5,
        "single_target_weight": 0.8,
        "movement_weight": 0.05,
        "resource_no_gen_penalty": 0.60,
        "must_have_defensive": True,
        "survivability_weight": 1.0,
    },
}


def is_valid_combo(combo: list[SkillInfo], purpose_cfg: dict) -> bool:
    """
    Fast pre-filter: reject combos that violate hard constraints.
    Called before expensive scoring to prune the search space.
    """
    # No duplicate skills on the bar
    names = set()
    for s in combo:
        if s.display_name in names:
            return False
        names.add(s.display_name)

    # Max 1 ultimate (already checked in outer loop, but belt-and-suspenders)
    if sum(1 for s in combo if s.is_ultimate) > 1:
        return False

    # Must have generator if required by purpose
    if purpose_cfg.get("must_have_generator"):
        if not any(s.is_generator for s in combo):
            return False

    # Must have at least 1 defensive if required
    if purpose_cfg.get("must_have_defensive"):
        has_defense = any(
            s.is_defensive
            or "Keyword_Barrier" in s.skill_tags
            or "Keyword_Fortify" in s.skill_tags
            or "Keyword_Immune" in s.skill_tags
            for s in combo
        )
        if not has_defense:
            return False

    return True


def score_build(
    combo: list[SkillInfo],
    spec_mults: dict[str, float],
    boards: list[dict],
    glyphs: list[dict],
    rune_bonus: float,
    purpose: str = "pit",
) -> tuple[float, dict]:
    """Score a 6-skill build for a specific purpose."""
    cfg = BUILD_PURPOSES[purpose]

    # ── Individual skill scores ──
    breakdown: dict[str, float] = {}
    skill_total = 0.0
    for s in combo:
        sm = spec_mults.get(s.power_name, 1.0)
        ss = score_skill(s, sm)
        breakdown[s.display_name] = round(ss, 4)
        skill_total += ss

    # ── Mechanical synergy (replaces old tag-overlap synergy) ──
    syn = mechanical_synergy(combo)

    # ── Survivability (replaces old binary model) ──
    surv = survivability_score(combo, cfg)
    surv_weight = cfg.get("survivability_weight", 1.0)
    # Blend: survivability multiplies the score, weighted by purpose
    surv_factor = 1.0 + (surv - 1.0) * surv_weight

    # ── AoE vs single target balance ──
    aoe_count = sum(1 for s in combo if s.is_aoe)
    damage_count = sum(1 for s in combo if s.damage_coeff > 0)
    st_count = damage_count - aoe_count

    aoe_w = cfg.get("aoe_weight", 0.5)
    st_w = cfg.get("single_target_weight", 0.5)
    if damage_count > 0:
        aoe_ratio = aoe_count / damage_count
        st_ratio = st_count / damage_count
        # Score how well the AoE/ST mix matches the purpose
        mix_score = 1.0 + (aoe_ratio * aoe_w + st_ratio * st_w - 0.5) * 0.20
    else:
        mix_score = 1.0

    # ── Resource sustain ──
    has_generator = any(s.is_generator for s in combo)
    total_cost = sum(s.resource_cost for s in combo)

    if has_generator:
        resource_factor = 1.0
    elif total_cost == 0:
        resource_factor = 1.0
    else:
        resource_factor = cfg["resource_no_gen_penalty"]

    # ── Movement bonus (speed builds) ──
    movement_bonus = 1.0
    move_w = cfg.get("movement_weight", 0.0)
    if move_w > 0:
        move_count = sum(1 for s in combo
                         if "Skill_Mobility" in s.skill_tags
                         or "Keyword_Unstoppable" in s.skill_tags)
        movement_bonus += move_count * move_w

    # ── Paragon ──
    paragon_total, _paragon_detail = score_paragon(combo, boards, glyphs)

    # ── Final score ──
    total = (skill_total
             * (1.0 + syn)
             * surv_factor
             * mix_score
             * resource_factor
             * movement_bonus
             + paragon_total
             + rune_bonus)

    return total, breakdown


# ─── Rune scoring (reused from v1) ───────────────────────────────────────────

INVOCATION_VALUE = {
    'Tec': 3.0, 'Kry': 3.0, 'Tal': 3.0, 'Ton': 3.0, 'Yom': 3.0, 'Tzic': 3.0,
    'Tun': 2.5, 'Thul': 2.5, 'Wat': 2.5,
    'Ohm': 2.0, 'Lac': 2.0, 'Vex': 2.0, 'Xal': 2.0, 'Qax': 2.0, 'Xan': 2.0,
    'Mot': 2.0, 'Ner': 2.0,
    'Eom': 1.5, 'Zec': 1.5, 'Lum': 1.5, 'Ceh': 1.5,
    'Jah': 1.0, 'Qua': 1.0,
}


def best_rune_pairs(runes: list[dict]) -> tuple[dict | None, dict | None, float]:
    rituals = [r for r in runes if r["rune_type"] == "ritual"]
    invocations = [r for r in runes if r["rune_type"] == "invocation"]
    if not rituals or not invocations:
        return None, None, 0.0

    pairs = []
    for r in rituals:
        for inv in invocations:
            if not r["offering_gain"] or not inv["offering_cost"]:
                continue
            proc = r["offering_gain"] / inv["offering_cost"]
            val = INVOCATION_VALUE.get(inv["display_name"], 1.0)
            pairs.append((proc * val, r, inv))
    pairs.sort(key=lambda x: -x[0])

    chosen = []
    used: set[str] = set()
    for score, r, inv in pairs:
        if r["display_name"] not in used and inv["display_name"] not in used:
            chosen.append((score, r, inv))
            used.add(r["display_name"])
            used.add(inv["display_name"])
        if len(chosen) == 2:
            break

    def fmt(score, r, inv):
        return {"ritual": r["display_name"], "invocation": inv["display_name"],
                "score": round(score, 6)}

    p1 = fmt(*chosen[0]) if chosen else None
    p2 = fmt(*chosen[1]) if len(chosen) > 1 else None
    total = sum(c[0] for c in chosen)
    return p1, p2, total


# ─── Spec routing ─────────────────────────────────────────────────────────────

def get_specs_for_class(cls: str, all_specs: list[dict]) -> list[tuple[str, str]]:
    """Return [(spec_name, synthetic_mechanic_type)] for optimizer runs."""
    INDIVIDUAL = {
        "Paladin": {
            "disciple": "oaths_disciple", "judicator": "oaths_judicator",
            "juggernaut": "oaths_juggernaut", "zealot": "oaths_zealot",
        },
        "Spiritborn": {
            "centipede (primary)": "guardian_centipede", "eagle (primary)": "guardian_eagle",
            "gorilla (primary)": "guardian_gorilla", "jaguar (primary)": "guardian_jaguar",
        },
    }

    if cls == "Necromancer":
        return [("Sacrifice All", "book_sacrifice"), ("Summon All", "book_summon")]

    if cls in INDIVIDUAL:
        mapping = INDIVIDUAL[cls]
        result = []
        for spec in all_specs:
            if spec["class"] != cls:
                continue
            key = spec["name"].lower()
            if key in mapping:
                result.append((spec["name"], mapping[key]))
        return result

    if cls == "Rogue":
        return [
            ("Combo Points", "combo_points"),
            ("Inner Sight", "inner_sight"),
            ("Preparation", "preparation"),
        ]

    # Default: one run with first spec's mechanic_type
    for spec in all_specs:
        if spec["class"] == cls:
            return [(spec["name"], spec["mechanic_type"])]
    return [("Default", "")]


# ─── Skill tree prerequisite graph ─────────────────────────────────────────

_SKILL_TREE_GRAPH: dict[str, dict] = {}  # class -> {name_to_indices, adj, gateways, idx_info}

# Map optimizer class names to maxroll skillTree keys
_TREE_KEY_MAP = {
    "Barbarian": "Barbarian", "Druid": "Druid", "Necromancer": "Necromancer",
    "Paladin": "Paladin_NEW", "Rogue": "Rogue", "Sorcerer": "Sorcerer",
    "Spiritborn": "Spiritborn",
}


def _load_skill_tree_graph():
    """Build passive prerequisite graphs from maxroll skillTrees data."""
    global _SKILL_TREE_GRAPH
    if _SKILL_TREE_GRAPH:
        return
    if not MAXROLL_PATH.exists():
        return
    with open(MAXROLL_PATH) as f:
        mdata = json.load(f)
    skill_trees = mdata.get("skillTrees", {})
    skills_mx = mdata.get("skills", {})
    if isinstance(skills_mx, list):
        skills_mx = {str(i): s for i, s in enumerate(skills_mx)}

    # power_id -> display name
    power_to_name = {}
    for sid, skill in skills_mx.items():
        name = skill.get("name", "")
        if name:
            power_to_name[sid] = name

    for cls_name, tree_key in _TREE_KEY_MAP.items():
        tree = skill_trees.get(tree_key)
        if not tree:
            continue
        nodes = tree["nodes"]
        connections = tree["connections"]

        # Build index -> info (connections use array indices, not node.id)
        idx_info = {}
        name_to_indices: dict[str, list[int]] = defaultdict(list)
        for i, n in enumerate(nodes):
            power = n.get("reward", {}).get("power", "")
            name = power_to_name.get(power, power)
            ntype = n.get("reward", {}).get("type", -1)
            is_gateway = "reward" not in n or not power
            idx_info[i] = {"power": power, "name": name, "type": ntype, "gateway": is_gateway}
            if ntype == 2:  # passive
                name_to_indices[name].append(i)

        # Undirected adjacency
        adj: dict[int, set[int]] = defaultdict(set)
        for c in connections:
            adj[c["node1"]].add(c["node2"])
            adj[c["node2"]].add(c["node1"])

        gateways = {i for i in range(len(nodes)) if idx_info[i]["gateway"]}
        # Non-passive nodes (skills, upgrades) are always traversable
        free_nodes = {i for i in range(len(nodes))
                      if idx_info[i]["type"] in (0, 1) or idx_info[i]["gateway"]}

        _SKILL_TREE_GRAPH[cls_name] = {
            "idx_info": idx_info,
            "name_to_indices": dict(name_to_indices),
            "adj": dict(adj),
            "gateways": gateways,
            "free_nodes": free_nodes,
            "nodes": nodes,
        }


def _find_prereq_passives(cls: str, selected_names: list[str]) -> list[str]:
    """Given selected passives, return prerequisite passives needed to unlock them.

    Returns display names of prereq passives not already in selected_names,
    choosing the cheapest (fewest hops) prereq at each step.
    """
    graph = _SKILL_TREE_GRAPH.get(cls)
    if not graph:
        return []

    idx_info = graph["idx_info"]
    name_to_indices = graph["name_to_indices"]
    adj = graph["adj"]
    gateways = graph["gateways"]
    free_nodes = graph["free_nodes"]

    selected_set = set(selected_names)
    # Indices of allocated passives
    allocated = set(free_nodes)  # gateways + skills/upgrades always traversable
    for name in selected_set:
        for idx in name_to_indices.get(name, []):
            allocated.add(idx)

    prereqs_needed: list[str] = []

    # Iterate until all selected passives are reachable
    changed = True
    while changed:
        changed = False
        for name in list(selected_set):
            indices = name_to_indices.get(name, [])
            if not indices:
                continue

            for pidx in indices:
                # BFS from gateways through allocated nodes to see if pidx is reachable
                reachable = set()
                queue = deque()
                for g in gateways:
                    queue.append(g)
                    reachable.add(g)
                while queue:
                    cur = queue.popleft()
                    for neighbor in adj.get(cur, set()):
                        if neighbor not in reachable and (neighbor in allocated or neighbor == pidx):
                            reachable.add(neighbor)
                            queue.append(neighbor)

                if pidx in reachable:
                    continue  # already reachable

                # Not reachable — find shortest path and add the first missing passive
                # BFS from pidx outward to find nearest allocated passive or gateway
                visited = {pidx}
                parent: dict[int, int] = {pidx: -1}
                bfs = deque([pidx])
                found_root = -1
                while bfs:
                    cur = bfs.popleft()
                    if cur in allocated and cur != pidx:
                        found_root = cur
                        break
                    for neighbor in adj.get(cur, set()):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            parent[neighbor] = cur
                            bfs.append(neighbor)

                if found_root == -1:
                    continue

                # Trace path from found_root back to pidx, add missing passives
                path = []
                cur = found_root
                while cur != -1:
                    path.append(cur)
                    cur = parent.get(cur, -1)

                for node_idx in path:
                    info = idx_info[node_idx]
                    if (info["type"] == 2 and not info["gateway"]
                            and info["name"] not in selected_set
                            and info["name"] not in prereqs_needed):
                        prereqs_needed.append(info["name"])
                        selected_set.add(info["name"])
                        allocated.add(node_idx)
                        changed = True

    return prereqs_needed


# ─── Recommendation helpers (from v1) ────────────────────────────────────────

def select_passives(cls, skill_bar_pnames, all_skills):
    """Pick key passive + regular passives by tag overlap.

    After scoring, verifies passives are reachable in the skill tree and
    inserts 1-pt prerequisite passives where needed.
    """
    active_tags: set[str] = set()
    for pname in skill_bar_pnames:
        skill = all_skills.get(pname)
        if not skill:
            continue
        for tag in skill.skill_tags.split(","):
            t = tag.strip()
            if t:
                active_tags.add(t)

    candidates = [
        s for s in all_skills.values()
        if s.cls == cls and s.is_passive
        and s.display_name and not s.display_name.startswith("{c_red}")
        and not s.display_name.startswith("(PH)")
        and "_" not in s.display_name  # skip internal names
        and not s.primary_tag  # real passives have no primary_tag; minion/active "passives" do
        # Filter out Book of the Dead / Specialization passives
        and "_Passive_" not in s.power_name
        and "_Sacrifice" not in s.power_name
        and "_UpgradeA" not in s.power_name
        and "_UpgradeB" not in s.power_name
        and s.display_name not in ("Sacrifice", "Upgrade A", "Upgrade B", "Upgrade C")
    ]

    def is_key(s):
        if "Key Passive" in s.skill_tags:
            return True
        if "_KeyPassive_" in s.power_name or "_T5_" in s.power_name:
            return True
        if s.cls == "Sorcerer" and "_T3_" in s.power_name:
            return True
        return False

    # Build set of states the bar applies (for matching damage_mult_vs_*)
    bar_applied_states: set[str] = set()
    for pname in skill_bar_pnames:
        skill = all_skills.get(pname)
        if skill:
            bar_applied_states.update(skill.applies_states)

    def passive_score(p) -> float:
        """Score a passive by tag overlap PLUS its actual extracted effects."""
        p_tags = set(t.strip() for t in p.skill_tags.split(",") if t.strip())
        score = float(len(p_tags & active_tags))

        # Look up extracted effects
        eff_data = _PASSIVE_EFFECTS.get(p.power_name, {})
        for eff in eff_data.get("extracted_effects", []):
            tag = eff.get("tag", "")
            value = eff.get("value", 0)
            etype = eff.get("type", "additive")
            # Multiplicative damage bonuses are very strong
            if tag == "damage_mult":
                score += value * 10  # +12%[x] = +1.2 to score
            elif tag.startswith("damage_mult_vs_"):
                state = tag.replace("damage_mult_vs_", "")
                # Only valuable if the build applies that state
                if state in bar_applied_states:
                    score += value * 10
                else:
                    score += value * 2  # still some value if relying on aspects
            elif tag == "attack_speed_mult":
                score += value * 8
            elif tag == "attack_speed":
                score += value * 4
            elif tag == "crit_chance":
                score += value * 6
            elif tag == "crit_damage":
                score += value * 5
            elif tag == "resource_cost_reduction":
                score += value * 3
            elif tag == "damage_reduction":
                score += value * 2
            elif tag.startswith("damage"):
                score += value * 3  # additive damage = less valuable
        return score

    key_best = None
    regular = []
    for p in candidates:
        score = passive_score(p)
        if is_key(p):
            if key_best is None or score > key_best[0]:
                key_best = (score, p)
        else:
            regular.append((score, p))

    regular.sort(key=lambda x: (-x[0], x[1].display_name))
    key_name = key_best[1].display_name if key_best else None
    top_reg = [p.display_name for _, p in regular[:10]]

    # Ensure selected passives are reachable in the skill tree.
    # Add prerequisite passives, displacing lowest-scored picks to stay at 10.
    _load_skill_tree_graph()

    # Build the complete valid passive set iteratively.
    # Strategy: start with scored picks, find prereqs, drop lowest-scored to
    # make room, repeat until the set is self-consistent.
    all_scored = [(s, p.display_name) for s, p in regular]  # (score, name), desc order

    for _round in range(10):  # converges in 2-3 rounds
        prereqs = _find_prereq_passives(cls, top_reg)
        if not prereqs:
            break
        # Merge prereqs into the set, dropping lowest-scored non-prereq passives
        required = set(prereqs)
        for name in top_reg:
            required.add(name)
        # Score lookup
        score_of = {name: sc for sc, name in all_scored}
        # Prereqs get a minimum score so they aren't dropped
        for p in prereqs:
            if p not in score_of:
                score_of[p] = -1.0  # low but present
        # Sort all required: prereqs first (must keep), then by score desc
        prereq_set = set(prereqs)
        must_keep = [p for p in required if p in prereq_set]
        optional = sorted(
            [p for p in required if p not in prereq_set],
            key=lambda n: -score_of.get(n, 0),
        )
        top_reg = must_keep + optional
        top_reg = top_reg[:10]

    return key_name, top_reg


def pick_morph(base_name: str, all_skills: dict) -> dict:
    entry = _MORPHS.get(base_name)
    if not entry:
        return {"enhanced": None, "morph": None}
    enhanced = entry.get("enhanced")
    m1, m2 = entry.get("morph_1"), entry.get("morph_2")
    if not m1:
        return {"enhanced": enhanced, "morph": None}
    if not m2:
        return {"enhanced": enhanced, "morph": m1}
    # Pick morph with higher aspect+temper support
    def mscore(name):
        low = name.lower()
        s = all_skills.get(low)
        return 1 if s else 0  # simple heuristic
    chosen = m1 if mscore(m1) >= mscore(m2) else m2
    return {"enhanced": enhanced, "morph": chosen}


# ─── Output DB ────────────────────────────────────────────────────────────────

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
            paragon_boards       TEXT,
            purpose              TEXT,
            purpose_label        TEXT,
            global_rank          INTEGER,
            tier                 TEXT
        )
    """)
    conn.commit()


def write_builds(conn: sqlite3.Connection, builds: list[dict]) -> None:
    for b in builds:
        conn.execute(
            "INSERT INTO optimizer_results "
            "(class, specialization, rank, build_score, skill_bar, skill_upgrades, "
            "passives, key_passive, aspects, tempers, rune_pair_1, rune_pair_2, "
            "score_breakdown, aspects_recommended, tempers_recommended, gems_recommended, "
            "gear_recommended, mercenary, specialization_detail, class_mechanic, "
            "nightmare_dungeons, paragon_boards, purpose, purpose_label) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                b["class"], b["specialization"], b["rank"], b["build_score"],
                json.dumps(b["skill_bar"]), json.dumps(b.get("skill_upgrades", [])),
                json.dumps(b.get("passives", [])), b.get("key_passive"),
                json.dumps([]), json.dumps([]),
                json.dumps(b.get("rune_pair_1")), json.dumps(b.get("rune_pair_2")),
                json.dumps(b["score_breakdown"]),
                json.dumps(b.get("aspects_recommended", [])),
                json.dumps(b.get("tempers_recommended", {})),
                json.dumps(b.get("gems_recommended", {})),
                json.dumps(b.get("gear_recommended", {})),
                json.dumps(b.get("mercenary", {})),
                json.dumps(b.get("specialization_detail", {})),
                json.dumps(b.get("class_mechanic", {})),
                json.dumps(b.get("nightmare_dungeons", [])),
                json.dumps(b.get("paragon_boards", [])),
                b.get("purpose", ""),
                b.get("purpose_label", ""),
            ),
        )
    conn.commit()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    t0 = time.time()

    conn = sqlite3.connect(str(DB_IN))
    all_skills = load_active_skills(conn)
    paragon = load_paragon_data(conn)
    glyphs_by_class = load_glyphs(conn)
    runes = load_runes(conn)
    all_specs = load_specializations(conn)

    # Also load passive skills for passive selection
    passive_rows = conn.execute(
        "SELECT power_name, display_name, class, is_passive, skill_tags, primary_tag "
        "FROM skills WHERE is_passive = 1"
    ).fetchall()
    for pname, dname, cls, is_passive, stags, ptag in passive_rows:
        if pname not in all_skills:
            all_skills[pname] = SkillInfo(
                power_name=pname, display_name=dname or pname, cls=cls or "",
                is_passive=True, skill_tags=stags or "", primary_tag=ptag or "",
                damage_coeff=0.0, cooldown=0.0, resource_cost=0, lucky_hit=0.0,
                school_tags=frozenset(), aspect_bonus=0.0, temper_bonus=0.0,
            )

    conn.close()

    active_count = sum(1 for s in all_skills.values() if not s.is_passive)
    print(f"Loaded {active_count} active skills, {len(all_skills)} total")
    print(f"Paragon boards: {sum(len(v) for v in paragon.values())} legendary nodes")
    print(f"Glyphs: {sum(len(v) for v in glyphs_by_class.values())}")

    # Runes (class-independent)
    p1, p2, rune_bonus = best_rune_pairs(runes)
    print(f"Runes: best pair score = {rune_bonus:.2f}")

    # Load v1 recommendation data (aspects, tempers, gems, gear, mercenary)
    conn2 = sqlite3.connect(str(DB_IN))
    v1_sd = V1ScoringData(
        skills={},  # not needed for recommendations
        damage_map={},
        cooldowns={},
        aspect_bonuses={},
        temper_bonuses={},
        affix_rows=load_affixes(conn2),
        temper_rows=load_temper_rows(conn2),
        item_rows=load_item_rows(conn2),
        gem_lookup=load_gem_lookup(conn2),
    )
    conn2.close()
    print(f"Loaded recommendation data: {len(v1_sd.affix_rows)} affixes, "
          f"{len(v1_sd.temper_rows)} tempers, {len(v1_sd.item_rows)} items")

    # Cooldowns map for spec multiplier logic
    cd_map = {s.power_name: s.cooldown for s in all_skills.values()}

    out_conn = sqlite3.connect(str(DB_OUT))
    init_output_db(out_conn)

    classes = sorted(set(
        s.cls for s in all_skills.values()
        if not s.is_passive and s.cls and s.cls != "Generic"
    ))
    print(f"\nClasses: {', '.join(classes)}\n")

    all_class_builds: list[dict] = []

    MAX_POOL = 20  # Cap pool size to keep C(n,6) tractable: C(20,6)=38,760

    for cls in classes:
        cls_pool_raw = [
            s for s in all_skills.values()
            if s.cls == cls and not s.is_passive
            and (s.damage_coeff > 0 or score_skill(s, 1.0) > 0)
        ]
        if len(cls_pool_raw) < 6:
            print(f"  [{cls}] Only {len(cls_pool_raw)} skills with damage data — skipping")
            continue

        # Pre-filter: keep top MAX_POOL skills by individual score, but always
        # include at least 1 generator, 1 defensive, and 1 ultimate if available
        if len(cls_pool_raw) > MAX_POOL:
            scored = [(score_skill(s, 1.0), s) for s in cls_pool_raw]
            scored.sort(key=lambda x: -x[0])

            # Mandatory includes: best generator, best defensive, best ultimate
            must_include: set[str] = set()
            for s in cls_pool_raw:
                if s.is_generator and not any(m.is_generator for m in cls_pool_raw if m.power_name in must_include):
                    must_include.add(s.power_name)
                if s.is_defensive and not any(m.is_defensive for m in cls_pool_raw if m.power_name in must_include):
                    must_include.add(s.power_name)
                if s.is_ultimate and not any(m.is_ultimate for m in cls_pool_raw if m.power_name in must_include):
                    must_include.add(s.power_name)

            # Fill remaining slots from top scorers
            cls_pool = [s for _, s in scored if s.power_name in must_include]
            for _, s in scored:
                if len(cls_pool) >= MAX_POOL:
                    break
                if s.power_name not in must_include:
                    cls_pool.append(s)
            print(f"  [{cls}] {len(cls_pool_raw)} skills → pruned to {len(cls_pool)}")
        else:
            cls_pool = cls_pool_raw

        cls_boards = paragon.get(cls, [])
        cls_glyphs = glyphs_by_class.get(cls, [])
        specs = get_specs_for_class(cls, all_specs)

        from math import comb
        combo_count = comb(len(cls_pool), 6)
        print(f"  [{cls}] {len(cls_pool)} skills, {len(specs)} specs, "
              f"C({len(cls_pool)},6) = {combo_count} combos")

        # Pre-compute all spec multipliers
        all_spec_mults = {}
        for spec_name, mtype in specs:
            all_spec_mults[(spec_name, mtype)] = compute_spec_multipliers(cls, mtype, cls_pool, cd_map)

        # For each purpose, find the best build
        for purpose, cfg in BUILD_PURPOSES.items():
            purpose_results: list[tuple[float, str, str, list[SkillInfo], dict]] = []

            for spec_name, mtype in specs:
                spec_mults = all_spec_mults[(spec_name, mtype)]

                for combo_tuple in itertools.combinations(cls_pool, 6):
                    combo = list(combo_tuple)
                    # Fast constraint check before expensive scoring
                    if not is_valid_combo(combo, cfg):
                        continue
                    total, breakdown = score_build(
                        combo, spec_mults, cls_boards, cls_glyphs, rune_bonus,
                        purpose=purpose,
                    )
                    purpose_results.append((total, spec_name, mtype, combo, breakdown))

            purpose_results.sort(key=lambda x: -x[0])

            # Deduplicate and take top 1
            seen_combos: set[tuple[str, ...]] = set()
            best = None
            for result in purpose_results:
                combo_key = tuple(sorted(s.power_name for s in result[3]))
                if combo_key not in seen_combos:
                    seen_combos.add(combo_key)
                    best = result
                    break

            if not best:
                continue

            score, spec_name, mtype, combo, breakdown = best
            rank = 1
            skill_names = [s.display_name for s in combo]
            skill_pnames = [s.power_name for s in combo]
            key_passive, passives = select_passives(cls, skill_pnames, all_skills)
            skill_upgrades = [
                {"name": name, **pick_morph(name, all_skills)}
                for name in skill_names
            ]
            _, paragon_assignments = score_paragon(combo, cls_boards, cls_glyphs)

            # Paragon pathing
            from paragon_pathfinder import plan_paragon
            board_names = [p["board"] for p in paragon_assignments]
            glyph_names = [p["glyph"] or "" for p in paragon_assignments]
            paragon_paths = plan_paragon(cls, board_names, glyph_names)

            # Merge path data into assignments
            for assignment, path in zip(paragon_assignments, paragon_paths):
                assignment["activated_nodes"] = path.get("activated_nodes", [])
                assignment["points_spent"] = path.get("points_spent", 0)
                assignment["stats_gained"] = path.get("stats_gained", {})
                assignment["board_id"] = path.get("board_id", "")

            aspects_rec = select_aspects(cls, skill_names, v1_sd.affix_rows)
            tempers_rec = select_tempers(cls, skill_names, v1_sd)
            gems_rec = select_gems(score, cls, v1_sd)
            gear_rec = select_gear(cls, skill_names, v1_sd)

            combo_skill_tags = [s.skill_tags for s in combo]
            merc_rec = select_mercenary(cls, combo_skill_tags, score)
            nm_rec = select_nightmare_dungeons(aspects_rec, v1_sd)

            build = {
                "class": cls,
                "specialization": spec_name,
                "rank": rank,
                "purpose": purpose,
                "purpose_label": cfg["label"],
                "build_score": round(score, 6),
                "skill_bar": skill_names,
                "skill_bar_rank": SKILL_RANK,  # active skills assumed at rank 5
                "skill_upgrades": skill_upgrades,
                "passives": passives,
                "passive_rank": PASSIVE_RANK,  # passive skills assumed at rank 3
                "key_passive": key_passive,
                "rune_pair_1": p1,
                "rune_pair_2": p2,
                "score_breakdown": breakdown,
                "aspects_recommended": aspects_rec,
                "tempers_recommended": tempers_rec,
                "gems_recommended": gems_rec,
                "gear_recommended": gear_rec,
                "mercenary": merc_rec,
                "specialization_detail": {"name": spec_name, "mechanic_type": mtype},
                "paragon_boards": paragon_assignments,
                "nightmare_dungeons": nm_rec,
            }
            all_class_builds.append(build)
            print(f"    {cfg['label']:12s} [{spec_name}] score={score:.2f}: {', '.join(skill_names)}")

    # Write all builds
    write_builds(out_conn, all_class_builds)

    # Compute global ranks (for cross-class comparison, not used for tier badge)
    all_rows = out_conn.execute(
        "SELECT id, build_score FROM optimizer_results ORDER BY build_score DESC"
    ).fetchall()
    total = len(all_rows)
    for i, (row_id, _) in enumerate(all_rows):
        grank = i + 1
        out_conn.execute(
            "UPDATE optimizer_results SET global_rank = ? WHERE id = ?",
            (grank, row_id),
        )

    # Compute per-class tiers (what users actually see)
    # Each class's 4 builds are ranked relative to that class's score range.
    # This prevents one class with higher raw coefficients from sweeping all top tiers.
    classes = out_conn.execute(
        "SELECT DISTINCT class FROM optimizer_results"
    ).fetchall()
    for (cls,) in classes:
        cls_rows = out_conn.execute(
            "SELECT id, build_score FROM optimizer_results WHERE class = ? ORDER BY build_score DESC",
            (cls,),
        ).fetchall()
        if not cls_rows:
            continue
        top_score = cls_rows[0][1]
        for row_id, score in cls_rows:
            # Tier by percentage of the class's top score
            pct_of_top = score / top_score if top_score > 0 else 0
            tier = "S" if pct_of_top >= 0.90 else "A" if pct_of_top >= 0.75 else "B" if pct_of_top >= 0.60 else "C"
            out_conn.execute(
                "UPDATE optimizer_results SET tier = ? WHERE id = ?",
                (tier, row_id),
            )
    out_conn.commit()
    out_conn.close()

    elapsed = time.time() - t0
    print(f"\n{'='*65}")
    print(f"Wrote {len(all_class_builds)} builds to {DB_OUT}")
    print(f"Completed in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
