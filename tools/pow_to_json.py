#!/usr/bin/env python3
"""
Diablo 4 .pow file parser — extracts power/skill data to JSON.

Parses the binary CASC .pow format used by Diablo 4 to define skill coefficients,
damage formulas, scaling factors (SF_), and unique item interactions.

Supports optional SF_ value resolution via an external lookup table.
"""

import struct
import re
import json
import sys
import os
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<I', data, offset)[0]

def read_i32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<i', data, offset)[0]

def read_f32(data: bytes, offset: int) -> float:
    return struct.unpack_from('<f', data, offset)[0]

def read_u64(data: bytes, offset: int) -> int:
    return struct.unpack_from('<Q', data, offset)[0]

def read_cstring(data: bytes, offset: int, max_len: int = 256) -> str:
    """Read a null-terminated ASCII string."""
    end = data.find(b'\x00', offset, offset + max_len)
    if end == -1:
        end = offset + max_len
    return data[offset:end].decode('ascii', errors='replace')

def align4(offset: int) -> int:
    return (offset + 3) & ~3


# ---------------------------------------------------------------------------
# String extraction
# ---------------------------------------------------------------------------

def extract_strings(data: bytes, min_len: int = 3) -> list[tuple[int, str]]:
    """Extract all printable ASCII strings with their file offsets."""
    results = []
    current = b''
    start = 0
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if not current:
                start = i
            current += bytes([b])
        else:
            if len(current) >= min_len:
                results.append((start, current.decode('ascii', errors='replace')))
            current = b''
    if len(current) >= min_len:
        results.append((start, current.decode('ascii', errors='replace')))
    return results


# ---------------------------------------------------------------------------
# Formula detection & classification
# ---------------------------------------------------------------------------

FORMULA_PATTERNS = [
    re.compile(rb'(?:SF_\d+|Table\(\d+,\w+\)|Affix[A-Za-z0-9_.#\"\s]+|Attacks_Per_Second_Total|'
               rb'Owner\.[A-Za-z_]+|Min\([^)]+\)|Chance_For_[A-Za-z_#]+|'
               rb'AoE_Size_Bonus_Per_Power#[A-Za-z_]+|'
               rb'-?\d+\.?\d*\s*[\*/\+\-]\s*(?:SF_\d+|Table|[A-Za-z]))')
]

def is_formula_string(s: str) -> bool:
    """Check if a string looks like a D4 formula/expression."""
    indicators = ['SF_', 'Table(', 'Affix', 'Attacks_Per_Second',
                  'Owner.', 'Min(', 'Max(', 'Chance_For_', 'AoE_Size',
                  ' * ', ' / ', ' + ', ' - ']
    return any(ind in s for ind in indicators)

DAMAGE_TABLE_ID = 34
COOLDOWN_TABLE_ID = 35

def classify_formula(s: str) -> str:
    """Classify a formula string by its role."""
    sl = s.lower()
    if 'table(' in sl and '*' in sl:
        # Distinguish damage (Table 34) from cooldown/duration (Table 35)
        table_match = re.search(r'table\((\d+)', sl)
        if table_match:
            tid = int(table_match.group(1))
            if tid == DAMAGE_TABLE_ID:
                return 'damage_scalar'
            elif tid == COOLDOWN_TABLE_ID:
                return 'cooldown_scalar'
            else:
                return 'damage_scalar'  # unknown table, assume damage
        return 'damage_scalar'
    if 'attacks_per_second' in sl:
        return 'attack_speed'
    if 'affix' in sl and 'static value' in sl:
        return 'unique_item_affix'
    if 'affix_value' in sl and 'weapon_damage' in sl:
        return 'weapon_damage_scaling'
    if 'affix_value' in sl:
        return 'affix_modifier'
    if 'aoe_size' in sl or 'min(' in sl or 'max(' in sl:
        return 'aoe_scaling'
    if 'chance_for_double_damage' in sl:
        return 'crit_modifier'
    if re.match(r'^SF_\d+\s*[\*/\+\-]', s) or re.match(r'.*SF_\d+', s):
        return 'sf_expression'
    if re.match(r'^-?\d+\.?\d*$', s.strip()):
        return 'constant'
    return 'expression'


