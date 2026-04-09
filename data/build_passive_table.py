#!/usr/bin/env python3
"""
Build the authoritative passive effects table by combining:
  1. d4data SF values (from temp/d4data/json/base/meta/Power/*.pow.json)
     → AUTHORITATIVE numeric values
  2. Maxroll skill descriptions (from /tmp/maxroll_data.json)
     → semantic context for each value (damage_mult, attack_speed, etc.)

Output: data/passive_table.json
  {
    "Sorcerer_Talent_Cold_T3_N1": {
      "name": "Shatter",
      "effects": [
        {
          "tag": "damage_proc",
          "value": 0.45,
          "type": "percent",
          "context": "After Freeze expires, enemies explode for X of the damage..."
        }
      ]
    }
  }
"""

import json
import re
from pathlib import Path

MAXROLL = (lambda: __import__("_maxroll").MAXROLL_PATH)()
POWER_DIR = Path("/Users/michael/code/d4builder/temp/d4data/json/base/meta/Power")
PASSIVE_D4DATA = Path(__file__).parent / "passive_effects_d4data.json"
OUT = Path(__file__).parent / "passive_table.json"


def strip_markup_keep_brackets(s: str) -> str:
    """Strip {tags} but keep [expr|format|] and \\[x\\]/\\[+\\] markers visible."""
    if not s:
        return ""
    s = re.sub(r"\{[^}]*\}", "", s)
    s = s.replace("\\[", "[").replace("\\]", "]")
    return s.replace("\r", "").replace("\n", " ").strip()


# Format hint → semantic type mapping
FORMAT_TO_TYPE = {
    "%": "percent",
    "%x": "multiplicative",
    "%+": "additive",
    "1%x": "multiplicative",
    "1%+": "additive",
    "2?": "duration_or_value",
    "1": "integer",
}


def parse_format_hint(fmt: str) -> str:
    """Convert a Maxroll format hint to a semantic type."""
    fmt = fmt.strip()
    if not fmt:
        return "value"
    return FORMAT_TO_TYPE.get(fmt, fmt)


def extract_leading_number(expr: str) -> float | None:
    """
    Pull the leading numeric constant from a formula expression.
    e.g. '(0.45+Sorc_Shatter_Damage)*100' → 0.45
         'Min(1.4, Max((Resource_Cur(6))*0.007,0))*100' → 1.4

    Skips floor sentinels in Max(N, ...) and Min(N, ...) where N is a small
    integer used as a clamp (e.g. Max(1, expr) means "at least 1").
    """
    # Strip leading Max(N,...) / Min(N,...) floor sentinels by removing the
    # leading number-then-comma pattern inside Max/Min calls.
    # e.g. "Max(1, PlayerHealthMax()*0.05)" → look past the "1, " floor.
    cleaned = re.sub(r"\b(?:Max|Min)\(\s*\d+(?:\.\d+)?\s*,\s*", "(", expr)
    m = re.search(r"(\d+\.?\d*)", cleaned)
    if m:
        return float(m.group(1))
    # Fallback: original expression
    m = re.search(r"(\d+\.?\d*)", expr)
    if m:
        return float(m.group(1))
    return None


def extract_inline_value(token_match: re.Match, full_desc: str) -> dict | None:
    """
    Extract value + semantic tag from a [expr|format|] token, using surrounding
    text for semantic context.
    """
    expr = token_match.group(1) or ""
    fmt = token_match.group(2) or ""

    leading = extract_leading_number(expr)
    if leading is None:
        return None

    # If expression has *100, the leading is a fraction → multiply
    multiplier = 1.0
    if "*100" in expr or "* 100" in expr:
        multiplier = 100.0

    raw_value = leading * multiplier

    # Try forward context first; fall back to backward, then wide bidirectional.
    fmt_type = parse_format_hint(fmt)
    context = _forward_context(full_desc, token_match.end())
    tag = _infer_tag(context, fmt_type)
    if tag == "unknown":
        back = _backward_context(full_desc, token_match.start())
        if back:
            tag = _infer_tag(back, fmt_type)
    if tag == "unknown":
        # Last resort: wide bidirectional 60-char window (legacy behavior)
        wide = full_desc[max(0, token_match.start() - 60):
                         min(len(full_desc), token_match.end() + 60)].lower()
        tag = _infer_tag(wide, fmt_type)

    # Convert to a normalized 0-1 fraction for percent values
    value = raw_value / 100.0 if "%" in fmt or fmt_type in ("percent", "multiplicative", "additive") else raw_value

    return {
        "tag": tag,
        "value": value,
        "raw_value": raw_value,
        "format": fmt,
        "type": fmt_type,
        "expr": expr[:80],
    }


