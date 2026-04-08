#!/usr/bin/env python3
"""
build_db.py — Build d4_stats.db entirely from Maxroll game-data JSON.
Source: /tmp/maxroll_data.json
Every NULL means the value was not present in the source data.
"""

import json
import shutil
import sqlite3
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────
BASE       = Path(__file__).parent
SOURCE     = Path('/tmp/maxroll_data.json')
DB_PATH    = BASE / 'd4_stats.db'
DB_BACKUP  = BASE / 'd4_stats.db.bak'

# ── class index map ─────────────────────────────────────────────────────────
CLASS_INDEX = {
    0: 'Sorcerer',
    1: 'Druid',
    2: 'Barbarian',
    3: 'Rogue',
    4: 'Necromancer',
    5: 'Spiritborn',
    6: 'Paladin',
    7: 'Warlock',
}

# Skill key prefix → class name
SKILL_PREFIX_CLASS = {
    'Sorcerer':    'Sorcerer',
    'Druid':       'Druid',
    'Barbarian':   'Barbarian',
    'Rogue':       'Rogue',
    'Necromancer': 'Necromancer',
    'Spiritborn':  'Spiritborn',
    'Paladin':     'Paladin',
    'Warlock':     'Warlock',
}

# Paragon board prefix → class
PARAGON_CLASS = {
    'Paragon_Sorc':    'Sorcerer',
    'Paragon_Druid':   'Druid',
    'Paragon_Barb':    'Barbarian',
    'Paragon_Rogue':   'Rogue',
    'Paragon_Necro':   'Necromancer',
    'Paragon_Spirit':  'Spiritborn',
    'Paragon_Paladin': 'Paladin',
}

# Skip-pattern prefixes for affixes (test/old-season junk)
AFFIX_SKIP_PREFIXES = (
    'S01_', 'S02_', 'S03_', 'S04_', 'S05_', 'S06_', 'S07_', 'S08_', 'S09_',
    'S10_', 'TEST_', 'QA_', 'DEV_', 'TEMP_', 'Debug_', 'Debug',
)

# Skip items that are clearly test/placeholder
ITEM_SKIP_PREFIXES = (
    'TEST_', 'QA_', 'DEV_', 'TEMP_', 'Lewis', 'Debug',
    'Gold', 'Elixir_', 'Potion_', 'Quest', 'QST_',
)
ITEM_SKIP_TYPES = {'Gold', 'Quest', 'Elixir', 'Potion', 'Event', 'Mount'}

# ── schema ──────────────────────────────────────────────────────────────────
SCHEMA = """
DROP TABLE IF EXISTS skills;
DROP TABLE IF EXISTS skill_ranks;
DROP TABLE IF EXISTS skill_cooldowns;
DROP TABLE IF EXISTS affixes;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS paragon_boards;
DROP TABLE IF EXISTS paragon_nodes;
DROP TABLE IF EXISTS paragon_glyphs;
DROP TABLE IF EXISTS runes;
DROP TABLE IF EXISTS specializations;
DROP TABLE IF EXISTS tempering_recipes;
DROP TABLE IF EXISTS gems;
DROP TABLE IF EXISTS skill_damage;

CREATE TABLE skills (
    power_name    TEXT PRIMARY KEY,
    display_name  TEXT,
    class         TEXT,
    is_passive    INTEGER,
    skill_tags    TEXT,
    primary_tag   TEXT,
    description   TEXT
);

CREATE TABLE skill_ranks (
    table_id  INTEGER NOT NULL,
    rank      INTEGER NOT NULL,
    value     REAL NOT NULL
);

CREATE TABLE skill_cooldowns (
    power_name        TEXT PRIMARY KEY,
    cooldown_table_id INTEGER,
    cooldown_base     REAL,
    cooldown_formula  TEXT
);

CREATE TABLE affixes (
    internal_name  TEXT PRIMARY KEY,
    display_name   TEXT,
    class          TEXT,
    max_value      REAL,
    linked_skill   TEXT,
    affix_category TEXT
);

CREATE TABLE items (
    item_name      TEXT PRIMARY KEY,
    display_name   TEXT,
    item_type      TEXT,
    magic_type     TEXT,
    usable_by_class TEXT,
    affixes        TEXT
);

CREATE TABLE paragon_boards (
    board_name       TEXT PRIMARY KEY,
    class            TEXT,
    display_name     TEXT,
    node_count       INTEGER,
    socket_positions TEXT
);

CREATE TABLE paragon_nodes (
    node_name    TEXT PRIMARY KEY,
    node_type    TEXT,
    display_name TEXT,
    bonus_value  REAL,
    bonus_type   TEXT,
    skill_tags   TEXT
);

CREATE TABLE paragon_glyphs (
    glyph_name      TEXT PRIMARY KEY,
    display_name    TEXT,
    rarity          TEXT,
    usable_by_class TEXT,
    bonus_per_point REAL,
    radius_bonus    REAL,
    max_rank        INTEGER
);

CREATE TABLE runes (
    rune_name         TEXT PRIMARY KEY,
    display_name      TEXT,
    rune_type         TEXT,
    effect_description TEXT,
    runic_amount      REAL
);

CREATE TABLE specializations (
    class               TEXT,
    specialization_name TEXT,
    description         TEXT,
    mechanic_type       TEXT,
    required_skill_tags TEXT,
    generator_tags      TEXT,
    spender_tags        TEXT
);

CREATE TABLE tempering_recipes (
    recipe_name   TEXT PRIMARY KEY,
    display_name  TEXT,
    class         TEXT,
    item_type     TEXT,
    affix_key     TEXT,
    max_value     REAL,
    category      TEXT
);

CREATE TABLE gems (
    gem_name      TEXT PRIMARY KEY,
    display_name  TEXT,
    gem_type      TEXT,
    quality       TEXT,
    weapon_bonus  TEXT,
    armor_bonus   TEXT,
    jewelry_bonus TEXT
);

CREATE TABLE skill_damage (
    power_name  TEXT,
    rank        INTEGER,
    damage_pct  REAL,
    cooldown    REAL,
    PRIMARY KEY (power_name, rank)
);
"""

