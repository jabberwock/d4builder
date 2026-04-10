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
OVERRIDES = Path(__file__).parent / "passive_overrides.yaml"


def _load_overrides_yaml(path: Path) -> dict:
    """
    Minimal YAML parser for passive_overrides.yaml shape (no pyyaml dep).
    Supports top-level mappings → list of inline-or-block mappings.
    """
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

    with open(path) as f:
        for raw in f:
            stripped = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
            if not stripped:
                continue
            indent = len(stripped) - len(stripped.lstrip())
            line = stripped.strip()

            if indent == 0 and line.endswith(":"):
                current_key = line[:-1].strip()
                result[current_key] = []
                current_list = result[current_key]
                pending_block = None
            elif current_key is not None and line.startswith("- "):
                rest = line[2:].strip()
                if ":" in rest:
                    k, v = rest.split(":", 1)
                    pending_block = {k.strip(): coerce(v)}
                    pending_indent = indent + 2
                    current_list.append(pending_block)
            elif pending_block is not None and indent >= pending_indent and ":" in line:
                k, v = line.split(":", 1)
                pending_block[k.strip()] = coerce(v)

    return result


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


# Bonus-percentage runtime variables — when an expression contains one of
# these AND is wrapped in Min/Max, it's the game computing the player's
# CURRENT live bonus from their actual stats. These are tooltip displays,
# not new effects.
#
# Important: PlayerHealthMax() and ToPlayerDmgNum() are NOT in this list
# because they're just display formatters around real numeric coefficients
# (e.g. PlayerHealthMax()*0.05 for "5% of max life heal" — the 0.05 is real).
_BONUS_RUNTIME_PATTERNS = (
    "_Bonus",          # Damage_Bonus_*, DOT_DPS_Bonus_*, Multiplicative_*_Bonus_*
    "_Percent",        # *_Percent_Bonus, Crit_Damage_Percent, Damage_Percent_*
    "Resource_Cur",    # Resource_Cur(N) — only used in "Current Bonus" displays
)


def _is_runtime_display_expr(expr: str) -> bool:
    """
    Detect 'Current Bonus' tooltip displays — expressions wrapped in Min/Max
    clamps AND referencing live bonus state (Damage_Bonus_*, *_Percent_*,
    Resource_Cur). These compute the player's current computed bonus and
    would double-count if extracted as new effects.

    Does NOT filter PlayerHealthMax/ToPlayerDmgNum tokens since those just
    render real numeric coefficients (e.g. "5% of max life heal").
    """
    has_clamp = "Min(" in expr or "Max(" in expr
    if not has_clamp:
        return False
    return any(p in expr for p in _BONUS_RUNTIME_PATTERNS)


def extract_inline_value(token_match: re.Match, full_desc: str) -> dict | None:
    """
    Extract value + semantic tag from a [expr|format|] token, using surrounding
    text for semantic context.
    """
    expr = token_match.group(1) or ""
    fmt = token_match.group(2) or ""

    # Skip 'Current Bonus' tooltip displays — they're computed from other
    # effects and would double-count if extracted.
    if _is_runtime_display_expr(expr):
        return None

    leading = extract_leading_number(expr)
    if leading is None:
        return None

    # If expression has *100, the leading is a fraction → multiply
    multiplier = 1.0
    if "*100" in expr or "* 100" in expr:
        multiplier = 100.0

    raw_value = leading * multiplier

    # Expression-driven tag detection: when the expression itself unambiguously
    # identifies the effect type, skip context inference.
    fmt_type = parse_format_hint(fmt)
    if "PlayerHealthMax" in expr:
        # X * PlayerHealthMax() → "X% of max life" (heal/barrier value)
        return {
            "tag": "life",
            "value": leading * multiplier / 100.0 if multiplier == 100 else leading,
            "raw_value": raw_value,
            "format": fmt,
            "type": fmt_type,
            "expr": expr[:80],
        }

    # Token-local hint: when the format marker is %[x] AND there's a "%[x]"
    # earlier in the description with "damage" nearby, the value is likely
    # a damage cap on the same effect.
    fmt_str = fmt.lower()
    is_damage_mult_marker = "x" in fmt_str

    # Try forward context first; fall back to backward, then wide bidirectional,
    # then full-description scan for global patterns.
    context = _forward_context(full_desc, token_match.end())
    tag = _infer_tag(context, fmt_type)
    if tag == "unknown":
        back = _backward_context(full_desc, token_match.start())
        if back:
            tag = _infer_tag(back, fmt_type)
    if tag == "unknown":
        # Wide bidirectional 60-char window
        wide = full_desc[max(0, token_match.start() - 60):
                         min(len(full_desc), token_match.end() + 60)].lower()
        tag = _infer_tag(wide, fmt_type)
    if tag == "unknown":
        # Final fallback: scan the entire description. Used for global
        # patterns where the descriptor is far from the value
        # (e.g., "Critical Strike Chance with X is increased by [val]").
        # When the marker is multiplicative, prefer a damage-related tag
        # since unannotated %[x] caps are nearly always damage mults.
        full = full_desc.lower()
        if is_damage_mult_marker and (
            "increased damage" in full or "more damage" in full
            or "deal " in full and "damage" in full
        ):
            tag = "damage_mult"
        else:
            tag = _infer_tag(full, fmt_type)

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

    # Forward → backward → wide bidirectional → full desc scan.
    is_damage_mult_marker = fmt_marker == "x"
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
    if tag == "unknown":
        full = full_desc.lower()
        # Multiplicative caps default to damage_mult since unannotated %[x]
        # values are nearly always damage caps in D4 passives.
        if is_damage_mult_marker and (
            "increased damage" in full or "more damage" in full
            or "damage is increased" in full
        ):
            tag = "damage_mult"
        else:
            tag = _infer_tag(full, fmt_type)

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