# ---------------------------------------------------------------------------
# Typed value parser (type_tag, value) pairs after formula strings
# ---------------------------------------------------------------------------

VALUE_TYPE_FLOAT = 6
VALUE_TYPE_SF_REF = 5
VALUE_TYPE_INT = 0

def parse_typed_values(data: bytes, offset: int, max_pairs: int = 8) -> list[dict]:
    """
    Parse (type_tag, value) pairs that follow formula strings.
    type 6 = float literal, type 5 = SF reference (index = SF_N + 6)
    """
    values = []
    pos = offset
    for _ in range(max_pairs):
        if pos + 8 > len(data):
            break
        type_tag = read_u32(data, pos)
        if type_tag == VALUE_TYPE_FLOAT:
            fval = read_f32(data, pos + 4)
            values.append({'type': 'float', 'value': round(fval, 10)})
        elif type_tag == VALUE_TYPE_SF_REF:
            idx = read_u32(data, pos + 4)
            sf_num = idx - 6 if idx >= 6 else idx
            values.append({'type': 'sf_ref', 'sf_index': idx, 'sf_name': f'SF_{sf_num}'})
        else:
            # Not a recognized type pair — stop parsing
            break
        pos += 8
    return values


# ---------------------------------------------------------------------------
# Header parser
# ---------------------------------------------------------------------------

MAGIC = 0xDEADBEEF

def parse_header(data: bytes) -> dict:
    """Parse the .pow file header."""
    magic = read_u32(data, 0x00)
    if magic != MAGIC:
        raise ValueError(f'Invalid .pow magic: 0x{magic:08X} (expected 0xDEADBEEF)')

    power_id = read_u32(data, 0x10)
    hash_val = read_u32(data, 0x64)

    return {
        'magic': f'0x{magic:08X}',
        'power_id': power_id,
        'power_id_hex': f'0x{power_id:08X}',
        'hash': f'0x{hash_val:08X}',
        'file_size': len(data),
    }


# ---------------------------------------------------------------------------
# Section table parser
# ---------------------------------------------------------------------------

def find_section_table(data: bytes) -> list[dict]:
    """
    Locate the 4-entry section table that defines major data regions.
    The table is stored as 4 entries of (offset_u32, size_u32, pad, pad) at 16-byte stride.
    We scan the file for this characteristic pattern.
    """
    sections = []
    file_size = len(data)

    # Section entries are (offset_u32, size_u32) pairs separated by variable
    # padding. Scan the region for all valid (offset, size) pairs, then pick
    # 4 consecutive ascending entries.
    candidates = []
    for pos in range(0x0400, min(0x0D00, file_size - 8), 4):
        try:
            off = read_u32(data, pos)
            sz = read_u32(data, pos + 4)
            if (256 < off < file_size and 16 < sz < file_size
                    and off + sz <= file_size + 512):
                candidates.append((pos, off, sz))
        except struct.error:
            continue

    # Find 4 consecutive candidates with ascending offsets
    best_table = None
    for i in range(len(candidates) - 3):
        group = candidates[i:i+4]
        offsets = [g[1] for g in group]
        if all(offsets[j] < offsets[j+1] for j in range(3)):
            # Verify they don't overlap
            entries = [(g[1], g[2]) for g in group]
            if all(entries[j][0] + entries[j][1] <= entries[j+1][0] + 256
                   for j in range(3)):
                best_table = (group[0][0], entries)
                break

    if not best_table:
        return sections

    names = ['sf_definitions', 'helper_formulas', 'payload_data', 'scaling_tables']
    for (off, sz), name in zip(best_table[1], names):
        sections.append({
            'name': name,
            'offset': off,
            'offset_hex': f'0x{off:04X}',
            'size': sz,
            'end': off + sz,
            'end_hex': f'0x{off+sz:04X}',
        })

    return sections