# ── helpers ─────────────────────────────────────────────────────────────────

def class_from_filter(class_filter):
    """Return comma-joined class names from a classFilter bool array, or None if all classes."""
    if not class_filter:
        return None
    names = [CLASS_INDEX[i] for i, v in enumerate(class_filter) if v and i in CLASS_INDEX]
    if len(names) == len([i for i in CLASS_INDEX if i < len(class_filter)]):
        return None  # all classes, no restriction
    if not names:
        return None
    return ','.join(names)


def class_from_skill_key(key):
    """Derive class from skill key prefix like 'Sorcerer_Spark'."""
    for prefix, cls in SKILL_PREFIX_CLASS.items():
        # Handle X1_ prefixed enchantments/variants
        stripped = key
        if key.startswith('X1_') or key.startswith('x1_'):
            stripped = key[3:]
        if stripped.startswith(prefix + '_') or stripped.startswith(prefix.lower() + '_'):
            return cls
    return 'Generic'


def class_from_board_name(board_name):
    """Derive class from paragon board name like 'Paragon_Sorc_00'."""
    for prefix, cls in PARAGON_CLASS.items():
        if board_name.startswith(prefix):
            return cls
    return None


def node_type_name(rarity):
    """Convert numeric rarity to node type string."""
    return {1: 'normal', 2: 'magic', 3: 'rare', 4: 'legendary', 5: 'socket'}.get(rarity, None)


def glyph_rarity_name(rarity):
    return {0: 'common', 1: 'rare', 2: 'legendary'}.get(rarity, None)


def magic_type_name(mt):
    return {0: 'normal', 1: 'magic', 2: 'unique', 3: 'test', 4: 'mythic'}.get(mt, None)


def affix_category_name(cat):
    return {
        0: 'general', 1: 'offensive', 2: 'defensive', 3: 'utility',
        4: 'resource', 5: 'legendary',
    }.get(cat, None)


def extract_table_id_from_cooldown(cooldown_val):
    """If cooldown formula references Table(N, ...), extract N as table_id."""
    import re
    if isinstance(cooldown_val, str):
        m = re.search(r'Table\((\d+),', cooldown_val)
        if m:
            return int(m.group(1))
    return None


# ── extractors ──────────────────────────────────────────────────────────────

