#!/usr/bin/env python3
"""
Extract authoritative skill coefficients from maxroll's skills data.

Maxroll's /tmp/maxroll_data.json contains a 'skills' dict where each skill has:
  - payloads: list of damage payloads with fully resolved formula strings
  - cooldown: cooldown formula (resolved)
  - cost: resource cost
  - combatEffectChance: lucky hit chance
  - desc: description with damage breakpoints

Each payload's `damage.scalar` is a complete formula like:
  '1.65*Table(34,sLevel)/5'                          → Ice Shards
  '((Collectible)?2.6:1.3)*Table(34,sLevel)'         → Bone Spear
  '((Collectible)?0.8:0.4)*Table(34,sLevel)'         → Blight

We parse these to extract the base coefficient (without seasonal collectible bonuses),
producing the most authoritative coefficient dataset possible.

Output: data/maxroll_coefficients.json
"""

import json
import re
from pathlib import Path

MAXROLL = (lambda: __import__("_maxroll").MAXROLL_PATH)()
OUT = Path(__file__).parent / "maxroll_coefficients.json"


def extract_base_coefficient(scalar: str) -> tuple[float | None, int | None, dict]:
    """
    Extract the base coefficient from a payload scalar formula by symbolically
    evaluating the expression with Table(34,sLevel) replaced by the symbol T.

    For ternaries with conditions like (Collectible?A:B), we always pick the
    BASE branch (no collectible) which gives the un-buffed default coefficient.

    Returns (coefficient, table_id, metadata).
    """
    if not scalar or not isinstance(scalar, str):
        return None, None, {}

    s = scalar.strip()
    metadata: dict = {"raw": s}

    # Verify it references Table(34, ...) somewhere
    table_match = re.search(r"Table\((\d+)", s)
    if not table_match:
        return None, None, metadata
    table_id = int(table_match.group(1))
    if table_id != 34:
        return None, table_id, metadata

    # Symbolically replace Table(34,...) with the literal '1.0' so we can evaluate
    # the rest of the expression. The result is the per-Table-unit coefficient.
    s_with_t = re.sub(r"Table\(34\s*,\s*[^)]*\)", "1.0", s)

    # Also replace Table(34, N) standalone references (e.g. tooltip displays)
    s_with_t = re.sub(r"Table\(34[^)]*\)", "1.0", s_with_t)

    coeff = _eval_safe(s_with_t)
    if coeff is None or coeff <= 0:
        return None, table_id, metadata

    return coeff, table_id, metadata


def _eval_safe(expr: str) -> float | None:
    """
    Safely evaluate a maxroll formula expression to a numeric value.
    Picks the BASE branch (false branch) of any ternary involving conditions
    we can't evaluate (like GetCollectiblePower, AffixIsEquipped, etc.).
    """
    if not expr:
        return None
    expr = expr.strip()

    # Plain number
    try:
        return float(expr)
    except ValueError:
        pass

    # Strip outer parens
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        balanced = True
        for i, c in enumerate(expr):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0 and i < len(expr) - 1:
                    balanced = False
                    break
        if balanced:
            expr = expr[1:-1].strip()
        else:
            break

    try:
        return float(expr)
    except ValueError:
        pass

    # Top-level ternary: cond ? true : false → take false (base) branch
    parts = _balanced_ternary_split(expr)
    if parts:
        _cond, true_b, false_b = parts
        # Try false branch first (the base/default case)
        result = _eval_safe(false_b)
        if result is not None:
            return result
        # Fallback to true branch
        return _eval_safe(true_b)

    # Top-level addition
    if "+" in expr:
        parts = _split_top_level(expr, "+")
        if len(parts) >= 2:
            vals = [_eval_safe(p) for p in parts]
            if all(v is not None for v in vals):
                return sum(vals)

    # Top-level subtraction
    if "-" in expr:
        # Be careful — subtract differs from negative number
        parts = _split_top_level(expr, "-")
        if len(parts) >= 2 and parts[0]:
            vals = [_eval_safe(p) for p in parts]
            if all(v is not None for v in vals):
                result = vals[0]
                for v in vals[1:]:
                    result -= v
                return result

    # Top-level multiplication
    if "*" in expr:
        parts = _split_top_level(expr, "*")
        if len(parts) >= 2:
            vals = [_eval_safe(p) for p in parts]
            if all(v is not None for v in vals):
                result = 1.0
                for v in vals:
                    result *= v
                return result

    # Top-level division
    if "/" in expr:
        parts = _split_top_level(expr, "/")
        if len(parts) >= 2:
            vals = [_eval_safe(p) for p in parts]
            if all(v is not None and v != 0 for v in vals[1:]) and vals[0] is not None:
                result = vals[0]
                for v in vals[1:]:
                    result /= v
                return result

    return None


