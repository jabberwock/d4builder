#!/usr/bin/env python3
"""
Score an existing build against the optimizer's model.
Feed it a class + skill names and it returns the score + breakdown.

Usage:
    python3 score_build.py --class Sorcerer --skills "Ice Shards,Frost Bolt,Firewall,Blizzard,Teleport,Deep Freeze"
    python3 score_build.py --class Sorcerer --skills "..." --purpose pit
    python3 score_build.py --json build.json
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Import scoring engine
from optimizer_v2 import (
    load_active_skills, load_paragon_data, load_glyphs, load_runes,
    load_specializations, score_build, score_skill, mechanical_synergy,
    survivability_score, best_rune_pairs, get_specs_for_class,
    compute_spec_multipliers, score_paragon, select_passives,
    BUILD_PURPOSES, SKILL_RANK, PASSIVE_RANK,
)

DB_PATH = Path(__file__).parent / "d4_stats.db"


def find_skill_by_name(name: str, all_skills: dict, cls: str):
    """Find a skill by display name (case-insensitive, fuzzy)."""
    name_lower = name.strip().lower()
    # Exact match first
    for s in all_skills.values():
        if s.cls == cls and not s.is_passive:
            if (s.display_name or "").lower() == name_lower:
                return s
    # Partial match
    for s in all_skills.values():
        if s.cls == cls and not s.is_passive:
            if name_lower in (s.display_name or "").lower():
                return s
    # Cross-class fallback
    for s in all_skills.values():
        if not s.is_passive and (s.display_name or "").lower() == name_lower:
            return s
    return None


def score_existing_build(
    cls: str,
    skill_names: list[str],
    purpose: str = "pit",
    spec_override: str | None = None,
):
    """Score a build defined by class + skill display names."""
    conn = sqlite3.connect(str(DB_PATH))
    all_skills = load_active_skills(conn)
    paragon = load_paragon_data(conn)
    glyphs_by_class = load_glyphs(conn)
    runes = load_runes(conn)
    all_specs = load_specializations(conn)

    # Also load passives
    passive_rows = conn.execute(
        "SELECT power_name, display_name, class, is_passive, skill_tags, primary_tag "
        "FROM skills WHERE is_passive = 1"
    ).fetchall()
    from optimizer_v2 import SkillInfo
    for pname, dname, cls2, is_passive, stags, ptag in passive_rows:
        if pname not in all_skills:
            all_skills[pname] = SkillInfo(
                power_name=pname, display_name=dname or pname, cls=cls2 or "",
                is_passive=True, skill_tags=stags or "", primary_tag=ptag or "",
                damage_coeff=0.0, cooldown=0.0, resource_cost=0, lucky_hit=0.0,
                school_tags=frozenset(), aspect_bonus=0.0, temper_bonus=0.0,
            )
    conn.close()

    # Resolve skill names to SkillInfo objects
    combo = []
    missing = []
    for name in skill_names:
        skill = find_skill_by_name(name, all_skills, cls)
        if skill:
            combo.append(skill)
        else:
            missing.append(name)

    if missing:
        print(f"WARNING: Could not find skills: {', '.join(missing)}")
        print(f"Available {cls} skills:")
        available = sorted(set(
            s.display_name for s in all_skills.values()
            if s.cls == cls and not s.is_passive and s.display_name
        ))
        for s in available:
            print(f"  - {s}")
        if len(combo) < 6:
            print(f"\nOnly found {len(combo)}/6 skills, cannot score.")
            return

    # Pick spec
    specs = get_specs_for_class(cls, all_specs)
    if spec_override:
        spec_match = [(n, m) for n, m in specs if spec_override.lower() in n.lower()]
        spec_name, mtype = spec_match[0] if spec_match else specs[0]
    else:
        # Try each spec, pick the one that scores highest
        best_spec = None
        best_score = -1
        cd_map = {s.power_name: s.cooldown for s in all_skills.values()}
        for sn, mt in specs:
            sm = compute_spec_multipliers(cls, mt, combo, cd_map)
            cls_boards = paragon.get(cls, [])
            cls_glyphs = glyphs_by_class.get(cls, [])
            _, p2, rune_bonus = best_rune_pairs(runes)
            s, _ = score_build(combo, sm, cls_boards, cls_glyphs, rune_bonus, purpose)
            if s > best_score:
                best_score = s
                best_spec = (sn, mt)
        spec_name, mtype = best_spec

    # Final scoring
    cd_map = {s.power_name: s.cooldown for s in all_skills.values()}
    spec_mults = compute_spec_multipliers(cls, mtype, combo, cd_map)
    cls_boards = paragon.get(cls, [])
    cls_glyphs = glyphs_by_class.get(cls, [])
    _, _, rune_bonus = best_rune_pairs(runes)

    total, breakdown = score_build(combo, spec_mults, cls_boards, cls_glyphs, rune_bonus, purpose)

    # Synergy and survivability detail
    syn = mechanical_synergy(combo)
    cfg = BUILD_PURPOSES[purpose]
    surv = survivability_score(combo, cfg)

    # Passive recommendation
    skill_pnames = [s.power_name for s in combo]
    key_passive, passives = select_passives(cls, skill_pnames, all_skills)

    # Compare to optimizer's best
    from optimizer_v2 import DB_OUT
    try:
        out_conn = sqlite3.connect(str(DB_OUT))
        opt_row = out_conn.execute(
            "SELECT build_score, skill_bar FROM optimizer_results "
            "WHERE class = ? AND purpose = ? ORDER BY build_score DESC LIMIT 1",
            (cls, purpose)
        ).fetchone()
        out_conn.close()
    except Exception:
        opt_row = None

    # Print results
    print(f"\n{'='*65}")
    print(f"  {cls} — {purpose.upper()} Build Score")
    print(f"  Spec: {spec_name}")
    print(f"{'='*65}\n")

    print("SKILL BREAKDOWN:")
    for s in combo:
        sc = breakdown.get(s.display_name, 0)
        role = []
        if s.is_generator: role.append("GEN")
        if s.is_defensive: role.append("DEF")
        if s.is_ultimate: role.append("ULT")
        if s.is_aoe: role.append("AOE")
        if s.is_cc: role.append("CC")
        role_str = f" [{','.join(role)}]" if role else ""
        print(f"  {s.display_name:30s}  {sc:>8.1f}{role_str}")

    skill_total = sum(breakdown.values())
    print(f"  {'':30s}  {'--------':>8s}")
    print(f"  {'Skill Total':30s}  {skill_total:>8.1f}")

    print(f"\nMULTIPLIERS:")
    print(f"  Mechanical Synergy:   +{syn:.1%}")
    print(f"  Survivability:        x{surv:.3f}")
    surv_weight = cfg.get("survivability_weight", 1.0)
    surv_factor = 1.0 + (surv - 1.0) * surv_weight
    print(f"  Surv Factor (weighted): x{surv_factor:.3f}")
    print(f"  Rune Bonus:           +{rune_bonus:.1f}")

    print(f"\n  TOTAL SCORE:          {total:.0f}")

    if opt_row:
        opt_score, opt_skills = opt_row
        pct = (total / opt_score) * 100
        print(f"\n  vs. Optimizer Best:   {opt_score:.0f} ({json.loads(opt_skills)[:3]}...)")
        print(f"  Your build is {pct:.1f}% of optimal")

    print(f"\n  Suggested Key Passive: {key_passive}")
    print(f"  Suggested Passives:    {', '.join(passives[:6])}")

    return total


def main():
    parser = argparse.ArgumentParser(description="Score an existing D4 build")
    parser.add_argument("--cls", required=True, help="Class name (e.g., Sorcerer)")
    parser.add_argument("--skills", required=True, help="Comma-separated skill names")
    parser.add_argument("--purpose", default="pit", choices=list(BUILD_PURPOSES.keys()))
    parser.add_argument("--spec", default=None, help="Specialization name override")
    args = parser.parse_args()

    skills = [s.strip() for s in args.skills.split(",")]
    score_existing_build(args.cls, skills, args.purpose, args.spec)


if __name__ == "__main__":
    main()