def extract_skills(data):
    rows = []
    cooldown_rows = []
    skills = data.get('skills', {})

    for key, skill in skills.items():
        # Skip skills with no meaningful name for humans
        display_name = skill.get('name')

        cls = class_from_skill_key(key)

        # is_passive: field 'passive' is True for passives; also category 12 = enchantment passive
        is_passive = 1 if skill.get('passive') else 0

        tags_list = skill.get('tags', [])
        skill_tags = ','.join(tags_list) if tags_list else None

        # primary_tag: explicit 'primaryTag' field, or first tag starting with 'Skill_Primary_'
        primary_tag = skill.get('primaryTag')
        if not primary_tag:
            for t in tags_list:
                if t.startswith('Skill_Primary_'):
                    primary_tag = t
                    break

        description = skill.get('desc')

        rows.append((key, display_name, cls, is_passive, skill_tags, primary_tag, description))

        # Cooldown data
        cooldown_val = skill.get('cooldown')
        if cooldown_val is not None:
            if isinstance(cooldown_val, (int, float)):
                cooldown_rows.append((key, None, float(cooldown_val), None))
            else:
                # String formula
                table_id = extract_table_id_from_cooldown(cooldown_val)
                cooldown_rows.append((key, table_id, None, str(cooldown_val)))

    return rows, cooldown_rows


def extract_skill_ranks(data):
    """Store all powerTables as rank rows (table_id is 0-based index)."""
    rows = []
    power_tables = data.get('powerTables', [])
    for table_id, table in enumerate(power_tables):
        if not isinstance(table, list):
            continue
        for rank_idx, value in enumerate(table):
            if isinstance(value, (int, float)):
                rows.append((table_id, rank_idx + 1, float(value)))
    return rows


def extract_affixes(data):
    rows = []
    affixes = data.get('affixes', {})
    classes_data = data.get('classes', {})

    for key, affix in affixes.items():
        # Skip test/old-season affixes
        skip = False
        for prefix in AFFIX_SKIP_PREFIXES:
            if key.startswith(prefix):
                skip = True
                break
        if skip:
            continue

        display_name = affix.get('prefix') or affix.get('suffix') or affix.get('desc')
        if display_name and len(display_name) > 120:
            display_name = display_name[:120]

        # Class from classFilter array
        cf = affix.get('classFilter', [])
        cls_str = class_from_filter(cf)

        # If 'class' field is set (integer index), prefer that
        if 'class' in affix and isinstance(affix['class'], int):
            cls_str = CLASS_INDEX.get(affix['class'])

        # max_value: use maximumRank[2] (Sacred/Ancestral slot) as a proxy rank cap
        # The actual numeric range lives in attributeFormulas; store maximumRank[2] as max_value
        max_rank = affix.get('maximumRank', [])
        max_value = None
        if max_rank and len(max_rank) >= 3 and max_rank[2] not in (0, None):
            max_value = float(max_rank[2])

        # linked_skill: check if affix has a 'power' field (passive power)
        linked_skill = affix.get('power')

        affix_cat = affix_category_name(affix.get('category'))

        rows.append((key, display_name, cls_str, max_value, linked_skill, affix_cat))

    return rows


def extract_items(data):
    rows = []
    items = data.get('items', {})

    for key, item in items.items():
        # Skip test/placeholder/non-gear items
        skip = False
        for prefix in ITEM_SKIP_PREFIXES:
            if key.startswith(prefix):
                skip = True
                break
        if not skip:
            itype = item.get('type', '')
            for st in ITEM_SKIP_TYPES:
                if st in itype:
                    skip = True
                    break
        if skip:
            continue

        display_name = item.get('name')
        item_type = item.get('type')
        mt = magic_type_name(item.get('magicType', 0))

        cf = item.get('classFilter', [])
        usable = class_from_filter(cf)

        # affixes: 'explicits' for uniques/legendaries, 'implicits' for inherents
        affix_list = item.get('explicits', []) or item.get('implicits', [])
        affixes_str = json.dumps(affix_list) if affix_list else None

        rows.append((key, display_name, item_type, mt, usable, affixes_str))

    return rows


