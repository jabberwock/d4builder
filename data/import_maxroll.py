#!/usr/bin/env python3
"""
Import high-priority datasets from /tmp/maxroll_data.json into d4_stats.db.

Imports:
  - power_tables: 37 scaling tables (Table(N, level) lookups for damage/cooldown scaling)
  - attribute_formulas: 890 affix value formulas at different item powers
  - paragon_glyph_affixes: 285 glyph effect definitions
  - paragon_thresholds: 49 paragon threshold bonus definitions
  - skill_tags: 441 keyword/tag definitions with descriptions
  - skill_categories: 5 skill bar slot categories
  - item_types: 140 item type definitions
  - tempering_groups: 6 tempering recipe groupings
  - level_scaling: 201 per-level scaling values

Also backfills missing items and affixes against existing tables.
"""

import json
import sqlite3
from pathlib import Path

MAXROLL = (lambda: __import__("_maxroll").MAXROLL_PATH)()
DB = Path(__file__).parent / "d4_stats.db"


def create_tables(conn: sqlite3.Connection) -> None:
    """Create tables for new maxroll datasets."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS power_tables (
            table_id INTEGER PRIMARY KEY,
            values_json TEXT NOT NULL,
            level_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attribute_formulas (
            attribute_name TEXT PRIMARY KEY,
            formulas_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paragon_glyph_affixes (
            affix_name TEXT PRIMARY KEY,
            id INTEGER,
            required_rank INTEGER,
            operation INTEGER,
            extra_data TEXT
        );

        CREATE TABLE IF NOT EXISTS paragon_thresholds (
            threshold_name TEXT PRIMARY KEY,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS skill_tags_data (
            tag_name TEXT PRIMARY KEY,
            display_name TEXT,
            description TEXT,
            types INTEGER
        );

        CREATE TABLE IF NOT EXISTS skill_categories_data (
            category_id TEXT PRIMARY KEY,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS item_types_data (
            type_name TEXT PRIMARY KEY,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS tempering_groups (
            group_name TEXT PRIMARY KEY,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS level_scaling (
            level INTEGER PRIMARY KEY,
            hp_scalar REAL,
            xp_scalar REAL,
            monster_dr REAL,
            power_base INTEGER,
            power_delta INTEGER,
            power_item INTEGER
        );

        CREATE TABLE IF NOT EXISTS mr_items_extra (
            item_name TEXT PRIMARY KEY,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS mr_affixes_extra (
            affix_name TEXT PRIMARY KEY,
            data_json TEXT
        );
    """)
    conn.commit()


