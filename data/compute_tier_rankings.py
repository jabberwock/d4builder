#!/usr/bin/env python3
"""
Compute S/A/B/C tier rankings for all builds using real data from d4_stats.db.

Data sources:
- skill_coefficients: weapon damage coefficients from DiabloTools/d4data S12 game files
- tempers: real temper ranges from Wowhead/Maxroll/Game8 verified data
- glyph_levels: glyph effect multipliers at glyph level 21 (endgame standard)
- paragon stat_increases: from build JSON files

Scoring weights:
- Primary skill DPS (35%): core-tier coefficient × rank scaling
- Temper offensive bonus (30%): sum of offensive tempers at midpoint value
- Paragon damage investment (20%): total damage stats in paragon boards
- Glyph offensive support (15%): offensive glyphs × effect multiplier @ lvl 21
"""

import json
import sqlite3
import glob
from pathlib import Path

DB_PATH = Path(__file__).parent / "d4_stats.db"
BUILDS_DIR = Path(__file__).parent.parent / "webapp" / "public" / "data" / "builds"
INDEX_PATH = Path(__file__).parent.parent / "webapp" / "public" / "data" / "builds_index.json"

# Gear +skill bonus (standard endgame: Sacred gear typically gives +3-4 to skills)
GEAR_SKILL_BONUS = 3

# Nodes in radius at glyph level 21 (radius=4 squares, typical board geometry)
NODES_IN_RADIUS_21 = 16

# Glyph effect_multiplier at level 21
GLYPH_MULT_21 = 0.71

# DPS tier ordering for skill scan (most→least preferred for primary)
PRIMARY_TIERS = [
    'core', 'focus', 'ferocity', 'potency', 'wrath', 'justice',
    'valor', 'aura', 'incarnate', 'resolve', 'spirit',
    'imbuement', 'trap', 'companion', 'macabre', 'corpse_macabre',
    'summoning', 'summon', 'curse', 'corruption',
    'brawling', 'agility', 'weapon_mastery', 'enchantment_slots',
]

# Skills that deal DPS but aren't the primary spammable (discount their contribution)
COOLDOWN_SKILLS = {
    "Wrath of the Berserker", "Call of the Ancients", "Iron Maelstrom",
    "Grizzly Rage", "Ravens", "Inferno", "Unstable Currents", "Deep Freeze",
    "Bone Storm", "Army of the Dead", "Rain of Arrows", "Death Trap",
    "Shadow Clone", "Heaven's Fury", "Wrath of the Berserker",
    "The Seeker", "Rally", "Stampede",
}

# Skills that are buffs/utility (not DPS at all)
BUFF_SKILLS = {
    "Rallying Cry", "War Cry", "Challenging Shout", "Iron Skin",
    "Blood Howl", "Cyclone Armor", "Earthen Bulwark", "Debilitating Roar",
    "Blood Mist", "Decrepify", "Bone Prison", "Dark Shroud", "Concealment",
    "Cold Imbuement", "Shadow Imbuement", "Poison Imbuement", "Smoke Grenade",
    "Flame Shield", "Ice Armor", "Frost Nova", "Teleport", "Purify",
    "Holy Shield", "Armored Hide", "Counterattack",
}