def extract_paragon_boards(data):
    rows = []
    boards = data.get('paragonBoards', {})
    classes_data = data.get('classes', {})

    # Build board → class map from classes data
    board_class_map = {}
    for idx, cls in classes_data.items():
        cls_name = cls.get('nameMale', CLASS_INDEX.get(int(idx)))
        for bname in cls.get('paragonBoards', []):
            board_class_map[bname] = cls_name

    for board_name, board in boards.items():
        cls = board_class_map.get(board_name) or class_from_board_name(board_name)

        nodes = board.get('nodes', [])
        non_null_nodes = [n for n in nodes if n is not None]
        node_count = len(non_null_nodes)

        # Socket positions: collect positions of 'Socket' nodes
        width = board.get('width', 21)
        socket_positions = []
        for idx, n in enumerate(nodes):
            if n and isinstance(n, str) and 'Socket' in n:
                row = idx // width
                col = idx % width
                socket_positions.append({'row': row, 'col': col, 'node': n})
        socket_pos_json = json.dumps(socket_positions) if socket_positions else None

        # Use board_name as display_name (no separate display field in data)
        display_name = board_name

        rows.append((board_name, cls, display_name, node_count, socket_pos_json))

    return rows


def extract_paragon_nodes(data):
    rows = []
    nodes = data.get('paragonNodes', {})

    for node_name, node in nodes.items():
        ntype = node_type_name(node.get('rarity'))
        display_name = node.get('name')

        # bonus_value / bonus_type from first attribute if available
        bonus_value = None
        bonus_type = None
        attrs = node.get('attributes', [])
        if attrs:
            first_attr = attrs[0]
            # The formula name gives the stat type
            bonus_type = first_attr.get('formula')
            # Static value if present
            if 'value' in first_attr:
                try:
                    bonus_value = float(first_attr['value'])
                except (TypeError, ValueError):
                    bonus_value = None

        tags_list = node.get('tags', [])
        skill_tags = ','.join(tags_list) if tags_list else None

        rows.append((node_name, ntype, display_name, bonus_value, bonus_type, skill_tags))

    return rows


def extract_paragon_glyphs(data):
    rows = []
    glyphs = data.get('paragonGlyphs', {})
    glyph_affixes = data.get('paragonGlyphAffixes', {})

    for glyph_name, glyph in glyphs.items():
        display_name = glyph.get('name')
        rarity = glyph_rarity_name(glyph.get('rarity'))

        cf = glyph.get('classFilter', [])
        usable = class_from_filter(cf)

        # bonus_per_point and radius_bonus: from first glyph affix's base/perLevel
        affix_keys = glyph.get('affixes', [])
        bonus_per_point = None
        radius_bonus = None

        for akey in affix_keys:
            aff = glyph_affixes.get(akey, {})
            base = aff.get('base')
            per_level = aff.get('perLevel')
            display_factor = aff.get('displayFactor', 1)
            req_rank = aff.get('requiredRank', 0)

            if base is not None and req_rank == 0:
                # This is the base bonus (active at rank 1)
                val = base / display_factor if display_factor else base
                if bonus_per_point is None:
                    bonus_per_point = float(val)
            elif base is not None and req_rank > 0:
                # This is the threshold/radius bonus
                val = base / display_factor if display_factor else base
                if radius_bonus is None:
                    radius_bonus = float(val)

        # max_rank: D4 glyphs max at rank 21
        max_rank = 21

        rows.append((glyph_name, display_name, rarity, usable, bonus_per_point, radius_bonus, max_rank))

    return rows


def extract_runes(data):
    rows = []
    items = data.get('items', {})

    for key, item in items.items():
        itype = item.get('type', '')
        if itype not in ('ConditionRune', 'EffectRune'):
            continue

        display_name = item.get('name')

        # rune_type: Condition → ritual, Effect → invocation
        rune_type = 'ritual' if itype == 'ConditionRune' else 'invocation'

        rune_data = item.get('rune', {})
        effect_desc = rune_data.get('desc')
        runic_amount = rune_data.get('value')
        if runic_amount is not None:
            try:
                runic_amount = float(runic_amount)
            except (TypeError, ValueError):
                runic_amount = None

        rows.append((key, display_name, rune_type, effect_desc, runic_amount))

    return rows


