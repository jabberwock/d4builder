#!/usr/bin/env python3
"""
Clean build JSONs against verified game data in d4_stats.db.
- Removes skills that don't exist for their class
- Nulls aspects that don't exist in game files
- Validates rune names
- Recalculates efficiency scores from verified coefficients
- Updates builds_index.json (Paladin builds already excluded)
"""
import json, re, sqlite3, glob, os
from pathlib import Path

DB_PATH    = Path(__file__).parent / 'd4_stats.db'
BUILDS_DIR = Path(__file__).parent.parent / 'webapp/public/data/builds'
INDEX_PATH = Path(__file__).parent.parent / 'webapp/public/data/builds_index.json'

db = sqlite3.connect(DB_PATH)

# ── load ground truth ──────────────────────────────────────────────────────

# Skills: display_name (lower) → class set
real_skills: dict[str, set] = {}
for row in db.execute("SELECT LOWER(display_name), class FROM skills"):
    name, cls = row
    real_skills.setdefault(name, set()).add(cls)

# Aspects: normalised display names (lower, strip leading "of ")
real_aspects: set[str] = set()
for (name,) in db.execute("SELECT LOWER(display_name) FROM aspects"):
    real_aspects.add(name)
    real_aspects.add(name.lstrip('of ').strip())  # also without "of " prefix

# Runes
real_runes: set[str] = set()
for (name,) in db.execute("SELECT LOWER(display_name) FROM runes"):
    real_runes.add(name)

# dps_coeff lookup: display_name (lower) → dps_coeff
dps_lookup: dict[str, float] = {}
for row in db.execute("SELECT LOWER(display_name), dps_coeff FROM skill_coefficients"):
    dps_lookup[row[0]] = row[1] or 0.0

db.close()

# ── morph prefixes (stripped before skill lookup) ─────────────────────────
STRIP_PREFIXES = {
    'supreme','enhanced','primary','fundamental',"acolyte's",
    'raging',"warrior's","brawler's",'battle','combat','power',
    'vengeful','disciplined',"nature's",'primal','ferocious','wild',
    'plagued','sacred','blessed','holy','brutal','methodical',
    'trick','rugged','countering',
}

def base_skill_name(name: str) -> str:
    low = name.lower()
    words = low.split()
    if words and words[0] in STRIP_PREFIXES and len(words) > 1:
        return ' '.join(words[1:])
    return low

def skill_exists(name: str, cls: str) -> bool:
    base = base_skill_name(name)
    classes = real_skills.get(base, set())
    if not classes:
        return False  # not in game files = hallucinated
    return cls in classes

def aspect_exists(name: str) -> bool:
    if not name:
        return True
    low = name.lower()
    # "Aspect of X" → check "of x" and "x"
    low = re.sub(r'^aspect\s+', '', low).strip()
    return low in real_aspects or f'of {low}' in real_aspects

def clean_skills(skills: dict, cls: str) -> tuple[dict, list]:
    removed = []
    cleaned = {}
    for section, items in skills.items():
        if section == 'key_passive':
            cleaned[section] = items
            continue
        if section in ('skill_bar', 'enchantment_slots', 'specialization'):
            cleaned[section] = items
            continue
        if not isinstance(items, list):
            cleaned[section] = items
            continue
        kept = []
        for sk in items:
            if not isinstance(sk, dict) or 'name' not in sk:
                kept.append(sk)
                continue
            if 'PASSIVE' in sk.get('note', '').upper():
                kept.append(sk)  # passives: keep even if name unverified
                continue
            if skill_exists(sk['name'], cls):
                kept.append(sk)
            else:
                removed.append(f"{section}/{sk['name']}")
        if kept:
            cleaned[section] = kept
    return cleaned, removed

def clean_gear(gear: dict) -> tuple[dict, list]:
    nulled = []
    for slot, item in gear.items():
        if not isinstance(item, dict):
            continue
        asp = item.get('aspect')
        if asp and not aspect_exists(asp):
            item['aspect'] = None
            nulled.append(f"{slot}: {asp}")
    return gear, nulled