def load_data(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    coeff_map = {}
    for row in cur.execute("SELECT skill_name, coefficient FROM skill_coefficients WHERE coefficient IS NOT NULL"):
        coeff_map[row[0]] = row[1]

    temper_map = {}
    for row in cur.execute("SELECT temper_name, range_min, range_max, range_unit, category FROM tempers WHERE range_min IS NOT NULL"):
        name, rmin, rmax, unit, cat = row
        temper_map[name] = {'min': rmin, 'max': rmax, 'unit': unit, 'category': cat}

    glyph_map = {}
    import re
    for row in cur.execute("SELECT glyph_id, glyph_name, effect, glyph_type FROM glyphs"):
        gid, gname, effect, gtype = row
        m = re.search(r'\+(\d+\.?\d*)', effect or '')
        bpp = float(m.group(1)) if m else 0.5  # default 0.5% per point
        glyph_map[gname] = {'bonus_per_point': bpp, 'type': (gtype or '').lower()}

    conn.close()
    return coeff_map, temper_map, glyph_map


def get_primary_skill_score(d, coeff_map):
    """Find core-tier primary DPS skill and compute base score."""
    skill_tiers = d.get('skills', {})

    # Build list of (tier_priority, coefficient, rank, name) tuples
    candidates = []
    for tier_idx, tier in enumerate(PRIMARY_TIERS):
        items = skill_tiers.get(tier, [])
        if not isinstance(items, list):
            continue
        for s in items:
            if not isinstance(s, dict):
                continue
            name = s.get('name', '')
            note = s.get('note', '')
            if not name or note.startswith('PASSIVE'):
                continue
            if name in BUFF_SKILLS:
                continue
            if name not in coeff_map:
                continue
            coeff = coeff_map[name]
            rank = s.get('rank', 1)
            # Cooldown/ultimate skills get a frequency discount
            freq = 0.4 if name in COOLDOWN_SKILLS else 1.0
            # Effective rank: base + gear bonus
            eff_rank = rank + GEAR_SKILL_BONUS
            rank_mult = 1.0 + (eff_rank - 1) * 0.05
            candidates.append((tier_idx, -coeff * freq * rank_mult, tier, name, coeff, rank, coeff * freq * rank_mult))

    if not candidates:
        return 0.0, None, None

    # Sort: lowest tier_idx (= most preferred tier) first, then highest score
    candidates.sort(key=lambda x: (x[0], x[1]))
    best = candidates[0]
    return best[6], best[3], best[2]


def get_temper_score(d, temper_map):
    """Sum offensive temper bonuses at midpoint of range."""
    total = 0.0
    for slot, gear in d.get('gear', {}).items():
        if not gear:
            continue
        for key in ['temper_1', 'temper_2']:
            t = gear.get(key)
            if t and t in temper_map:
                td = temper_map[t]
                if td['category'] == 'offensive':
                    mid = (td['min'] + td['max']) / 2
                    total += mid
    return total


def get_glyph_score(d, glyph_map):
    """Sum offensive glyph bonuses at standard endgame level 21."""
    total = 0.0
    pb = d.get('paragon_boards', {})
    for key in ['starting', 'board_1', 'board_2', 'board_3', 'board_4']:
        b = pb.get(key)
        if not b or not isinstance(b.get('glyph'), dict):
            continue
        gname = b['glyph']['name']
        if gname not in glyph_map:
            continue
        gd = glyph_map[gname]
        # Only count offensive-typed glyphs
        gtype = gd['type']
        if 'druid' in gtype or 'utility' in gtype:
            # Still count but at 50% weight (some are partially offensive)
            weight = 0.5
        else:
            weight = 1.0
        bonus = gd['bonus_per_point'] * NODES_IN_RADIUS_21 * GLYPH_MULT_21 * weight
        total += bonus
    return total


def get_paragon_score(d):
    """Sum offensive damage stats from paragon boards."""
    total = 0
    pb = d.get('paragon_boards', {})
    for key in ['starting', 'board_1', 'board_2', 'board_3', 'board_4']:
        b = pb.get(key)
        if not b or not isinstance(b, dict):
            continue
        si = b.get('stat_increases', {})
        # Count direct damage stats
        total += si.get('damage', 0)
        total += si.get('weapon_damage', 0) * 0.7
        total += si.get('bleed_damage', 0) * 0.4
        total += si.get('fire_damage', 0) * 0.5
        total += si.get('shadow_damage', 0) * 0.5
        total += si.get('poison_damage', 0) * 0.4
        total += si.get('overpower_damage', 0) * 0.5
        total += si.get('melee_damage', 0) * 0.5
        # Defensive investment reduces offensive potential (opportunity cost)
        total -= si.get('fortify', 0) * 0.1
        total -= si.get('damage_reduction', 0) * 0.5
    return max(0, total)


def score_build(d, fname, coeff_map, temper_map, glyph_map):
    primary_score, best_skill, best_tier = get_primary_skill_score(d, coeff_map)
    temper_score = get_temper_score(d, temper_map)
    glyph_score = get_glyph_score(d, glyph_map)
    paragon_score = get_paragon_score(d)

    # Normalize each component to comparable scale, then weight
    # Primary: max expected ~6.0, normalize to 0-100
    primary_norm = min(primary_score / 6.0, 1.0) * 100

    # Temper: max expected ~1200 (10 offensive slots × ~120 each), normalize to 0-100
    temper_norm = min(temper_score / 1200.0, 1.0) * 100

    # Glyph: max expected ~35 (5 glyphs × 7%), normalize to 0-100
    glyph_norm = min(glyph_score / 35.0, 1.0) * 100

    # Paragon: max expected ~400, normalize to 0-100
    paragon_norm = min(paragon_score / 400.0, 1.0) * 100

    composite = (
        primary_norm * 0.35 +
        temper_norm * 0.30 +
        glyph_norm * 0.15 +
        paragon_norm * 0.20
    )

    return {
        'file': fname,
        'name': d.get('build_name', '?'),
        'class': d.get('class', '?'),
        'primary_score': primary_score,
        'primary_norm': primary_norm,
        'best_skill': best_skill,
        'best_tier': best_tier,
        'temper_score': temper_score,
        'temper_norm': temper_norm,
        'glyph_score': glyph_score,
        'glyph_norm': glyph_norm,
        'paragon_score': paragon_score,
        'paragon_norm': paragon_norm,
        'composite': composite,
    }


def assign_tier(score, percentile_75, percentile_50, percentile_25):
    if score >= percentile_75:
        return 'S'
    elif score >= percentile_50:
        return 'A'
    elif score >= percentile_25:
        return 'B'
    else:
        return 'C'


def main():
    coeff_map, temper_map, glyph_map = load_data(DB_PATH)
    print(f"Loaded: {len(coeff_map)} skill coefficients, {len(temper_map)} tempers, {len(glyph_map)} glyphs\n")

    build_files = sorted(BUILDS_DIR.glob("*.json"))
    scores = []
    for fpath in build_files:
        d = json.loads(fpath.read_text())
        fname = fpath.stem
        s = score_build(d, fname, coeff_map, temper_map, glyph_map)
        scores.append((fpath, d, s))

    # Sort by composite score
    scores.sort(key=lambda x: -x[2]['composite'])

    # Compute tier thresholds (quartiles)
    composites = sorted([x[2]['composite'] for x in scores])
    n = len(composites)
    p75 = composites[int(n * 0.75)]
    p50 = composites[int(n * 0.50)]
    p25 = composites[int(n * 0.25)]

    print(f"Score thresholds: S≥{p75:.1f}, A≥{p50:.1f}, B≥{p25:.1f}, C<{p25:.1f}")
    print(f"{'Rank':<4} {'Tier':<4} {'Build':<40} {'Class':<12} {'Pri':>5} {'Tem':>5} {'Gly':>5} {'Par':>5} {'Total':>7}")
    print('-' * 95)

    tier_counts = {'S': 0, 'A': 0, 'B': 0, 'C': 0}
    results = []
    for rank, (fpath, d, s) in enumerate(scores, 1):
        tier = assign_tier(s['composite'], p75, p50, p25)
        tier_counts[tier] += 1
        results.append((fpath, d, s, tier, rank))
        print(
            f"{rank:<4} {tier:<4} {s['name']:<40} {s['class']:<12} "
            f"{s['primary_norm']:>5.1f} {s['temper_norm']:>5.1f} "
            f"{s['glyph_norm']:>5.1f} {s['paragon_norm']:>5.1f} "
            f"{s['composite']:>7.2f}  [{s['best_skill'] or 'none'}]"
        )

    print(f"\nTier counts: {tier_counts}")

    # Write tiers back to build JSON files and update builds_index.json
    print("\nUpdating build files...")
    tier_map = {}
    for fpath, d, s, tier, rank in results:
        d['tier'] = tier
        d['efficiency_score'] = round(s['composite'], 1)
        d['season_rank'] = rank
        fpath.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        tier_map[fpath.stem] = {'tier': tier, 'score': round(s['composite'], 1), 'rank': rank}
        print(f"  {fpath.name}: tier={tier}, score={round(s['composite'],1)}, rank={rank}")

    # Update builds_index.json
    print("\nUpdating builds_index.json...")
    index = json.loads(INDEX_PATH.read_text())
    for build in index['builds']:
        key = build.get('file', '').replace('.json', '')
        if key in tier_map:
            build['tier'] = tier_map[key]['tier']
            build['efficiency_score'] = tier_map[key]['score']
            build['season_rank'] = tier_map[key]['rank']

    index['tier_methodology'] = (
        "Composite score (0-100) weighted: primary skill DPS (35%, using DiabloTools/d4data "
        "S12 coefficients × endgame rank), offensive tempers (30%, midpoint of verified ranges "
        "from Wowhead/Maxroll/Game8), paragon damage investment (20%, from build stat_increases), "
        "glyph offensive bonus (15%, +X%%/point × nodes in radius × effect multiplier at level 21). "
        "S≥75th percentile, A≥50th, B≥25th, C<25th."
    )
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    print("Done.")


if __name__ == "__main__":
    main()