def _resolve_expression(expr: str) -> float | None:
    """
    Resolve a coefficient expression to a numeric value.
    Picks the BASE branch (without collectible/seasonal bonuses) for ternaries.
    """
    if not expr:
        return None
    expr = expr.strip()

    # Plain number
    try:
        return float(expr)
    except ValueError:
        pass

    # Strip outer parens
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        balanced = True
        for i, c in enumerate(expr):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0 and i < len(expr) - 1:
                    balanced = False
                    break
        if balanced:
            expr = expr[1:-1].strip()
        else:
            break

    # Try plain number again
    try:
        return float(expr)
    except ValueError:
        pass

    # Ternary: (cond)?A:B → take B (the false/base branch)
    parts = _balanced_ternary_split(expr)
    if parts:
        _cond, true_b, false_b = parts
        # The 'false' branch (post-:) is typically the base case (without collectible)
        result = _resolve_expression(false_b)
        if result is not None:
            return result
        # Fallback to true branch
        result = _resolve_expression(true_b)
        if result is not None:
            return result

    # "X*Y" — multiply
    if "*" in expr:
        parts = _split_top_level(expr, "*")
        if len(parts) >= 2:
            vals = [_resolve_expression(p) for p in parts]
            if all(v is not None for v in vals):
                result = 1.0
                for v in vals:
                    result *= v
                return result

    return None


def _resolve_suffix(suffix: str) -> float | None:
    """Resolve a suffix like '/5' or '*1.5' to a multiplier."""
    suffix = suffix.strip()
    if not suffix:
        return 1.0
    # Pattern: /N (divide)
    m = re.match(r"^/\s*(\d+(?:\.\d+)?)$", suffix)
    if m:
        return 1.0 / float(m.group(1))
    # Pattern: *N
    m = re.match(r"^\*\s*(\d+(?:\.\d+)?)$", suffix)
    if m:
        return float(m.group(1))
    # Compound: /5*1.5 or /5*(...)
    m = re.match(r"^/\s*(\d+(?:\.\d+)?)\s*\*\s*(\d+(?:\.\d+)?)$", suffix)
    if m:
        return float(m.group(2)) / float(m.group(1))
    return None


def _split_top_level(expr: str, sep: str) -> list[str]:
    """Split expr on `sep` only at top-level (not inside parens)."""
    parts = []
    depth = 0
    current = ""
    for c in expr:
        if c == "(":
            depth += 1
            current += c
        elif c == ")":
            depth -= 1
            current += c
        elif c == sep and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += c
    if current.strip():
        parts.append(current.strip())
    return parts


def _balanced_ternary_split(s: str) -> tuple[str, str, str] | None:
    """Split a ternary expression at top-level ? and :."""
    depth = 0
    qpos = -1
    for i, c in enumerate(s):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "?" and depth == 0:
            qpos = i
            break
    if qpos == -1:
        return None
    depth = 0
    cpos = -1
    for i in range(qpos + 1, len(s)):
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == ":" and depth == 0:
            cpos = i
            break
    if cpos == -1:
        return None
    return s[:qpos].strip(), s[qpos + 1:cpos].strip(), s[cpos + 1:].strip()