def extract_hardcoded_value(match: re.Match, full_desc: str) -> dict | None:
    """Extract value from a hardcoded `N%[x]` or `N%[+]` pattern."""
    raw_value = float(match.group(1))
    fmt_marker = match.group(2)  # 'x' or '+'
    fmt_type = "multiplicative" if fmt_marker == "x" else "additive"

    # Forward context first, backward fallback, wide bidirectional last.
    context = _forward_context(full_desc, match.end())
    tag = _infer_tag(context, fmt_type)
    if tag == "unknown":
        back = _backward_context(full_desc, match.start())
        if back:
            tag = _infer_tag(back, fmt_type)
    if tag == "unknown":
        wide = full_desc[max(0, match.start() - 60):
                         min(len(full_desc), match.end() + 60)].lower()
        tag = _infer_tag(wide, fmt_type)

    return {
        "tag": tag,
        "value": raw_value / 100.0,
        "raw_value": raw_value,
        "type": fmt_type,
        "format": f"%[{fmt_marker}]",
    }


def _forward_context(desc: str, start_pos: int, max_len: int = 80) -> str:
    """
    Get the text immediately after a value, bounded by the NEXT value marker
    or hard clause break. The descriptor for a value lives between that value
    and the next %, [x], [+], or sentence break:
        "6%[+] Resource Cost Reduction and 12%[x] increased damage"
                                          ^stop here (next value)^
    """
    end = min(len(desc), start_pos + max_len)
    snippet = desc[start_pos:end]
    # Stop at the next value marker — this is the strongest clause boundary
    next_value = re.search(r"\d+%\s*\[[x+]\]|\d+\s*%(?!\w)|\.|;", snippet)
    if next_value:
        snippet = snippet[:next_value.start()]
    return snippet.lower()


def _backward_context(desc: str, end_pos: int, max_len: int = 50) -> str:
    """Fallback: text immediately before a value (e.g. for tail-position values)."""
    start = max(0, end_pos - max_len)
    snippet = desc[start:end_pos]
    # Find the previous clause break and start from there
    for sep in [". ", "; ", ", ", " and "]:
        idx = snippet.rfind(sep)
        if idx >= 0:
            snippet = snippet[idx + len(sep):]
            break
    return snippet.lower()