# ---------------------------------------------------------------------------
# SF definition extractor
# ---------------------------------------------------------------------------

def extract_sf_definitions(data: bytes) -> list[dict]:
    """
    Extract all SF_N definitions from the file.

    True SF definitions are standalone null-terminated strings like "SF_9\\x00"
    followed by metadata: [padding_to_8_bytes] [type_tag=5] [internal_index].
    The internal_index = SF_number + 6.

    We distinguish definitions from inline formula references (e.g. "SF_0 / SF_3")
    by checking that the string starts at a position where the preceding byte is
    a null (i.e. it's the start of a new string, not mid-formula).
    """
    sf_defs = []

    for m in re.finditer(rb'SF_(\d+)\x00', data):
        offset = m.start()
        sf_num = int(m.group(1).decode())
        name = f'SF_{sf_num}'

        # Only consider this a definition if:
        # 1. It's at the start of a string (preceded by null or at file start)
        # 2. The string is JUST "SF_N" (not part of a longer formula)
        if offset > 0 and data[offset - 1] != 0x00:
            continue  # mid-formula reference, not a definition

        # Verify the full string at this offset is just "SF_N"
        full_str = read_cstring(data, offset, 32)
        if full_str != name:
            continue  # part of a longer expression like "SF_0 / SF_3"

        # Read metadata after the 8-byte padded name field
        meta_start = offset + 8
        if meta_start + 8 <= len(data):
            type_tag = read_u32(data, meta_start)
            index = read_u32(data, meta_start + 4)

            # True SF definitions have type_tag=5 and index=sf_num+6
            expected_index = sf_num + 6
            is_verified = (type_tag == 5 and index == expected_index)

            entry = {
                'name': name,
                'sf_number': sf_num,
                'internal_index': index,
                'offset': offset,
                'offset_hex': f'0x{offset:04X}',
                'type_tag': type_tag,
                'verified': is_verified,
            }
            sf_defs.append(entry)

    # Deduplicate by name (keep first occurrence = true definition)
    seen = set()
    unique = []
    for sf in sf_defs:
        if sf['name'] not in seen:
            seen.add(sf['name'])
            unique.append(sf)
    return unique


# ---------------------------------------------------------------------------
# Formula/payload extractor
# ---------------------------------------------------------------------------