def extract_specializations(data):
    rows = []
    classes_data = data.get('classes', {})
    skills = data.get('skills', {})

    for idx, cls in classes_data.items():
        cls_name = cls.get('nameMale', CLASS_INDEX.get(int(idx), '?'))

        # ── Rogue: combo points / inner sight / preparation ──────────────────
        for passive_entry in cls.get('roguePassives', []):
            power_name = passive_entry.get('power', '')
            skill = skills.get(power_name, {})
            spec_name = skill.get('name')
            desc = skill.get('desc')
            if not spec_name:
                continue

            # mechanic_type from name
            mechanic = None
            name_lower = spec_name.lower()
            if 'combo' in name_lower:
                mechanic = 'combo_points'
            elif 'inner sight' in name_lower:
                mechanic = 'inner_sight'
            elif 'preparation' in name_lower:
                mechanic = 'preparation'

            rows.append((cls_name, spec_name, desc, mechanic, None, None, None))

        # ── Paladin: oaths ────────────────────────────────────────────────────
        for oath in cls.get('paladinOaths', []):
            spec_name = oath.get('name')
            desc = oath.get('desc')
            power = oath.get('power', '')
            if not spec_name:
                continue

            mechanic = None
            name_lower = spec_name.lower()
            if 'zealot' in name_lower:
                mechanic = 'zealot_stacks'
            elif 'juggernaut' in name_lower:
                mechanic = 'resolve_stacks'
            elif 'judicator' in name_lower:
                mechanic = 'judgement_marks'
            elif 'disciple' in name_lower:
                mechanic = 'arbiter_stacks'

            rows.append((cls_name, spec_name, desc, mechanic, None, None, None))

        # ── Druid: spirit boons ───────────────────────────────────────────────
        for spirit_group in cls.get('druidSpirits', []):
            if not isinstance(spirit_group, list):
                continue
            for entry in spirit_group:
                power_name = entry.get('power', '')
                skill = skills.get(power_name, {})
                spec_name = skill.get('name')
                desc = skill.get('desc')
                if not spec_name:
                    # Use power_name as fallback
                    spec_name = power_name
                mechanic = 'spirit_boon'
                rows.append((cls_name, spec_name, desc, mechanic, None, None, None))

        # ── Barbarian: weapon expertise ───────────────────────────────────────
        for we in cls.get('weaponExpertise', []):
            power_name = we.get('power', '')
            skill = skills.get(power_name, {})
            spec_name = skill.get('name') or f"Expertise: {we.get('itemType', power_name)}"
            desc = skill.get('desc')
            mechanic = 'weapon_expertise'
            rows.append((cls_name, spec_name, desc, mechanic, None, None, None))

        # ── Spiritborn: guardian spirits ──────────────────────────────────────
        for spirit in cls.get('spiritbornSpirits', []):
            primary_power = spirit.get('primary', '')
            primary_tag = spirit.get('primaryTag')
            skill = skills.get(primary_power, {})
            spec_name = skill.get('name') or primary_power
            desc = skill.get('desc')
            mechanic = 'guardian_spirit'
            rows.append((cls_name, spec_name, desc, mechanic, primary_tag, None, None))

        # ── Necromancer: minion types ──────────────────────────────────────────
        for minion in cls.get('minionPowers', []):
            spec_name = minion.get('name')
            power_name = minion.get('power', '')
            skill = skills.get(power_name, {})
            desc = skill.get('desc') or minion.get('desc')
            mechanic = 'book_of_the_dead'
            if spec_name:
                rows.append((cls_name, spec_name, desc, mechanic, None, None, None))

    return rows


# ── Gear item types considered valid for tempering slot descriptions ─────────
TEMPERING_GEAR_TYPES = {
    'Helm', 'ChestArmor', 'Legs', 'Boots', 'Gloves', 'Shield',
    'Ring', 'Amulet',
    'Sword', 'Sword2H', 'Axe', 'Axe2H', 'Mace', 'Mace2H', 'Mace2HDruid',
    'Staff', 'Scythe', 'Scythe2H', 'Dagger', 'DaggerOffHand', 'Polearm',
    'Wand', 'Bow', 'Crossbow', 'Crossbow2H', 'Focus', 'OffHandTotem',
    'Glaive', 'Quarterstaff', 'Flail', 'StaffDruid', 'StaffSorcerer',
    'FocusBookOffHand',
}

# Quality tiers for gems: suffix index → quality name
GEM_QUALITY = {
    '01': 'crude',
    '02': 'chipped',
    '03': 'normal',
    '04': 'flawless',
    '05': 'royal',
    '06': 'grand',
}

# socketedEffects type codes
GEM_SLOT_TYPE = {0: 'weapon', 1: 'armor', 2: 'jewelry'}


