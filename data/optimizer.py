#!/usr/bin/env python3
"""
Diablo 4 Build Optimizer
Reads from d4_stats.db, writes results to optimizer_results.db.

Scoring model (max-roll assumption):
  base_skill_score = damage_pct@rank7 / max(cooldown_base, 1.0)
  aspect_multiplier = 1.0 + sum(max_value/100 for linked affixes)
  temper_multiplier = 1.0 + sum(max_value/100 for matching tempers)
  skill_score = base_skill_score * aspect_multiplier * temper_multiplier
  rune_bonus = sum(runic_amount for top-4 runes) / 1000.0
  build_score = sum(skill_scores) + rune_bonus
"""

import sqlite3
import json
import itertools
from dataclasses import dataclass
from pathlib import Path

DB_IN  = Path(__file__).parent / "d4_stats.db"
DB_OUT = Path(__file__).parent / "optimizer_results.db"

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
    """Return best damage_pct per skill: rank 7 if available, else rank 5."""
    rows = conn.execute(
        "SELECT power_name, rank, damage_pct FROM skill_damage "
        "WHERE rank IN (5, 7)"
    ).fetchall()
    # rank 7 preferred
    r7: dict[str, float] = {}
    r5: dict[str, float] = {}
    for pname, rank, dpct in rows:
        if dpct is None:
            continue
        if rank == 7:
            r7[pname] = float(dpct)
        elif rank == 5:
            r5[pname] = float(dpct)
    result: dict[str, float] = dict(r5)
    result.update(r7)
    return result


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


def load_runes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT rune_name, display_name, rune_type, runic_amount "
        "FROM runes WHERE runic_amount IS NOT NULL"
    ).fetchall()
    runes = []
    for rname, dname, rtype, amt in rows:
        if rname.lower().startswith("test_"):
            continue
        try:
            runes.append({
                "rune_name": rname,
                "display_name": dname or rname,
                "rune_type": rtype or "",
                "runic_amount": float(amt),
            })
        except (TypeError, ValueError):
            pass
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

def best_rune_pairs(
    runes: list[dict],
) -> tuple[dict | None, dict | None, float]:
    """
    Two runeword slots, each = (ritual, invocation).
    Pick 4 distinct runes (2 ritual + 2 invocation) maximising total runic_amount.
    """
    ritual    = sorted([r for r in runes if r["rune_type"] == "ritual"],
                       key=lambda r: r["runic_amount"], reverse=True)
    invocation = sorted([r for r in runes if r["rune_type"] == "invocation"],
                        key=lambda r: r["runic_amount"], reverse=True)

    if not ritual or not invocation:
        return None, None, 0.0

    best_score = -1.0
    best_p1 = best_p2 = None

    for r1, r2 in itertools.combinations(ritual[:10], 2):
        for i1, i2 in itertools.combinations(invocation[:10], 2):
            total = (r1["runic_amount"] + r2["runic_amount"]
                     + i1["runic_amount"] + i2["runic_amount"])
            if total > best_score:
                best_score = total
                pairs = sorted(
                    [(r1, i1), (r2, i2)],
                    key=lambda p: p[0]["runic_amount"] + p[1]["runic_amount"],
                    reverse=True,
                )
                best_p1, best_p2 = pairs[0], pairs[1]

    if best_p1 is None:
        return None, None, 0.0

    def to_dict(r, i):
        return {"ritual": r["rune_name"], "invocation": i["rune_name"],
                "runic_amount": r["runic_amount"] + i["runic_amount"]}

    p1 = to_dict(*best_p1)
    p2 = to_dict(*best_p2)
    return p1, p2, best_score


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