def extract_formulas(data: bytes) -> list[dict]:
    """
    Extract all formula strings with their parsed metadata.
    Formulas are null-terminated ASCII strings followed by (type, value) pairs.
    """
    strings = extract_strings(data, min_len=3)
    formulas = []

    for offset, s in strings:
        if not is_formula_string(s):
            continue

        # Skip fragments that are clearly mid-string (from multi-match)
        if s.startswith(('ue ', 'atic', 'oves', 'arb_', 'que_')):
            continue

        formula_end = offset + len(s)
        # Find the actual null terminator
        null_pos = data.find(b'\x00', offset)
        if null_pos > offset:
            full_string = data[offset:null_pos].decode('ascii', errors='replace')
            formula_end = null_pos + 1
        else:
            full_string = s

        # Skip if this is a substring of an already-found formula
        if full_string != s and is_formula_string(full_string):
            s = full_string

        aligned_end = align4(formula_end)

        # Parse typed values after the formula
        typed_values = parse_typed_values(data, aligned_end)

        # Extract coefficient and table ID from damage formulas.
        # Handles both numeric coefficients (1.75 * Table(...))
        # and SF-based coefficients (SF_30 * Table(...))
        coefficient = None
        coefficient_sf = None
        table_id = None
        ternary_expr = None

        # Try numeric coefficient first: "1.75 * Table(34,sLevel)"
        match = re.match(r'(-?\d+\.?\d*)\s*\*\s*Table\((\d+)', s)
        if match:
            coefficient = float(match.group(1))
            table_id = int(match.group(2))
        else:
            # Try SF-based coefficient: "SF_30 * Table(34,sLevel) ..."
            match = re.match(r'(SF_\d+)\s*\*\s*Table\((\d+)', s)
            if match:
                coefficient_sf = match.group(1)
                table_id = int(match.group(2))
            else:
                # Try expression coefficient: "(0.10 / SF_36) * Table(34,sLevel)"
                match = re.match(r'\(([^)]+)\)\s*\*\s*Table\((\d+)', s)
                if match:
                    coefficient_sf = match.group(1).strip()
                    table_id = int(match.group(2))
                else:
                    # Try SF * numeric * Table: "SF_30 * 100 * Table(34,3)"
                    match = re.match(r'(SF_\d+\s*\*\s*\d+\.?\d*)\s*\*\s*Table\((\d+)', s)
                    if match:
                        coefficient_sf = match.group(1).strip()
                        table_id = int(match.group(2))

        # Capture ternary expressions: "... * (SF_41 ? (1 + ...) : 1)"
        ternary_match = re.search(r'\(SF_\d+\s*\?\s*\([^)]*\)\s*:\s*\d+\)', s)
        if ternary_match:
            ternary_expr = ternary_match.group(0)

        # Extract SF references from formula text
        sf_refs = re.findall(r'SF_(\d+)', s)

        entry = {
            'formula': s,
            'classification': classify_formula(s),
            'offset': offset,
            'offset_hex': f'0x{offset:04X}',
            'parsed_values': typed_values,
        }

        if coefficient is not None:
            entry['coefficient'] = coefficient
        if coefficient_sf is not None:
            entry['coefficient_sf'] = coefficient_sf
        if table_id is not None:
            entry['table_id'] = table_id
        if ternary_expr is not None:
            entry['ternary_condition'] = ternary_expr
        if sf_refs:
            entry['sf_references'] = [f'SF_{n}' for n in sf_refs]

        formulas.append(entry)

    # Deduplicate formulas by offset (keep unique offsets)
    seen_offsets = set()
    unique = []
    for f in formulas:
        if f['offset'] not in seen_offsets:
            seen_offsets.add(f['offset'])
            unique.append(f)
    return unique


# ---------------------------------------------------------------------------
# Payload grouping — identify damage payloads
# ---------------------------------------------------------------------------

def group_payloads(formulas: list[dict]) -> list[dict]:
    """
    Group formulas into logical damage payloads.
    A payload starts with a damage_scalar formula and includes related entries.
    """
    payloads = []
    current_payload = None
    payload_idx = 0

    for formula in formulas:
        cls = formula['classification']

        if cls == 'damage_scalar':
            if current_payload:
                payloads.append(current_payload)
            damage_info = {
                'formula': formula['formula'],
                'table_id': formula.get('table_id'),
            }
            # Include whichever coefficient type is present
            if formula.get('coefficient') is not None:
                damage_info['coefficient'] = formula['coefficient']
            if formula.get('coefficient_sf') is not None:
                damage_info['coefficient_sf'] = formula['coefficient_sf']
            if formula.get('ternary_condition') is not None:
                damage_info['ternary_condition'] = formula['ternary_condition']
            if formula.get('sf_references'):
                damage_info['sf_references'] = formula['sf_references']
            damage_info['parsed_values'] = formula.get('parsed_values', [])

            current_payload = {
                'payload_index': payload_idx,
                'damage': damage_info,
                'modifiers': [],
            }
            payload_idx += 1
        elif current_payload:
            current_payload['modifiers'].append({
                'formula': formula['formula'],
                'classification': cls,
                'sf_references': formula.get('sf_references', []),
                'parsed_values': formula.get('parsed_values', []),
            })

    if current_payload:
        payloads.append(current_payload)

    return payloads


# ---------------------------------------------------------------------------
# Scaling table extractor (Section 4)
# ---------------------------------------------------------------------------