def import_power_tables(conn: sqlite3.Connection, md: dict) -> int:
    """Import the 37 power scaling tables."""
    tables = md.get("powerTables", [])
    if not tables:
        return 0
    conn.execute("DELETE FROM power_tables")
    rows = []
    for tid, table in enumerate(tables):
        if table is None:
            continue
        rows.append((tid, json.dumps(table), len(table) if isinstance(table, list) else 0))
    conn.executemany(
        "INSERT INTO power_tables (table_id, values_json, level_count) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_attribute_formulas(conn: sqlite3.Connection, md: dict) -> int:
    """Import affix value formulas."""
    formulas = md.get("attributeFormulas", {})
    if not formulas:
        return 0
    conn.execute("DELETE FROM attribute_formulas")
    rows = []
    for name, formula_list in formulas.items():
        rows.append((name, json.dumps(formula_list)))
    conn.executemany(
        "INSERT INTO attribute_formulas (attribute_name, formulas_json) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_paragon_glyph_affixes(conn: sqlite3.Connection, md: dict) -> int:
    """Import paragon glyph affix definitions."""
    affixes = md.get("paragonGlyphAffixes", {})
    if not affixes:
        return 0
    conn.execute("DELETE FROM paragon_glyph_affixes")
    rows = []
    for name, data in affixes.items():
        if not isinstance(data, dict):
            continue
        rows.append((
            name,
            data.get("id"),
            data.get("requiredRank"),
            data.get("operation"),
            json.dumps({k: v for k, v in data.items() if k not in ("id", "requiredRank", "operation")}),
        ))
    conn.executemany(
        "INSERT INTO paragon_glyph_affixes (affix_name, id, required_rank, operation, extra_data) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_paragon_thresholds(conn: sqlite3.Connection, md: dict) -> int:
    """Import paragon threshold definitions."""
    thresholds = md.get("paragonThresholds", {})
    if not thresholds:
        return 0
    conn.execute("DELETE FROM paragon_thresholds")
    rows = [(name, json.dumps(data)) for name, data in thresholds.items()]
    conn.executemany(
        "INSERT INTO paragon_thresholds (threshold_name, data_json) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_skill_tags(conn: sqlite3.Connection, md: dict) -> int:
    """Import skill tag/keyword definitions."""
    tags = md.get("skillTags", {})
    if not tags:
        return 0
    conn.execute("DELETE FROM skill_tags_data")
    rows = []
    for name, data in tags.items():
        if not isinstance(data, dict):
            continue
        rows.append((
            name,
            data.get("name") or "",
            data.get("desc") or "",
            data.get("types"),
        ))
    conn.executemany(
        "INSERT INTO skill_tags_data (tag_name, display_name, description, types) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_skill_categories(conn: sqlite3.Connection, md: dict) -> int:
    """Import skill bar slot categories."""
    cats = md.get("skillCategories", {})
    if not cats:
        return 0
    conn.execute("DELETE FROM skill_categories_data")
    rows = [(str(k), json.dumps(v)) for k, v in cats.items()]
    conn.executemany(
        "INSERT INTO skill_categories_data (category_id, data_json) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_item_types(conn: sqlite3.Connection, md: dict) -> int:
    """Import item type definitions."""
    types = md.get("itemTypes", {})
    if not types:
        return 0
    conn.execute("DELETE FROM item_types_data")
    rows = [(name, json.dumps(data)) for name, data in types.items()]
    conn.executemany(
        "INSERT INTO item_types_data (type_name, data_json) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_tempering_groups(conn: sqlite3.Connection, md: dict) -> int:
    """Import tempering recipe groupings."""
    groups = md.get("temperingGroups", {})
    if not groups:
        return 0
    conn.execute("DELETE FROM tempering_groups")
    rows = [(name, json.dumps(data)) for name, data in groups.items()]
    conn.executemany(
        "INSERT INTO tempering_groups (group_name, data_json) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_level_scaling(conn: sqlite3.Connection, md: dict) -> int:
    """Import per-level scaling values."""
    scaling = md.get("levelScaling", [])
    if not scaling:
        return 0
    conn.execute("DELETE FROM level_scaling")
    rows = []
    for level, data in enumerate(scaling):
        if data is None or not isinstance(data, dict):
            continue
        rows.append((
            level,
            data.get("hpScalar"),
            data.get("xpScalar"),
            data.get("monsterDr"),
            data.get("powerBase"),
            data.get("powerDelta"),
            data.get("powerItem"),
        ))
    conn.executemany(
        "INSERT INTO level_scaling (level, hp_scalar, xp_scalar, monster_dr, "
        "power_base, power_delta, power_item) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_extra_items(conn: sqlite3.Connection, md: dict) -> int:
    """Backfill items not present in our items table."""
    items = md.get("items", {})
    if not items:
        return 0
    existing = set(
        row[0] for row in conn.execute("SELECT item_name FROM items").fetchall()
    )
    conn.execute("DELETE FROM mr_items_extra")
    rows = []
    for name, data in items.items():
        if name not in existing:
            rows.append((name, json.dumps(data)))
    conn.executemany(
        "INSERT INTO mr_items_extra (item_name, data_json) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def import_extra_affixes(conn: sqlite3.Connection, md: dict) -> int:
    """Backfill affixes not present in our affixes table."""
    affixes = md.get("affixes", {})
    if not affixes:
        return 0
    existing = set(
        row[0] for row in conn.execute("SELECT internal_name FROM affixes").fetchall()
        if row[0]
    )
    conn.execute("DELETE FROM mr_affixes_extra")
    rows = []
    for name, data in affixes.items():
        if name not in existing:
            rows.append((name, json.dumps(data)))
    conn.executemany(
        "INSERT INTO mr_affixes_extra (affix_name, data_json) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def main() -> None:
    if not MAXROLL.exists():
        print(f"ERROR: {MAXROLL} not found")
        return

    print(f"Loading {MAXROLL}...")
    with open(MAXROLL) as f:
        md = json.load(f)

    print(f"Opening {DB}...")
    conn = sqlite3.connect(str(DB))
    create_tables(conn)

    print("\nImporting datasets:")
    importers = [
        ("power_tables", import_power_tables),
        ("attribute_formulas", import_attribute_formulas),
        ("paragon_glyph_affixes", import_paragon_glyph_affixes),
        ("paragon_thresholds", import_paragon_thresholds),
        ("skill_tags_data", import_skill_tags),
        ("skill_categories_data", import_skill_categories),
        ("item_types_data", import_item_types),
        ("tempering_groups", import_tempering_groups),
        ("level_scaling", import_level_scaling),
        ("mr_items_extra (backfill)", import_extra_items),
        ("mr_affixes_extra (backfill)", import_extra_affixes),
    ]

    for label, importer in importers:
        try:
            count = importer(conn, md)
            print(f"  {label:35s} {count:>6d} rows")
        except Exception as e:
            print(f"  {label:35s} ERROR: {e}")

    conn.close()
    print("\nImport complete.")


if __name__ == "__main__":
    main()