_RESOURCE_WORDS = r"(mana|fury|spirit|essence|energy|faith|vigor)"


def _infer_tag(context: str, fmt_type: str) -> str:
    """Determine semantic tag from surrounding context words.

    Order matters: more-specific compound phrases must be checked BEFORE the
    generic "damage" fallback, otherwise nearby damage clauses pollute tags
    for things like resource_cost_reduction.
    """
    # === Reductions and cost modifiers ===
    if "resource cost reduction" in context:
        return "resource_cost_reduction"
    if "cooldown reduction" in context:
        return "cooldown_reduction"
    if "damage reduction" in context:
        return "damage_reduction"
    if re.search(rf"less\s+{_RESOURCE_WORDS}", context):
        return "mana_cost_reduction"
    if re.search(rf"cost\s+\S+\s+more\s+{_RESOURCE_WORDS}", context):
        return "resource_cost_increase"

    # === Summon / Minion / Companion damage ===
    if (
        "summon damage" in context
        or "minion damage" in context
        or "companion skills deal" in context
    ):
        return "damage_mult_summon" if fmt_type == "multiplicative" else "damage_summon"

    # === Resource generation / regeneration ===
    # "X more Spirit" / "X more Fury" — % increase to generation
    if re.search(rf"more\s+{_RESOURCE_WORDS}", context):
        return "resource_gen_pct"
    # "Fury Generation is increased by" / "X Generation is increased"
    if re.search(rf"{_RESOURCE_WORDS}\s+generation\s+is\s+increased", context):
        return "resource_gen_pct"
    # "Primary Resource Generation" (general, [WIP] Meditation)
    if "primary resource generation" in context or "resource generation" in context:
        return "resource_gen_pct"
    # "generates N Essence" / "gain N Fury" / "grants N Vigor"
    if re.search(rf"(?:generates?|gain(?:s)?|grants?)\s+\S+\s+{_RESOURCE_WORDS}", context):
        return "resource_gen_flat"
    # "spending X Mana" / "costs X Mana" — resource cost
    if re.search(rf"spend(?:ing)?\s+\S+\s+{_RESOURCE_WORDS}", context):
        return "resource_cost"
    # "Energy Regeneration" (increased)
    if re.search(rf"{_RESOURCE_WORDS}\s+regeneration", context):
        return "resource_regen_pct"

    # === Maximum resource ===
    if re.search(rf"maximum\s+{_RESOURCE_WORDS}", context):
        return "resource_max"

    # === Healing ===
    if (
        "additional healing" in context
        or "healing received" in context
        or "increased healing" in context
        or re.search(r"healing\s+from", context)
    ):
        return "healing_received"
    if re.search(r"heal\s+for\s+\S+\s+of\s+your\s+maximum\s+life", context):
        return "healing_pct"
    if "allies heal" in context:
        return "heal_share"
    if re.search(r"healing\s+(?:you\s+)?(?:receive|gain)", context):
        return "heal_share"

    # === Crowd control / states applied ===
    if "more chill" in context or "increased chill effect" in context:
        return "applies_chill"
    if re.search(r"appl(?:y|ies|ying)\s+\S*\s*chill", context):
        return "applies_chill"
    if re.search(r"slowed?\s+by", context) or "movement speed further reduced" in context:
        return "slow_pct"
    if re.search(r"chance\s+to\s+(immobilize|stun|freeze|daze|fear|knock)", context):
        return "proc_chance"
    # "X% chance to Immobilize" — value precedes percent literal
    if "chance to immobilize" in context:
        return "proc_chance"

    # === Thorns ===
    if "thorns" in context:
        return "thorns"

    # === Dodge / Retribution / misc defense ===
    if "dodge chance" in context:
        return "dodge_chance"
    if "retribution" in context:
        return "retribution"
    if "block chance" in context:
        return "block_chance"
    if "block reduction" in context:
        return "block_reduction"

    # === Damage proc on dealt damage ===
    if "of its damage" in context or "of the damage" in context or "explode for" in context:
        return "damage_proc"

    # === Damage variants ===
    if "damage to" in context or "damage vs" in context:
        m = re.search(r"damage (?:to|vs)\s+(\w+)", context)
        if m:
            target = m.group(1).rstrip(".,?")
            return f"damage_mult_vs_{target}" if fmt_type == "multiplicative" else f"damage_vs_{target}"
    # Positional damage (overpower/crit/vuln) before generic damage
    if "overpower damage" in context:
        return "overpower_damage_mult" if fmt_type == "multiplicative" else "overpower_damage"
    if "crit strike damage" in context or "critical strike damage" in context:
        return "crit_damage"
    if "critical strike chance" in context:
        return "crit_chance"
    # "more damage" / "increased damage" / "bonus damage" / "deal X damage"
    # Also catch "X damage is increased by" and "damage bonus is increased to"
    if (
        "more damage" in context
        or "increased damage" in context
        or "bonus damage" in context
        or "damage is increased" in context
        or "damage bonus" in context
        or "damage you" in context
        or "damage by" in context  # "increasing your damage by"
        or "damage dealt" in context  # "damage dealt to them is increased"
        or re.search(r"(?:more|increased)\s+(?:\w+\s+(?:and\s+\w+\s+)?){1,3}damage", context)
        or re.search(r"deals?\s+\S*\s*\w*\s*damage", context)
    ):
        return "damage_mult" if fmt_type == "multiplicative" else "damage"

    if "attack speed" in context:
        return "attack_speed_mult" if fmt_type == "multiplicative" else "attack_speed"
    if "movement speed" in context:
        return "movement_speed"
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

    # Load hand-tagged overrides for custom-mechanic passives.
    # These replace any auto-extracted unknown effects with curated tags.
    overrides: dict = {}
    if OVERRIDES.exists():
        try:
            import yaml
            with open(OVERRIDES) as f:
                overrides = yaml.safe_load(f) or {}
        except ImportError:
            # Fallback: minimal YAML parser for our shape
            overrides = _load_overrides_yaml(OVERRIDES)

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

        # Mark dev placeholders so they're excluded from tag-rate denominator.
        # These are stub passives in maxroll data that don't represent
        # shipped game content (PH = placeholder, WIP = work in progress).
        name_str = s.get("name") or sid
        is_placeholder = bool(
            re.search(r"\b(?:\(PH\)|\[PH\]|\[WIP\])", name_str, re.IGNORECASE)
            or "(PH)" in name_str
            or "[WIP]" in name_str
            or name_str.startswith("(PH)")
            or name_str.startswith("[PH]")
        )

        # Apply hand-tagged overrides: replace any unknown effects with the
        # curated entries from passive_overrides.yaml. Lookup is by sid OR by
        # display name (some overrides use the human name like "Memento Mori").
        applied_override = False
        override_entries = overrides.get(sid) or overrides.get(name_str)
        if override_entries and isinstance(override_entries, list):
            # Drop unknown effects and append curated overrides
            effects = [e for e in effects if e["tag"] != "unknown"]
            for ov in override_entries:
                if not isinstance(ov, dict):
                    continue
                effects.append({
                    "tag": ov.get("tag", "unknown"),
                    "value": ov.get("value", 0),
                    "raw_value": ov.get("value", 0),
                    "format": "override",
                    "type": "manual",
                    "source": ov.get("source", ""),
                })
            applied_override = True

        results[sid] = {
            "name": name_str,
            "primary_tag": s.get("primaryTag"),
            "category": s.get("category"),
            "desc": desc[:300],
            "effects": effects,
            "d4data_sfs": d4_sfs,
            "placeholder": is_placeholder,
            "override_applied": applied_override,
        }

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    # Stats — separate placeholder dev stubs from real game content
    total_passives = len(results)
    placeholders = sum(1 for r in results.values() if r.get("placeholder"))
    real = total_passives - placeholders

    real_effects = sum(
        len(r["effects"]) for r in results.values() if not r.get("placeholder")
    )
    real_unknown = sum(
        1 for r in results.values()
        if not r.get("placeholder")
        for e in r["effects"]
        if e["tag"] == "unknown"
    )
    real_with_unknown = sum(
        1 for r in results.values()
        if not r.get("placeholder")
        and any(e["tag"] == "unknown" for e in r["effects"])
    )
    with_d4_match = sum(
        1 for r in results.values()
        if any(e.get("d4data_verified") for e in r["effects"])
    )
    tag_rate = (
        100 * (real_effects - real_unknown) / real_effects if real_effects else 0.0
    )
    print(f"\nExtracted {total_passives} passives to {OUT}")
    print(f"  Real content:           {real} ({placeholders} dev placeholders excluded)")
    print(f"  Real effects total:     {real_effects}")
    print(f"  Real unknown effects:   {real_unknown}")
    print(f"  Real passives w/ unknown: {real_with_unknown}")
    print(f"  Tag rate (real):        {tag_rate:.1f}%")
    print(f"  d4data verified:        {with_d4_match}")
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