def build_score_breakdown(
    combo: list[str],
    sd: ScoringData,
    spec_multipliers: dict[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    """Score a 6-skill combo. spec_multipliers: power_name -> extra multiplier."""
    breakdown: dict[str, float] = {}
    total = 0.0
    for pname in combo:
        s = score_skill(pname, sd)
        if spec_multipliers and pname in spec_multipliers:
            s *= spec_multipliers[pname]
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
    """Active (non-passive) skills for a class or Generic."""
    return [
        s for s in skills.values()
        if (s["class"] == cls or s["class"] == "Generic")
        and not s["is_passive"]
        and s["display_name"]
        and not s["display_name"].startswith("{c_red}")  # skip WIP skills
        and not s["display_name"].startswith("(PH)")     # skip placeholder skills
    ]


def pre_score_skills(
    pool: list[dict],
    sd: ScoringData,
) -> list[tuple[float, dict]]:
    """Return [(score, skill_dict), ...] sorted descending."""
    scored = [(score_skill(s["power_name"], sd), s) for s in pool]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def optimize_class_spec(
    cls: str,
    spec: dict,
    sd: ScoringData,
    runes: list[dict],
) -> list[dict]:
    """
    Return up to TOP_N_PER_SPEC build dicts for one class+specialization combo.
    """
    pool = active_class_skills(cls, sd.skills)
    if not pool:
        return []

    mtype = spec["mechanic_type"]

    # Determine spec-specific multipliers and hard constraints
    spec_multipliers: dict[str, float] = {}
    requires_ultimate = False

    if cls == "Rogue" and mtype == "combo_points":
        # Core skills get 1.39x multiplier (conservative 3-point combo bonus)
        for s in pool:
            if is_core(s):
                spec_multipliers[s["power_name"]] = 1.39

    if cls == "Rogue" and mtype == "preparation":
        requires_ultimate = True

    # Pre-score all skills
    all_scored = pre_score_skills(pool, sd)

    # Bucket by role (power_name strings)
    basics    = [s["power_name"] for _, s in all_scored if is_basic(s)]
    ultimates = [s["power_name"] for _, s in all_scored if is_ultimate(s) and not is_basic(s)]
    # (others list unused directly; other_candidates built below)

    # Spec-constraint filtering for "others"
    if cls == "Rogue" and mtype == "combo_points":
        # Must include at least 1 Core skill in the 4 "other" slots
        # (not enforced by bucket but verified at combo level)
        pass

    if cls == "Paladin" and mtype == "oaths":
        spec_name = spec["name"]
        if spec_name == "Judicator":
            # Must have ≥1 Core Judicator skill in others
            pass  # enforced at combo level

    if not basics:
        return []

    # Restrict to top MAX_OTHER_CANDIDATES others by score
    other_candidates = [s["power_name"] for _, s in all_scored
                        if not is_basic(s) and not is_ultimate(s)][:MAX_OTHER_CANDIDATES]

    results: list[tuple[float, list[str], dict[str, float]]] = []

    def try_combo(combo: list[str]) -> None:
        """Validate constraints and score a combo."""
        skill_objs = [sd.skills[p] for p in combo if p in sd.skills]

        # All-class: exactly 1 Basic
        basics_in = [s for s in skill_objs if is_basic(s)]
        if len(basics_in) != 1:
            return

        # All-class: max 1 Ultimate
        ults_in = [s for s in skill_objs if is_ultimate(s)]
        if len(ults_in) > 1:
            return

        # Preparation: must have exactly 1 Ultimate
        if requires_ultimate and len(ults_in) != 1:
            return

        # Rogue Combo Points: must have ≥1 Core skill
        if cls == "Rogue" and mtype == "combo_points":
            cores_in = [s for s in skill_objs if is_core(s)]
            if not cores_in:
                return

        # Paladin Judicator: must have ≥1 Core Judicator skill (Judicator_Skill tag)
        if cls == "Paladin" and mtype == "oaths" and spec["name"] == "Judicator":
            judicator_cores = [
                s for s in skill_objs
                if is_core(s) and has_tag(s, "Judicator_Skill")
            ]
            if not judicator_cores:
                return

        total, bd = build_score_breakdown(combo, sd, spec_multipliers)
        results.append((total, combo, bd))

    # Enumerate combos: 1 basic + [0-1 ultimate] + fill to 6 from other_candidates
    for b in basics:
        # Without ultimate (5 others needed)
        needed = 5
        for fill in itertools.combinations(
            other_candidates, min(needed, len(other_candidates))
        ):
            if len(fill) == needed:
                try_combo([b] + list(fill))

        # With each ultimate (4 others needed)
        needed = 4
        for u in ultimates:
            for fill in itertools.combinations(
                other_candidates, min(needed, len(other_candidates))
            ):
                if len(fill) == needed:
                    try_combo([b, u] + list(fill))

    results.sort(key=lambda x: x[0], reverse=True)
    top = results[:TOP_N_PER_SPEC]

    # Rune bonus (global, class-agnostic)
    pair1, pair2, rune_total = best_rune_pairs(runes)
    rune_bonus = rune_total / 1000.0

    # Sorcerer enchantment bonus: top 2 non-bar skills by score add +15% each to total
    enchant_bonus = 0.0
    if cls == "Sorcerer" and mtype == "enchantment_slots":
        if top:
            bar_set = set(top[0][1])
            non_bar = [s for s in all_scored if s[1]["power_name"] not in bar_set
                       and not is_basic(s[1])][:2]
            enchant_bonus = len(non_bar) * 0.15 * (top[0][0] if top[0][0] else 0.0)

    builds = []
    for rank, (score, combo, breakdown) in enumerate(top, start=1):
        final_score = score + rune_bonus + (enchant_bonus if rank == 1 else 0.0)
        builds.append({
            "class": cls,
            "specialization": spec["name"],
            "rank": rank,
            "build_score": round(final_score, 6),
            "skill_bar": [sd.skills[p]["display_name"] for p in combo if p in sd.skills],
            "skill_bar_power_names": combo,
            "aspects": [],        # populated below
            "tempers": [],
            "rune_pair_1": pair1,
            "rune_pair_2": pair2,
            "score_breakdown": breakdown,
        })

    return builds


# ---------------------------------------------------------------------------
# Output DB
# ---------------------------------------------------------------------------

def init_output_db(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS optimizer_results")
    conn.execute("""
        CREATE TABLE optimizer_results (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            class            TEXT,
            specialization   TEXT,
            rank             INTEGER,
            build_score      REAL,
            skill_bar        TEXT,
            aspects          TEXT,
            tempers          TEXT,
            rune_pair_1      TEXT,
            rune_pair_2      TEXT,
            score_breakdown  TEXT
        )
    """)
    conn.commit()


def write_builds(conn: sqlite3.Connection, builds: list[dict]) -> None:
    for b in builds:
        conn.execute(
            "INSERT INTO optimizer_results "
            "(class, specialization, rank, build_score, skill_bar, aspects, "
            "tempers, rune_pair_1, rune_pair_2, score_breakdown) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                b["class"],
                b["specialization"],
                b["rank"],
                b["build_score"],
                json.dumps(b["skill_bar"]),
                json.dumps(b["aspects"]),
                json.dumps(b["tempers"]),
                json.dumps(b["rune_pair_1"]),
                json.dumps(b["rune_pair_2"]),
                json.dumps(b["score_breakdown"]),
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# One representative spec per class mechanic_type (avoid redundant runs for
# specializations that don't differ in skill constraints, e.g. Barbarian has
# 7 Arsenal rows but they're equipment bonuses, not skill-bar constraints).
# We run ONE spec per unique mechanic_type per class.
SPEC_PRIORITY: dict[str, list[str]] = {
    "Barbarian":   ["weapon_expertise"],   # all 7 rows same constraints
    "Druid":       ["spirit_boon"],        # all 16 rows same constraints
    "Sorcerer":    ["enchantment_slots"],
    "Rogue":       ["combo_points", "inner_sight", "preparation"],
    "Necromancer": ["book_of_the_dead"],
    "Spiritborn":  ["guardian_spirit"],
    "Paladin":     ["oaths"],
    "Warlock":     ["soul_shards"],
}

# For classes where multiple spec rows with same mechanic_type exist, pick first
def pick_specs_per_class(specs: list[dict]) -> dict[str, list[dict]]:
    """Return {class: [spec_dicts]} using priority list, one per mechanic_type."""
    by_class: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}
    for spec in specs:
        cls = spec["class"]
        mtype = spec["mechanic_type"]
        seen.setdefault(cls, set())
        if mtype not in seen[cls]:
            by_class.setdefault(cls, []).append(spec)
            seen[cls].add(mtype)
    return by_class


def main() -> None:
    if not DB_IN.exists():
        raise FileNotFoundError(f"Source DB not found: {DB_IN}")

    print(f"Reading from: {DB_IN}")
    conn = sqlite3.connect(str(DB_IN))

    sd = ScoringData(
        skills        = load_skills(conn),
        damage_map    = load_skill_damage(conn),
        cooldowns     = load_cooldowns(conn),
        aspect_bonuses = load_aspect_bonuses(conn),
        temper_bonuses = load_temper_bonuses(conn),
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
        f"{len(all_specs)} specialization rows"
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
            p1, p2 = b["rune_pair_1"], b["rune_pair_2"]
            print(f"  Runes : [{p1['ritual']} + {p1['invocation']}]  "
                  f"[{p2['ritual']} + {p2['invocation']}]")

    print(f"\nTotal rows written to optimizer_results.db: {total_rows}")
    print(f"Results at: {DB_OUT}")


if __name__ == "__main__":
    main()