def _build_group_item_type_map(data):
    """Build {group_name: comma-joined item type string} from temperingGroups + itemTypes."""
    item_types_data = data.get('itemTypes', {})
    groups = data.get('temperingGroups', {})

    label_to_types = {}
    for type_name, type_data in item_types_data.items():
        if type_name not in TEMPERING_GEAR_TYPES:
            continue
        for label_id in type_data.get('itemLabels', []):
            label_to_types.setdefault(label_id, []).append(type_name)

    result = {}
    for grp_name, grp_data in groups.items():
        labels = grp_data.get('itemLabels', [])
        types_set = set()
        for lbl in labels:
            types_set.update(label_to_types.get(lbl, []))
        result[grp_name] = ','.join(sorted(types_set)) if types_set else None
    return result


def extract_tempering_recipes(data):
    """One row per recipe. affix_key = JSON array of highest-tier affix keys."""
    rows = []
    recipes = data.get('temperingRecipes', [])
    affixes = data.get('affixes', {})
    group_item_type = _build_group_item_type_map(data)

    for recipe in recipes:
        recipe_name = recipe.get('name')
        if not recipe_name:
            continue

        display_name = recipe_name  # no separate display field

        # Class from classFilter
        cf = recipe.get('classFilter', [])
        cls_str = class_from_filter(cf)

        # category = group field (Offensive / Defensive / Utility / etc.)
        category = recipe.get('group')

        # item_type from the group → item type mapping
        item_type_str = group_item_type.get(category) if category else None

        # affix_key: JSON array of the highest tier's affix keys
        tiers = recipe.get('tiers', [])
        highest_tier = tiers[-1] if tiers else []
        affix_key = json.dumps(highest_tier) if highest_tier else None

        # max_value: highest maximumRank[2] among the highest-tier affixes (use max)
        max_value = None
        for akey in highest_tier:
            aff = affixes.get(akey, {})
            max_rank = aff.get('maximumRank', [])
            if max_rank and len(max_rank) >= 3 and max_rank[2] not in (0, None):
                val = float(max_rank[2])
                if max_value is None or val > max_value:
                    max_value = val

        rows.append((recipe_name, display_name, cls_str, item_type_str, affix_key, max_value, category))

    return rows


def extract_gems(data):
    """Only store Royal (suffix _05) and Grand (_06) quality gems plus the Mythic gem.
    socketedEffects type 0=weapon, 1=armor, 2=jewelry.
    Store each bonus as 'attr_id:value' string.
    """
    rows = []
    items = data.get('items', {})

    for key, item in items.items():
        if item.get('type') != 'Gem':
            continue
        # Skip QA/TEST prefixes
        if key.startswith('QA_') or key.startswith('TEST_'):
            continue

        # Parse suffix: Gem_Ruby_05 → gem_type='ruby', quality='royal'
        parts = key.split('_')
        # Parts: ['Gem', type, suffix] — but Mythic is ['Gem', 'Mythic', '01']
        if len(parts) < 3:
            continue

        suffix = parts[-1]   # e.g. '05', '01'
        gem_type_raw = parts[1].lower()  # e.g. 'ruby', 'mythic'

        quality = GEM_QUALITY.get(suffix)
        if quality is None:
            continue  # unknown suffix

        # Only store Royal (_05), Grand (_06), and Mythic (any suffix, magicType=4)
        is_mythic = item.get('magicType') == 4
        if not is_mythic and quality not in ('royal', 'grand'):
            continue

        display_name = item.get('name')
        gem_type = gem_type_raw

        # Parse socketedEffects
        weapon_bonus = None
        armor_bonus = None
        jewelry_bonus = None
        for eff in item.get('socketedEffects', []):
            slot = eff.get('type')
            attrs = eff.get('attributes', [])
            if not attrs:
                continue
            # Encode as "attr_id:value" (take first attribute)
            attr = attrs[0]
            bonus_str = f"{attr.get('id')}:{attr.get('value')}"
            if slot == 0:
                weapon_bonus = bonus_str
            elif slot == 1:
                armor_bonus = bonus_str
            elif slot == 2:
                jewelry_bonus = bonus_str

        rows.append((key, display_name, gem_type, quality, weapon_bonus, armor_bonus, jewelry_bonus))

    return rows