def extract_scaling_tables(data: bytes, sections: list[dict]) -> list[dict]:
    """
    Extract scaling table definitions from section 4.
    These are expression/value entries that define how SF values scale,
    typically with patterns like: "SF_0 / (1/(13/30))" followed by metadata.

    If the section table wasn't found, falls back to scanning for known
    scaling-table patterns in the latter portion of the file.
    """
    tables = []

    if len(sections) >= 4:
        start = sections[3]['offset']
        end = min(sections[3]['end'], len(data))
    else:
        # Fallback: scan from ~80% into the file where scaling tables typically live
        start = int(len(data) * 0.80)
        end = len(data)

    # Scan for formula strings in the scaling region
    pos = start
    while pos < end and pos + 8 <= len(data):
        b = data[pos]
        if 32 <= b < 127:
            s = read_cstring(data, pos, 80)
            if len(s) >= 2 and (is_formula_string(s) or s.startswith('SF_')
                                or re.match(r'^\d+$', s)):
                str_end = align4(pos + len(s) + 1)
                typed_vals = parse_typed_values(data, str_end)

                entry = {
                    'expression': s,
                    'offset_hex': f'0x{pos:04X}',
                    'parsed_values': typed_vals,
                }

                sf_refs = re.findall(r'SF_(\d+)', s)
                if sf_refs:
                    entry['sf_references'] = [f'SF_{n}' for n in sf_refs]

                tables.append(entry)
                pos = str_end + max(len(typed_vals) * 8, 4)
                continue
        pos += 4

    return tables


# ---------------------------------------------------------------------------
# SF value resolution
# ---------------------------------------------------------------------------

def load_sf_lookup(path: str) -> dict[str, float]:
    """
    Load an SF_ lookup table from a JSON file.
    Expected format: {"SF_0": 1.234, "SF_1": 5.678, ...}
    """
    with open(path, 'r') as f:
        return json.load(f)

def resolve_sf_in_formula(formula: str, sf_lookup: dict[str, float]) -> str:
    """Replace SF_N references in a formula string with their resolved values."""
    def replacer(m):
        name = m.group(0)
        if name in sf_lookup:
            val = sf_lookup[name]
            return str(val)
        return name
    return re.sub(r'SF_\d+', replacer, formula)

def resolve_sf_values(parsed: dict, sf_lookup: dict[str, float]) -> dict:
    """
    Walk the parsed output and add resolved values wherever SF_ references exist.
    Modifies in-place and returns the dict.
    """
    # Resolve in formulas
    for formula in parsed.get('formulas', []):
        if 'sf_references' in formula:
            resolved = {}
            for ref in formula['sf_references']:
                if ref in sf_lookup:
                    resolved[ref] = sf_lookup[ref]
            if resolved:
                formula['sf_resolved'] = resolved
                formula['formula_resolved'] = resolve_sf_in_formula(formula['formula'], sf_lookup)

    # Resolve in payload damage entries
    for payload in parsed.get('payloads', []):
        dmg = payload.get('damage', {})
        if 'sf_references' in dmg:
            resolved = {}
            for ref in dmg['sf_references']:
                if ref in sf_lookup:
                    resolved[ref] = sf_lookup[ref]
            if resolved:
                dmg['sf_resolved'] = resolved
                dmg['formula_resolved'] = resolve_sf_in_formula(dmg['formula'], sf_lookup)
                # If coefficient_sf can be resolved to a number, add coefficient
                if 'coefficient_sf' in dmg:
                    coeff_resolved = resolve_sf_in_formula(dmg['coefficient_sf'], sf_lookup)
                    dmg['coefficient_sf_resolved'] = coeff_resolved
                    try:
                        dmg['coefficient'] = eval(coeff_resolved)
                    except:
                        pass

        for mod in payload.get('modifiers', []):
            if 'sf_references' in mod:
                resolved = {}
                for ref in mod['sf_references']:
                    if ref in sf_lookup:
                        resolved[ref] = sf_lookup[ref]
                if resolved:
                    mod['sf_resolved'] = resolved
                    mod['formula_resolved'] = resolve_sf_in_formula(mod['formula'], sf_lookup)

    # Resolve in scaling tables
    for table in parsed.get('scaling_tables', []):
        if 'sf_references' in table:
            resolved = {}
            for ref in table['sf_references']:
                if ref in sf_lookup:
                    resolved[ref] = sf_lookup[ref]
            if resolved:
                table['sf_resolved'] = resolved
                table['expression_resolved'] = resolve_sf_in_formula(table['expression'], sf_lookup)

    # Add full SF lookup to output
    parsed['sf_lookup_applied'] = sf_lookup
    return parsed


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_pow_file(filepath: str, sf_lookup_path: Optional[str] = None) -> dict:
    """
    Parse a .pow file and return a structured JSON-ready dict.

    Args:
        filepath: Path to the .pow file
        sf_lookup_path: Optional path to SF_ value lookup JSON

    Returns:
        dict with all extracted power data
    """
    with open(filepath, 'rb') as f:
        data = f.read()

    # Derive power name from filename
    basename = Path(filepath).stem
    power_name = basename.replace('_', ' ') if '_' in basename else basename

    # Parse all components
    header = parse_header(data)
    sections = find_section_table(data)
    sf_defs = extract_sf_definitions(data)
    formulas = extract_formulas(data)
    payloads = group_payloads(formulas)
    scaling_tables = extract_scaling_tables(data, sections)

    result = {
        'power_name': power_name,
        'source_file': os.path.basename(filepath),
        'header': header,
        'sections': sections,
        'sf_definitions': sf_defs,
        'formulas': formulas,
        'payloads': payloads,
        'scaling_tables': scaling_tables,
    }

    # Apply SF resolution if lookup provided
    if sf_lookup_path:
        sf_lookup = load_sf_lookup(sf_lookup_path)
        resolve_sf_values(result, sf_lookup)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Parse Diablo 4 .pow files to JSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s Barbarian_Whirlwind.pow
  %(prog)s Barbarian_Whirlwind.pow -o whirlwind.json
  %(prog)s Barbarian_Whirlwind.pow --sf-lookup sf_values.json
  %(prog)s *.pow --batch -o output_dir/