def clean_runewords(runewords: list) -> tuple[list, list]:
    bad = []
    kept = []
    for rw in runewords:
        ritual = rw.get('ritual', '').lower()
        invoke = rw.get('invocation', '').lower()
        r_ok = ritual in real_runes
        i_ok = invoke in real_runes
        if r_ok and i_ok:
            kept.append(rw)
        else:
            issues = []
            if not r_ok: issues.append(f"ritual={rw.get('ritual')}")
            if not i_ok: issues.append(f"invocation={rw.get('invocation')}")
            bad.append(', '.join(issues))
    return kept, bad

# ── efficiency score ───────────────────────────────────────────────────────

def rotation_score(build: dict) -> float:
    skills = build.get('skills', {})
    names = set()
    for section, items in skills.items():
        if not isinstance(items, list):
            continue
        for sk in items:
            if not isinstance(sk, dict) or 'name' not in sk:
                continue
            if 'PASSIVE' in sk.get('note', '').upper():
                continue
            names.add(base_skill_name(sk['name']))
    total = sum(dps_lookup.get(n, 0.0) for n in names)
    return round(total, 4)

# ── process each build ─────────────────────────────────────────────────────

build_scores = {}
for path in sorted(BUILDS_DIR.glob('*.json')):
    bid = path.stem
    build = json.load(open(path))
    cls = build.get('class', '')
    issues = []

    # skills
    build['skills'], removed_skills = clean_skills(build.get('skills', {}), cls)
    if removed_skills:
        issues.append(f"  removed skills: {removed_skills}")

    # gear
    build['gear'], nulled_aspects = clean_gear(build.get('gear', {}))
    if nulled_aspects:
        issues.append(f"  nulled aspects: {nulled_aspects}")

    # runewords
    build['runewords'], bad_runes = clean_runewords(build.get('runewords', []))
    if bad_runes:
        issues.append(f"  removed runewords: {bad_runes}")

    # also clean variant gear
    for v in build.get('variants', []):
        v['gear'], vn = clean_gear(v.get('gear', {}))

    # recalc score
    score = rotation_score(build)
    build['efficiency_score'] = score
    build_scores[bid] = score

    with open(path, 'w') as f:
        json.dump(build, f, indent=2)
        f.write('\n')

    status = 'CLEAN' if not issues else 'FIXED'
    print(f"[{status}] {bid}")
    for i in issues:
        print(i)

# ── update builds_index.json ───────────────────────────────────────────────

index = json.load(open(INDEX_PATH))
# Keep only builds that have a file and are not Paladin
index['builds'] = [b for b in index['builds'] if b['id'] in build_scores]

# Update scores and ranks
ranked = sorted(build_scores.items(), key=lambda x: -x[1])
ranks = {bid: i+1 for i, (bid, _) in enumerate(ranked)}

for b in index['builds']:
    bid = b['id']
    score = build_scores[bid]
    rank = ranks[bid]
    b['efficiency_score'] = score
    b['season_rank'] = rank
    # re-derive tier: quartile split over 24 builds = 6 per tier
    if rank <= 6:   b['tier'] = 'S'
    elif rank <= 12: b['tier'] = 'A'
    elif rank <= 18: b['tier'] = 'B'
    else:            b['tier'] = 'C'

index['builds'].sort(key=lambda b: b['season_rank'])
index['total_builds'] = len(index['builds'])

with open(INDEX_PATH, 'w') as f:
    json.dump(index, f, indent=2)
    f.write('\n')

print(f"\nbuilds_index.json updated: {len(index['builds'])} builds")
print("\nFinal ranking:")
for bid, score in ranked:
    b = next(b for b in index['builds'] if b['id'] == bid)
    print(f"  #{b['season_rank']:2d} {b['tier']}  {score:6.4f}  {bid}")