def _infer_tag(context: str, fmt_type: str) -> str:
    """Determine semantic tag from surrounding context words.

    Order matters: more-specific compound phrases must be checked BEFORE the
    generic "damage" fallback, otherwise nearby damage clauses pollute tags
    for things like resource_cost_reduction.
    """
    # === Specific compound phrases (must come before generic damage) ===
    if "resource cost reduction" in context:
        return "resource_cost_reduction"
    if "cooldown reduction" in context:
        return "cooldown_reduction"
    if "damage reduction" in context:
        return "damage_reduction"
    # Resource cost / mana cost reduction (Avalanche: "less Mana", Soulfire: "less Mana")
    if re.search(r"less\s+(mana|fury|spirit|essence|energy|faith|vigor)", context):
        return "mana_cost_reduction"
    if "summon damage" in context or "minion damage" in context:
        return "damage_mult_summon" if fmt_type == "multiplicative" else "damage_summon"
    # Healing received (Mending: "additional Healing")
    if (
        "additional healing" in context
        or "healing received" in context
        or "increased healing" in context
        or re.search(r"healing\s+from", context)
    ):
        return "healing_received"
    # Maximum resource (Devastation: "Maximum Mana is increased",
    # Heart of the Wild: "Maximum Spirit is increased")
    if re.search(r"maximum\s+(mana|fury|spirit|essence|energy|faith|vigor)", context):
        return "resource_max"
    # Slow application (Cold Front: "more Chill", Neurotoxin: "slowed by")
    if "more chill" in context or "apply" in context and "chill" in context:
        return "applies_chill"
    if re.search(r"slowed?\s+by", context):
        return "slow_pct"
    # Damage proc on its own damage (Sorcerer Enchantments: "of its damage")
    if "of its damage" in context:
        return "damage_proc"

    # === Damage variants ===
    if "damage to" in context:
        m = re.search(r"damage to (\w+)", context)
        if m:
            target = m.group(1).rstrip(".,?")
            return f"damage_mult_vs_{target}" if fmt_type == "multiplicative" else f"damage_vs_{target}"
    # "more damage" / "increased damage" — also catch "more <element> damage"
    # (e.g. "more Earth and Storm damage", "more Fire damage", "more Bone damage")
    if (
        "more damage" in context
        or "increased damage" in context
        or "damage you" in context
        or re.search(r"more\s+(?:\w+\s+(?:and\s+\w+\s+)?){1,3}damage", context)
        or re.search(r"increased\s+(?:\w+\s+(?:and\s+\w+\s+)?){1,3}damage", context)
    ):
        return "damage_mult" if fmt_type == "multiplicative" else "damage"
    if "explode for" in context or "of the damage" in context:
        return "damage_proc"
    if "attack speed" in context:
        return "attack_speed_mult" if fmt_type == "multiplicative" else "attack_speed"
    if "movement speed" in context:
        return "movement_speed"
    if "critical strike chance" in context:
        return "crit_chance"
    if "critical strike damage" in context:
        return "crit_damage"
    if "lucky hit" in context:
        return "lucky_hit"
    if "chance to" in context:
        return "proc_chance"
    if "armor" in context:
        return "armor"
    if "resist" in context:
        return "resistance"
    if "life " in context or "maximum life" in context:
        return "life"
    if "fortify" in context:
        return "fortify"
    if "barrier" in context:
        return "barrier"
    if "duration" in context or "seconds" in context:
        return "duration"

    return "unknown"


# Inline token: [expr|format|]
INLINE_TOKEN = re.compile(r"\[([^|\]]*)\|([^|\]]*)\|\]")
# Hardcoded percentage with format marker: 12%[x] or 6%[+]
HARDCODED_PCT = re.compile(r"(\d+(?:\.\d+)?)%\s*\[([x+])\]")


def extract_passive_effects(desc: str) -> list[dict]:
    """Extract all (value, tag) effects from a passive description."""
    if not desc:
        return []

    effects = []
    # Inline tokens (with formula expressions)
    for m in INLINE_TOKEN.finditer(desc):
        eff = extract_inline_value(m, desc)
        if eff:
            effects.append(eff)
    # Hardcoded percentage values
    for m in HARDCODED_PCT.finditer(desc):
        eff = extract_hardcoded_value(m, desc)
        if eff:
            effects.append(eff)

    # Deduplicate by (tag, value)
    seen = set()
    unique = []
    for eff in effects:
        key = (eff["tag"], round(eff.get("raw_value", 0), 4))
        if key not in seen:
            seen.add(key)
            unique.append(eff)
    return unique


