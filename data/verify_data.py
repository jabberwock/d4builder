#!/usr/bin/env python3
"""
Regression test suite for d4_stats.db and derived JSON files.

Reads data/verify_data.yaml — a hand-curated YAML of known-correct values
for skill damage, cooldowns, affixes, passive effects, and overall counts.
Asserts each fixture against the live data and reports mismatches.

Exits with non-zero status on any failure so this can be wired into CI or
a pre-commit hook.

NO maxroll dependency. Reads only:
  - data/d4_stats.db
  - data/d4data_cooldowns.json
  - data/passive_table.json

Usage:
    python3 verify_data.py [--verbose]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# YAML loader: prefer pyyaml if available, fall back to a tiny inline parser
# so this works without dependencies.
try:
    import yaml
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

DATA = Path(__file__).parent
DB_PATH = DATA / "d4_stats.db"
COOLDOWNS_PATH = DATA / "d4data_cooldowns.json"
PASSIVES_PATH = DATA / "passive_table.json"
FIXTURES_PATH = DATA / "verify_data.yaml"


# Tolerances
TOL_DAMAGE_PCT = 0.01      # exact (DB stores fixed values)
TOL_COOLDOWN = 0.5         # rounding
TOL_AFFIX = 0.5            # rounding from random ranges
TOL_PASSIVE = 0.001        # floating point


# ─── YAML loader fallback ──────────────────────────────────────────────────

def _parse_yaml_fallback(path: Path) -> dict:
    """
    Tiny YAML subset parser. Handles:
      - top-level mappings
      - nested lists of inline `{...}` mappings
      - nested lists of multi-line block mappings
      - mappings with scalar values

    NOT a full YAML parser. Designed only for our verify_data.yaml shape.
    """
    with open(path) as f:
        text = f.read()

    result: dict = {}
    current_key: str | None = None
    current_list: list | None = None
    pending_block: dict | None = None
    pending_indent = 0

    def coerce(s: str):
        s = s.strip()
        if s.startswith("'") and s.endswith("'"):
            return s[1:-1]
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1]
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    def parse_inline_mapping(s: str) -> dict:
        # {a: 1, b: foo, c: 'with comma, here'}
        s = s.strip().lstrip("{").rstrip("}")
        out = {}
        depth = 0
        in_quote = None
        cur = ""
        parts = []
        for ch in s:
            if in_quote:
                cur += ch
                if ch == in_quote:
                    in_quote = None
            elif ch in ("'", '"'):
                in_quote = ch
                cur += ch
            elif ch in "{[":
                depth += 1
                cur += ch
            elif ch in "}]":
                depth -= 1
                cur += ch
            elif ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        if cur.strip():
            parts.append(cur)
        for p in parts:
            if ":" in p:
                k, v = p.split(":", 1)
                out[k.strip()] = coerce(v)
        return out

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        # Skip comments and blank lines
        stripped = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not stripped:
            i += 1
            continue

        indent = len(stripped) - len(stripped.lstrip())
        line = stripped.strip()

        if indent == 0 and line.endswith(":"):
            # Top-level section header
            current_key = line[:-1].strip()
            result[current_key] = None
            current_list = None
            pending_block = None
        elif indent == 0 and ":" in line:
            # Top-level scalar mapping (we don't have these, but support it)
            k, v = line.split(":", 1)
            result[k.strip()] = coerce(v)
            current_key = None
        elif current_key is not None and indent >= 2:
            if line.startswith("- "):
                rest = line[2:].strip()
                if result[current_key] is None:
                    result[current_key] = []
                    current_list = result[current_key]
                if rest.startswith("{") and rest.endswith("}"):
                    current_list.append(parse_inline_mapping(rest))
                    pending_block = None
                elif ":" in rest:
                    # Block mapping starts: "- key: value"
                    k, v = rest.split(":", 1)
                    pending_block = {k.strip(): coerce(v)}
                    pending_indent = indent + 2
                    current_list.append(pending_block)
                else:
                    current_list.append(coerce(rest))
                    pending_block = None
            elif pending_block is not None and indent >= pending_indent and ":" in line:
                k, v = line.split(":", 1)
                pending_block[k.strip()] = coerce(v)
            elif current_list is None and ":" in line:
                # mapping under top-level section without `- `
                k, v = line.split(":", 1)
                if result[current_key] is None:
                    result[current_key] = {}
                if isinstance(result[current_key], dict):
                    result[current_key][k.strip()] = coerce(v)
        i += 1

    return result


def load_fixtures() -> dict:
    if not FIXTURES_PATH.exists():
        raise FileNotFoundError(f"Missing fixtures: {FIXTURES_PATH}")
    if HAVE_YAML:
        with open(FIXTURES_PATH) as f:
            return yaml.safe_load(f)
    return _parse_yaml_fallback(FIXTURES_PATH)


# ─── Test runners ──────────────────────────────────────────────────────────


class Reporter:
    def __init__(self, verbose: bool = False) -> None:
        self.passed = 0
        self.failed: list[str] = []
        self.verbose = verbose

    def check(self, label: str, ok: bool, msg: str = "") -> None:
        if ok:
            self.passed += 1
            if self.verbose:
                print(f"  PASS {label}")
        else:
            self.failed.append(f"{label}: {msg}")
            print(f"  FAIL {label}: {msg}")

    def section(self, name: str, count: int) -> None:
        print(f"\n[{name}] {count} fixtures")

    def summary(self) -> int:
        total = self.passed + len(self.failed)
        print()
        if self.failed:
            print(f"FAIL  {self.passed}/{total} passed, {len(self.failed)} failed")
            return 1
        print(f"OK    {self.passed}/{total} passed")
        return 0


def check_skill_damage(conn: sqlite3.Connection, fixtures: list, r: Reporter) -> None:
    r.section("skill_damage", len(fixtures))
    for f in fixtures:
        pname = f.get("power_name")
        rank = f.get("rank", 5)
        expected = f.get("damage_pct")
        row = conn.execute(
            "SELECT damage_pct FROM skill_damage WHERE power_name = ? AND rank = ?",
            (pname, rank),
        ).fetchone()
        if row is None:
            r.check(f"{pname} rank{rank}", False, "row missing from skill_damage")
            continue
        actual = float(row[0])
        ok = abs(actual - expected) <= TOL_DAMAGE_PCT
        r.check(
            f"{pname} rank{rank}",
            ok,
            "" if ok else f"expected {expected}, got {actual}",
        )


def check_cooldowns(fixtures: list, r: Reporter) -> None:
    r.section("cooldowns", len(fixtures))
    if not COOLDOWNS_PATH.exists():
        r.check("d4data_cooldowns.json", False, "file missing")
        return
    with open(COOLDOWNS_PATH) as f:
        cds = json.load(f)
    for fixture in fixtures:
        pname = fixture.get("power_name")
        expected = fixture.get("seconds")
        actual = cds.get(pname)
        if actual is None:
            r.check(f"{pname}", False, f"missing from d4data_cooldowns.json (expected {expected}s)")
            continue
        ok = abs(float(actual) - expected) <= TOL_COOLDOWN
        r.check(
            f"{pname}",
            ok,
            "" if ok else f"expected {expected}s, got {actual}s",
        )


def check_affixes(conn: sqlite3.Connection, fixtures: list, r: Reporter) -> None:
    r.section("affixes", len(fixtures))
    for f in fixtures:
        name = f.get("internal_name")
        expected = f.get("max_value")
        row = conn.execute(
            "SELECT max_value FROM affixes WHERE internal_name = ?",
            (name,),
        ).fetchone()
        if row is None or row[0] is None:
            r.check(f"{name}", False, f"NULL or missing (expected {expected})")
            continue
        actual = float(row[0])
        ok = abs(actual - expected) <= TOL_AFFIX
        r.check(
            f"{name}",
            ok,
            "" if ok else f"expected {expected}%, got {actual}%",
        )


def check_passives(fixtures: list, r: Reporter) -> None:
    r.section("passive_effects", len(fixtures))
    if not PASSIVES_PATH.exists():
        r.check("passive_table.json", False, "file missing")
        return
    with open(PASSIVES_PATH) as f:
        pt = json.load(f)
    for f in fixtures:
        pname = f.get("power_name")
        expected_tag = f.get("require_tag")
        expected_value = f.get("expected_value")
        passive = pt.get(pname)
        if not passive:
            r.check(f"{f.get('name', pname)}", False, "passive missing from passive_table.json")
            continue
        matching = [
            e for e in passive.get("effects", [])
            if e.get("tag") == expected_tag
        ]
        if not matching:
            tags_present = sorted({e.get("tag") for e in passive.get("effects", [])})
            r.check(
                f"{f.get('name', pname)}",
                False,
                f"no effect with tag {expected_tag!r} (have: {tags_present})",
            )
            continue
        actual = matching[0].get("value")
        ok = abs(float(actual) - expected_value) <= TOL_PASSIVE
        r.check(
            f"{f.get('name', pname)} ({expected_tag})",
            ok,
            "" if ok else f"expected {expected_value}, got {actual}",
        )


def check_counts(conn: sqlite3.Connection, fixtures: dict, r: Reporter) -> None:
    r.section("counts", len(fixtures))
    queries = {
        "active_class_skills_min": (
            "SELECT COUNT(*) FROM skills WHERE class IN "
            "('Barbarian','Druid','Necromancer','Paladin','Rogue','Sorcerer','Spiritborn')"
            " AND is_passive = 0 AND display_name IS NOT NULL"
        ),
        "passives_total_min": None,  # special: read passive_table.json
        "paragon_glyphs_total": "SELECT COUNT(*) FROM paragon_glyphs",
        "paragon_boards_total": "SELECT COUNT(*) FROM paragon_boards",
        "paragon_nodes_total": "SELECT COUNT(*) FROM paragon_nodes",
        "unique_items_min": "SELECT COUNT(*) FROM items WHERE magic_type = 'unique'",
        "d4data_cooldowns_min": None,  # special
    }
    for key, expected in fixtures.items():
        if key == "passives_total_min":
            with open(PASSIVES_PATH) as f:
                actual = len(json.load(f))
            ok = actual >= expected
            r.check(key, ok, "" if ok else f"expected ≥{expected}, got {actual}")
            continue
        if key == "d4data_cooldowns_min":
            with open(COOLDOWNS_PATH) as f:
                actual = len(json.load(f))
            ok = actual >= expected
            r.check(key, ok, "" if ok else f"expected ≥{expected}, got {actual}")
            continue
        sql = queries.get(key)
        if not sql:
            r.check(key, False, "no query defined")
            continue
        actual = conn.execute(sql).fetchone()[0]
        if key.endswith("_min"):
            ok = actual >= expected
            r.check(key, ok, "" if ok else f"expected ≥{expected}, got {actual}")
        else:
            ok = actual == expected
            r.check(key, ok, "" if ok else f"expected ={expected}, got {actual}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"FATAL: {DB_PATH} not found")
        sys.exit(2)

    fixtures = load_fixtures()
    if not isinstance(fixtures, dict):
        print(f"FATAL: bad fixtures shape: {type(fixtures).__name__}")
        sys.exit(2)

    conn = sqlite3.connect(str(DB_PATH))
    r = Reporter(verbose=args.verbose)

    if "skill_damage" in fixtures:
        check_skill_damage(conn, fixtures["skill_damage"], r)
    if "cooldowns" in fixtures:
        check_cooldowns(fixtures["cooldowns"], r)
    if "affixes" in fixtures:
        check_affixes(conn, fixtures["affixes"], r)
    if "passive_effects" in fixtures:
        check_passives(fixtures["passive_effects"], r)
    if "counts" in fixtures:
        check_counts(conn, fixtures["counts"], r)

    conn.close()
    sys.exit(r.summary())


if __name__ == "__main__":
    main()