SF Lookup JSON format:
  {"SF_0": 1.234, "SF_1": 5.678, "SF_9": 0.43333, ...}
        """)

    parser.add_argument('files', nargs='+', help='.pow file(s) to parse')
    parser.add_argument('-o', '--output', help='Output file or directory (for batch mode)')
    parser.add_argument('--sf-lookup', help='Path to SF_ value lookup JSON file')
    parser.add_argument('--batch', action='store_true',
                        help='Batch mode: process multiple files, output to directory')
    parser.add_argument('--indent', type=int, default=2,
                        help='JSON indentation (default: 2)')
    parser.add_argument('--compact', action='store_true',
                        help='Compact JSON output (no indentation)')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress progress output')

    args = parser.parse_args()

    indent = None if args.compact else args.indent

    if args.batch or len(args.files) > 1:
        # Batch mode
        out_dir = args.output or '.'
        os.makedirs(out_dir, exist_ok=True)

        results = {}
        for filepath in args.files:
            try:
                if not args.quiet:
                    print(f'Parsing: {filepath}', file=sys.stderr)
                result = parse_pow_file(filepath, args.sf_lookup)
                out_name = Path(filepath).stem + '.json'
                out_path = os.path.join(out_dir, out_name)
                with open(out_path, 'w') as f:
                    json.dump(result, f, indent=indent)
                if not args.quiet:
                    print(f'  -> {out_path}', file=sys.stderr)
                results[filepath] = 'ok'
            except Exception as e:
                results[filepath] = f'error: {e}'
                if not args.quiet:
                    print(f'  ERROR: {e}', file=sys.stderr)

        if not args.quiet:
            ok = sum(1 for v in results.values() if v == 'ok')
            print(f'\nProcessed {ok}/{len(results)} files', file=sys.stderr)

    else:
        # Single file mode
        filepath = args.files[0]
        result = parse_pow_file(filepath, args.sf_lookup)

        if args.output:
            with open(args.output, 'w') as f:
                json.dump(result, f, indent=indent)
            if not args.quiet:
                print(f'Written to {args.output}', file=sys.stderr)
        else:
            print(json.dumps(result, indent=indent))


if __name__ == '__main__':
    main()