def extract_skill_data(skill_id: str, skill_data: dict) -> dict | None:
    """Extract coefficient data for one skill from maxroll skill entry."""
    payloads = skill_data.get("payloads", [])
    if not payloads:
        return None

    coeffs: list[float] = []
    raw_formulas: list[str] = []
    for p in payloads:
        if not isinstance(p, dict):
            continue
        damage = p.get("damage", {})
        if not isinstance(damage, dict):
            continue
        scalar = damage.get("scalar", "")
        # Skip integer payloads (typically buff effects, not damage)
        if isinstance(scalar, (int, float)):
            continue
        if not scalar or not isinstance(scalar, str):
            continue
        coeff, table_id, _ = extract_base_coefficient(scalar)
        if coeff is not None and coeff > 0 and table_id == 34:
            coeffs.append(coeff)
            raw_formulas.append(scalar)

    if not coeffs:
        return None

    # Cooldown extraction
    cooldown_val = None
    cd_str = skill_data.get("cooldown")
    if cd_str:
        if isinstance(cd_str, (int, float)):
            cooldown_val = float(cd_str)
        elif isinstance(cd_str, str):
            cooldown_val = _resolve_expression(cd_str)

    # Resource cost
    cost = 0
    cost_list = skill_data.get("cost", [])
    if isinstance(cost_list, list) and cost_list:
        first = cost_list[0]
        if isinstance(first, dict):
            cv = first.get("cost", 0)
            if isinstance(cv, (int, float)):
                cost = int(cv)

    return {
        "name": skill_data.get("name", skill_id),
        "coefficient": coeffs[0],
        "hit_count": len(coeffs),
        "max_coefficient": max(coeffs),
        "all_coefficients": coeffs,
        "raw_formulas": raw_formulas,
        "cooldown_sec": cooldown_val,
        "resource_cost": cost,
        "lucky_hit": skill_data.get("combatEffectChance"),
        "primary_tag": skill_data.get("primaryTag"),
    }


def main() -> None:
    if not MAXROLL.exists():
        print(f"ERROR: {MAXROLL} not found")
        return

    print(f"Loading {MAXROLL}...")
    with open(MAXROLL) as f:
        md = json.load(f)

    skills = md.get("skills", {})
    print(f"Processing {len(skills)} skills...")

    results: dict[str, dict] = {}
    for skill_id, sdata in skills.items():
        if not isinstance(sdata, dict):
            continue
        result = extract_skill_data(skill_id, sdata)
        if result:
            results[skill_id] = result

    with open(OUT, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nExtracted {len(results)} skills with coefficients to {OUT}")
    print()

    samples = ["Necromancer_BoneSpear", "Necromancer_Blight", "Necromancer_Sever",
               "Necromancer_BloodWave", "Sorcerer_IceShards", "Sorcerer_Hydra",
               "Sorcerer_Inferno", "Sorcerer_FrozenOrb", "Spiritborn_Eagle_Core",
               "Druid_Hurricane", "Paladin_HeavensFury", "Barbarian_Whirlwind",
               "Druid_GrizzlyRage"]
    print(f"{'Skill':45s} {'Coeff':>8s} {'Max':>8s} {'Hits':>6s} {'CD':>6s}")
    for s in samples:
        r = results.get(s, {})
        if r:
            c = f"{r.get('coefficient', 0):.2f}"
            mx = f"{r.get('max_coefficient', 0):.2f}"
            h = f"{r.get('hit_count', 0)}"
            cd = f"{r.get('cooldown_sec') or 0:.0f}"
            print(f"  {s:43s} {c:>8s} {mx:>8s} {h:>6s} {cd:>6s}")
        else:
            print(f"  {s:43s} NOT FOUND")


if __name__ == "__main__":
    main()