def extract_skill_damage(data):
    """Resolve damage_pct per rank (1–7) for skills with simple Table(N, sLevel) formulas.
    Max paragon level = 300 (300 paragon points total to allocate).
    Skill ranks: 5 base points + 2 from gear = max rank 7.
    Only store rows where damage_pct can be resolved.
    """
    import re as _re

    rows = []
    skills = data.get('skills', {})
    power_tables = data.get('powerTables', [])

    # Build cooldown map: power_name → base cooldown (seconds) if scalar
    cooldown_map = {}
    for skey, skill in skills.items():
        cd = skill.get('cooldown')
        if isinstance(cd, (int, float)):
            cooldown_map[skey] = float(cd)

    def _resolve_coeff(scalar):
        """Extract a static multiplier coefficient for Table(N, sLevel) formulas.
        Handles: [coeff*]Table(N,sLevel)[*factor][/divisor]
        Returns (coeff, table_id) or None for formulas requiring runtime state.
        """
        s = scalar.strip()
        # Pattern: optional_coeff * Table(N, sLevel) * optional_factor / optional_divisor
        m = _re.match(
            r'^([\d.]+\*)?Table\((\d+),sLevel\)(\*([\d.]+))?(/( [\d.]+))?$',
            s,
        )
        if m:
            coeff = float(m.group(1).rstrip('*')) if m.group(1) else 1.0
            table_id = int(m.group(2))
            if m.group(4):
                coeff *= float(m.group(4))
            if m.group(6):
                coeff /= float(m.group(6).strip())
            return (coeff, table_id)
        # Simpler two-part: coeff*Table(N,sLevel)/divisor  (no *factor)
        m2 = _re.match(r'^([\d.]+)\*Table\((\d+),sLevel\)/([\d.]+)$', s)
        if m2:
            coeff = float(m2.group(1)) / float(m2.group(3))
            table_id = int(m2.group(2))
            return (coeff, table_id)
        # Plain: Table(N,sLevel)
        m3 = _re.match(r'^Table\((\d+),sLevel\)$', s)
        if m3:
            return (1.0, int(m3.group(1)))
        # coeff*Table(N,sLevel)*factor (no divisor)
        m4 = _re.match(r'^([\d.]+)\*Table\((\d+),sLevel\)\*([\d.]+)$', s)
        if m4:
            coeff = float(m4.group(1)) * float(m4.group(3))
            table_id = int(m4.group(2))
            return (coeff, table_id)
        return None

    # Process each skill: take the first payload with a resolvable scalar
    seen = set()
    for skey, skill in skills.items():
        if skey in seen:
            continue
        for payload in skill.get('payloads', []):
            dmg = payload.get('damage', {})
            if not dmg:
                continue
            scalar = dmg.get('scalar', '')
            if not isinstance(scalar, str):
                continue
            result = _resolve_coeff(scalar)
            if result is None:
                continue

            coeff, table_id = result
            if table_id >= len(power_tables):
                continue
            table = power_tables[table_id]
            if not table:
                continue

            seen.add(skey)
            cd_base = cooldown_map.get(skey)

            for rank in range(1, 8):  # ranks 1–7
                idx = rank - 1
                if idx >= len(table):
                    continue
                raw_val = table[idx]
                if not isinstance(raw_val, (int, float)):
                    continue
                damage_pct = round(coeff * raw_val * 100, 4)
                rows.append((skey, rank, damage_pct, cd_base))
            break  # one formula per skill

    return rows


# ── null rate reporter ───────────────────────────────────────────────────────

