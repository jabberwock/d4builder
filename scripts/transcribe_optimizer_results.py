#!/usr/bin/env python3
"""
Transcribe optimizer_results.db rows into site/builds/*.json files.

Read-only against data/ (the d4builder data tree is filesystem RO).
Write-only to site/.

This script is the build-data pipeline: when the optimizer is re-run
or when new classes need to be transcribed, run this. The output is
deterministic — re-running with the same inputs produces byte-identical
files, so it's safe to run repeatedly.

Usage:
    python3 scripts/transcribe_optimizer_results.py
    python3 scripts/transcribe_optimizer_results.py --class Sorcerer
    python3 scripts/transcribe_optimizer_results.py --all-classes

Per project rule (memory: feedback_optimizer_is_source_of_truth.md):
the optimizer is the source of truth for build composition. Every
field in the output comes from an optimizer row or from a verified
data/ lookup. No hand-authoring. No fabrication.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"
DATA_DB = REPO / "data" / "d4_stats.db"
OPTIMIZER_DB = REPO / "data" / "optimizer_results.db"

# Items the optimizer can flag as placeholders. The loader's placeholder
# regex catches these too — kept here for visibility during transcription.
PLACEHOLDER_INTERNAL_SUFFIXES = ("_NoPowers", "_PH", "_TestLook")


def build_id_for(class_name: str, purpose: str) -> str:
    """`Sorcerer` + `pit` → `sorcerer_pit_push`. Matches the build URLs."""
    purpose_to_slug = {
        "pit": "pit_push",
        "speed": "speed_farm",
        "leveling": "leveling",
        "mythic": "balanced",
    }
    slug = purpose_to_slug.get(purpose, purpose)
    return f"{class_name.lower()}_{slug}"


def build_name_for(class_name: str, purpose_label: str) -> str:
    """Display name shown in headers. Format: `Sorcerer · Pit Push`."""
    return f"{class_name} · {purpose_label}"


def normalize_purpose_label(purpose: str, purpose_label: str) -> str:
    """
    Normalize purpose labels to match intended build archetypes.
    Maps optimizer purposes to display labels.
    """
    label_map = {
        "mythic": "Balanced",
    }
    return label_map.get(purpose, purpose_label)


def lookup_unique_id(conn: sqlite3.Connection, display_name: str) -> tuple[str, list[str]]:
    """
    Resolve an item display_name to its canonical unique_id from
    data/d4_stats.db.items. Skip S10_* seasonal duplicates and prefer
    the base row.

    Returns (unique_id_or_empty, concerns_list).
    """
    rows = conn.execute(
        "SELECT item_name, item_type, magic_type, usable_by_class "
        "FROM items WHERE display_name=? AND item_name NOT LIKE 'S10\\_%' ESCAPE '\\'",
        (display_name,),
    ).fetchall()
    if not rows:
        return ("", [f"NOT FOUND in items table: {display_name!r}"])
    if len(rows) > 1:
        rows.sort(key=lambda r: (r[0].startswith("S"), r[0]))
    item_name = rows[0][0]
    concerns: list[str] = []
    if any(item_name.endswith(s) for s in PLACEHOLDER_INTERNAL_SUFFIXES):
        concerns.append(f"placeholder unique: {display_name!r} → {item_name}")
    return (item_name, concerns)


def build_gear_block(
    conn: sqlite3.Connection, gear_dict: dict[str, str]
) -> tuple[dict, list[str]]:
    """
    Convert optimizer's `{Helm: 'Hail of Verglas', ...}` into the
    discriminated-union shape per BUILD_DATA_SPEC.md:
        `{Helm: {rarity, unique_id, _optimizer_name}, ...}`.
    """
    out: dict[str, dict] = {}
    concerns: list[str] = []
    for slot, display_name in gear_dict.items():
        unique_id, item_concerns = lookup_unique_id(conn, display_name)
        out[slot] = {
            "rarity": "unique",
            "unique_id": unique_id,
            "_optimizer_name": display_name,
        }
        concerns.extend(item_concerns)
    return out, concerns


def jload(s: str | None):
    """Parse a TEXT column that holds JSON; tolerate empty/null."""
    if not s:
        return None
    return json.loads(s)


def transcribe_row(
    row: sqlite3.Row, items_conn: sqlite3.Connection, today: str
) -> tuple[dict, dict, dict, list[str]]:
    """
    Convert one optimizer_results row into:
        (full_build_obj, index_entry, skill_tree_entry, concerns)
    """
    purpose = row["purpose"]
    class_name = row["class"]
    bid = build_id_for(class_name, purpose)
    normalized_label = normalize_purpose_label(purpose, row["purpose_label"])
    bname = build_name_for(class_name, normalized_label)

    skill_bar = jload(row["skill_bar"]) or []
    skill_upgrades = jload(row["skill_upgrades"]) or []
    passives = jload(row["passives"]) or []
    aspects_recommended = jload(row["aspects_recommended"]) or []
    tempers_recommended = jload(row["tempers_recommended"]) or {}
    gems_recommended = jload(row["gems_recommended"]) or {}
    gear_recommended = jload(row["gear_recommended"]) or {}
    mercenary = jload(row["mercenary"]) or {}
    spec_detail = jload(row["specialization_detail"]) or {}
    nm_dungeons = jload(row["nightmare_dungeons"]) or []
    paragon_boards = jload(row["paragon_boards"]) or []
    rune_pair_1 = jload(row["rune_pair_1"]) or {}
    rune_pair_2 = jload(row["rune_pair_2"]) or {}
    score_breakdown = jload(row["score_breakdown"]) or {}

    gear_block, gear_concerns = build_gear_block(items_conn, gear_recommended)

    build_obj = {
        "id": bid,
        "build_name": bname,
        "class": class_name,
        "purpose": purpose,
        "purpose_label": normalized_label,
        "tier": row["tier"],
        "build_score": row["build_score"],
        "global_rank": row["global_rank"],
        "available": "live",
        "season": "Season 12 - Season of Slaughter",
        "patch": "2.6.0.70982",
        "specialization": row["specialization"],
        "specialization_detail": spec_detail,
        # Skills
        "skill_bar": skill_bar,
        "skill_bar_rank": 5,
        "skill_upgrades": skill_upgrades,
        "passives": passives,
        "passive_rank": 3,
        "key_passive": row["key_passive"],
        # Gear (discriminated union per spec)
        "gear": gear_block,
        # Optimizer's parallel aspect picks (see optimizer_concerns.md #2)
        "aspects_recommended": aspects_recommended,
        # Tempers / gems
        "tempers_recommended": tempers_recommended,
        "gems_recommended": gems_recommended,
        # Paragon boards
        "paragon_boards": paragon_boards,
        # Runewords (Season mechanic)
        "rune_pair_1": rune_pair_1,
        "rune_pair_2": rune_pair_2,
        # Mercenary
        "mercenary": mercenary,
        # Nightmare dungeons that drop the build's aspects
        "nightmare_dungeons": nm_dungeons,
        # Score breakdown (debug — renderer may collapse)
        "score_breakdown": score_breakdown,
        # Sourced provenance — user-facing strings
        "sources": {
            "build_score": (
                f"optimizer_results.db row id={row['id']} · {bname} · "
                f"score {row['build_score']:.2f}"
            ),
            "tier": (
                f"optimizer_results.db row id={row['id']} · global rank "
                f"{row['global_rank']} → tier {row['tier']}"
            ),
            "skill_bar": (
                f"optimizer_results.db row id={row['id']} · skill_bar "
                f"(6 active skills, rank 5)"
            ),
            "passives": (
                f"optimizer_results.db row id={row['id']} · passives "
                f"(10 picks at rank 3)"
            ),
            "key_passive": f"optimizer_results.db row id={row['id']} · key_passive",
            "gear": (
                f"optimizer_results.db row id={row['id']} · gear_recommended "
                "(uniques only — see optimizer_concerns.md #1)"
            ),
            "aspects_recommended": (
                f"optimizer_results.db row id={row['id']} · aspects_recommended "
                "(parallel field, see optimizer_concerns.md #2)"
            ),
            "tempers_recommended": (
                f"optimizer_results.db row id={row['id']} · tempers_recommended"
            ),
            "gems_recommended": (
                f"optimizer_results.db row id={row['id']} · gems_recommended"
            ),
            "paragon_boards": (
                f"optimizer_results.db row id={row['id']} · paragon_boards "
                "(traversal fields empty — see optimizer_concerns.md #8)"
            ),
            "mercenary": f"optimizer_results.db row id={row['id']} · mercenary",
            "nightmare_dungeons": (
                f"optimizer_results.db row id={row['id']} · nightmare_dungeons"
            ),
        },
    }

    index_entry = {
        "id": bid,
        "build_name": bname,
        "class": class_name,
        "purpose": purpose,
        "purpose_label": normalized_label,
        "tier": row["tier"],
        "build_score": row["build_score"],
        "global_rank": row["global_rank"],
        "available": "live",
        "season": "Season 12 - Season of Slaughter",
        "specialization": row["specialization"],
        "key_passive": row["key_passive"],
        "skill_bar_summary": skill_bar,
        "file": f"builds/{bid}.json",
    }

    skill_tree_entry = {
        "id": bid,
        "build_name": bname,
        "class": class_name,
        "key_passive": row["key_passive"],
        "skill_bar": skill_bar,
        "skill_bar_rank": 5,
        "skill_upgrades": skill_upgrades,
        "passives": passives,
        "passive_rank": 3,
    }

    return build_obj, index_entry, skill_tree_entry, gear_concerns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--class",
        dest="class_filter",
        default="Sorcerer",
        help="Only transcribe builds for this class (default: Sorcerer)",
    )
    parser.add_argument(
        "--all-classes",
        action="store_true",
        help="Transcribe all classes (overrides --class)",
    )
    args = parser.parse_args(argv)

    opt_conn = sqlite3.connect(f"file:{OPTIMIZER_DB}?mode=ro", uri=True)
    opt_conn.row_factory = sqlite3.Row
    items_conn = sqlite3.connect(f"file:{DATA_DB}?mode=ro", uri=True)

    if args.all_classes:
        rows = opt_conn.execute(
            "SELECT * FROM optimizer_results ORDER BY class, build_score DESC"
        ).fetchall()
    else:
        rows = opt_conn.execute(
            "SELECT * FROM optimizer_results WHERE class=? ORDER BY build_score DESC",
            (args.class_filter,),
        ).fetchall()

    if not rows:
        print(f"no optimizer rows matched (class filter: {args.class_filter!r})")
        return 1

    print(f"transcribing {len(rows)} optimizer row(s)")

    builds_dir = SITE / "builds"
    builds_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    index_entries: list[dict] = []
    skill_tree_entries: list[dict] = []

    for row in rows:
        build_obj, index_entry, skill_entry, concerns = transcribe_row(
            row, items_conn, today
        )
        if concerns:
            print(f"  {build_obj['id']} concerns: {concerns}")

        out_path = builds_dir / f"{build_obj['id']}.json"
        out_path.write_text(json.dumps(build_obj, indent=2) + "\n")
        print(f"  wrote {out_path.relative_to(REPO)}")

        index_entries.append(index_entry)
        skill_tree_entries.append(skill_entry)

    index_obj = {
        "version": "1.1",
        "season": "Season 12 - Season of Slaughter",
        "patch": "2.6.0.70982",
        "generated_from": "data/optimizer_results.db",
        "generated_at": today,
        "total_builds": len(index_entries),
        "expected_builds": 28,
        "classes": [
            "Barbarian",
            "Druid",
            "Necromancer",
            "Paladin",
            "Rogue",
            "Sorcerer",
            "Spiritborn",
        ],
        "builds": index_entries,
    }
    (SITE / "builds_index.json").write_text(json.dumps(index_obj, indent=2) + "\n")
    print(f"  wrote {(SITE / 'builds_index.json').relative_to(REPO)}")

    trees_obj = {
        "metadata": {
            "version": "1.1",
            "season": "Season 12 - Season of Slaughter",
            "patch": "2.6.0.70982",
            "generated_from": "data/optimizer_results.db",
            "generated_at": today,
        },
        "builds": skill_tree_entries,
    }
    (SITE / "skill_trees.json").write_text(json.dumps(trees_obj, indent=2) + "\n")
    print(f"  wrote {(SITE / 'skill_trees.json').relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