def main() -> None:
    """
    Re-extract passive effects. Source preference:
      1. /tmp/maxroll_data.json (full raw skill data — preferred)
      2. data/passive_table.json (existing extracted descs — fallback)
    """
    use_existing = False
    if MAXROLL.exists():
        with open(MAXROLL) as f:
            md = json.load(f)
        skills_mx = md.get("skills", {})
        print(f"Scanning {len(skills_mx)} skills from {MAXROLL}...")
    elif OUT.exists():
        with open(OUT) as f:
            existing = json.load(f)
        skills_mx = {sid: {
            "desc": p.get("desc", ""),
            "name": p.get("name", sid),
            "primaryTag": p.get("primary_tag"),
            "category": p.get("category"),
        } for sid, p in existing.items()}
        use_existing = True
        print(f"WARN: {MAXROLL} not found — re-extracting from {OUT} ({len(skills_mx)} entries)")
    else:
        print(f"ERROR: neither {MAXROLL} nor {OUT} exist")
        return

    # Load d4data passive SF values for verification (optional)
    d4_passives: dict = {}
    if PASSIVE_D4DATA.exists():
        with open(PASSIVE_D4DATA) as f:
            d4_passives = json.load(f)

    results: dict[str, dict] = {}
    for sid, s in skills_mx.items():
        if not isinstance(s, dict):
            continue

        # Identify passives (only when reading from raw maxroll)
        if not use_existing:
            is_passive = (
                "Passive" in sid
                or "_Talent_" in sid
                or s.get("category") == 12
            )
            if not is_passive:
                continue

        raw_desc = s.get("desc", "")
        if not raw_desc:
            continue

        # When reading from /tmp/maxroll_data.json, desc has {markup} we strip.
        # When re-reading from passive_table.json, desc is already stripped.
        if use_existing:
            desc = raw_desc
        else:
            desc = strip_markup_keep_brackets(raw_desc)
        effects = extract_passive_effects(desc)

        # Try to attach d4data SF values for verification
        d4_data = d4_passives.get(sid, {})
        d4_sfs = d4_data.get("all_sfs", {})

        # Match d4data SF values against extracted effects (best effort)
        for eff in effects:
            raw = eff.get("raw_value", 0)
            # Find a d4data SF whose value matches (within 1% tolerance)
            for sf_name, sf_val in d4_sfs.items():
                if abs(sf_val * 100 - raw) < 1.0 or abs(sf_val - raw) < 0.01:
                    eff["d4data_sf"] = sf_name
                    eff["d4data_verified"] = True
                    break

        if not effects:
            continue

        results[sid] = {
            "name": s.get("name") or sid,
            "primary_tag": s.get("primaryTag"),
            "category": s.get("category"),
            "desc": desc[:300],
            "effects": effects,
            "d4data_sfs": d4_sfs,
        }

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    # Stats
    total_passives = len(results)
    with_meaningful = sum(
        1 for r in results.values()
        if any(e["tag"] != "unknown" for e in r["effects"])
    )
    with_d4_match = sum(
        1 for r in results.values()
        if any(e.get("d4data_verified") for e in r["effects"])
    )
    print(f"\nExtracted {total_passives} passives to {OUT}")
    print(f"  With non-unknown tags:  {with_meaningful}")
    print(f"  With d4data verified:   {with_d4_match}")
    print()

    # Show key passive verifications
    samples = [
        ("Sorcerer_Talent_Cold_T3_N1", "Shatter"),
        ("Barbarian_Talent_Warlord_T5_N1", "Walking Arsenal"),
        ("Necromancer_Talent_Caster_T5_N1", "Ossified Essence"),
        ("Paladin_Talent_KeyPassive_2", "Path of the Penitent"),
        ("Spiritborn_Talent_KeyPassive_1", "Vital Strikes"),
        ("Druid_Talent_Hybrid_T5_N2", "Nature's Fury"),
        ("Rogue_Talent_Cunning_T5_N2", "Momentum"),
        ("Barbarian_Talent_Warlord_T5_N2", "Unbridled Rage"),
    ]
    for sid, name in samples:
        r = results.get(sid)
        if not r:
            print(f"  {name}: NOT FOUND")
            continue
        print(f"\n  {r['name']}:")
        for e in r["effects"][:5]:
            verified = " ✓" if e.get("d4data_verified") else ""
            print(f"    {e['tag']:30s} = {e['value']:.4f} ({e['type']}){verified}")


if __name__ == "__main__":
    main()