def null_rate(rows, col_names):
    if not rows:
        return {}
    results = {}
    for i, col in enumerate(col_names):
        nulls = sum(1 for r in rows if r[i] is None)
        results[col] = f'{nulls}/{len(rows)} ({100*nulls//len(rows)}%)'
    return results


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print(f'Reading {SOURCE}...')
    with open(SOURCE) as f:
        data = json.load(f)
    print(f"  version: {data.get('version', '?')}")

    # Backup existing DB
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, DB_BACKUP)
        print(f'Backed up existing DB to {DB_BACKUP}')

    # Connect and create schema
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript(SCHEMA)
    con.commit()
    print('Schema created.')

    # ── Extract data ──────────────────────────────────────────────────────────
    print('\nExtracting skills...')
    skill_rows, cooldown_rows = extract_skills(data)
    cur.executemany(
        'INSERT OR REPLACE INTO skills VALUES (?,?,?,?,?,?,?)',
        skill_rows
    )

    print('Extracting skill_ranks...')
    rank_rows = extract_skill_ranks(data)
    cur.executemany(
        'INSERT INTO skill_ranks VALUES (?,?,?)',
        rank_rows
    )

    print('Extracting skill_cooldowns...')
    cur.executemany(
        'INSERT OR REPLACE INTO skill_cooldowns VALUES (?,?,?,?)',
        cooldown_rows
    )

    print('Extracting affixes...')
    affix_rows = extract_affixes(data)
    cur.executemany(
        'INSERT OR REPLACE INTO affixes VALUES (?,?,?,?,?,?)',
        affix_rows
    )

    print('Extracting items...')
    item_rows = extract_items(data)
    cur.executemany(
        'INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)',
        item_rows
    )

    print('Extracting paragon_boards...')
    board_rows = extract_paragon_boards(data)
    cur.executemany(
        'INSERT OR REPLACE INTO paragon_boards VALUES (?,?,?,?,?)',
        board_rows
    )

    print('Extracting paragon_nodes...')
    node_rows = extract_paragon_nodes(data)
    cur.executemany(
        'INSERT OR REPLACE INTO paragon_nodes VALUES (?,?,?,?,?,?)',
        node_rows
    )

    print('Extracting paragon_glyphs...')
    glyph_rows = extract_paragon_glyphs(data)
    cur.executemany(
        'INSERT OR REPLACE INTO paragon_glyphs VALUES (?,?,?,?,?,?,?)',
        glyph_rows
    )

    print('Extracting runes...')
    rune_rows = extract_runes(data)
    cur.executemany(
        'INSERT OR REPLACE INTO runes VALUES (?,?,?,?,?)',
        rune_rows
    )

    print('Extracting specializations...')
    spec_rows = extract_specializations(data)
    cur.executemany(
        'INSERT INTO specializations VALUES (?,?,?,?,?,?,?)',
        spec_rows
    )

    print('Extracting tempering_recipes...')
    tempering_rows = extract_tempering_recipes(data)
    cur.executemany(
        'INSERT OR REPLACE INTO tempering_recipes VALUES (?,?,?,?,?,?,?)',
        tempering_rows
    )

    print('Extracting gems...')
    gem_rows = extract_gems(data)
    cur.executemany(
        'INSERT OR REPLACE INTO gems VALUES (?,?,?,?,?,?,?)',
        gem_rows
    )

    print('Extracting skill_damage...')
    skill_damage_rows = extract_skill_damage(data)
    cur.executemany(
        'INSERT OR REPLACE INTO skill_damage VALUES (?,?,?,?)',
        skill_damage_rows
    )

    con.commit()
    con.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print('\n' + '='*60)
    print('ROW COUNTS AND NULL RATES')
    print('='*60)

    tables = [
        ('skills',          skill_rows,
         ['power_name','display_name','class','is_passive','skill_tags','primary_tag','description']),
        ('skill_ranks',     rank_rows,
         ['table_id','rank','value']),
        ('skill_cooldowns', cooldown_rows,
         ['power_name','cooldown_table_id','cooldown_base','cooldown_formula']),
        ('affixes',         affix_rows,
         ['internal_name','display_name','class','max_value','linked_skill','affix_category']),
        ('items',           item_rows,
         ['item_name','display_name','item_type','magic_type','usable_by_class','affixes']),
        ('paragon_boards',  board_rows,
         ['board_name','class','display_name','node_count','socket_positions']),
        ('paragon_nodes',   node_rows,
         ['node_name','node_type','display_name','bonus_value','bonus_type','skill_tags']),
        ('paragon_glyphs',  glyph_rows,
         ['glyph_name','display_name','rarity','usable_by_class','bonus_per_point','radius_bonus','max_rank']),
        ('runes',           rune_rows,
         ['rune_name','display_name','rune_type','effect_description','runic_amount']),
        ('specializations', spec_rows,
         ['class','specialization_name','description','mechanic_type','required_skill_tags','generator_tags','spender_tags']),
        ('tempering_recipes', tempering_rows,
         ['recipe_name','display_name','class','item_type','affix_key','max_value','category']),
        ('gems', gem_rows,
         ['gem_name','display_name','gem_type','quality','weapon_bonus','armor_bonus','jewelry_bonus']),
        ('skill_damage', skill_damage_rows,
         ['power_name','rank','damage_pct','cooldown']),
    ]

    for tname, rows, cols in tables:
        print(f'\n{tname}: {len(rows)} rows')
        nr = null_rate(rows, cols)
        for col, rate in nr.items():
            if not rate.startswith('0/'):
                print(f'  NULL {col}: {rate}')

    print('\nDone. DB written to', DB_PATH)


if __name__ == '__main__':
    main()
