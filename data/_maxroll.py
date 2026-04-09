"""
Shared loader for maxroll game data JSON.

Search order (first file found wins):
  1. data/maxroll_data.json    (preferred — committed to project)
  2. /tmp/maxroll_data.json    (legacy, gets cleared by reboots)

Usage:
    from _maxroll import MAXROLL_PATH, load_maxroll
    md = load_maxroll()  # raises FileNotFoundError if both missing
"""

import json
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "maxroll_data.json"
_TMP_PATH = Path("/tmp/maxroll_data.json")


def find_maxroll_path() -> Path | None:
    """Return the first existing maxroll JSON path, or None if missing."""
    if _DATA_PATH.exists():
        return _DATA_PATH
    if _TMP_PATH.exists():
        return _TMP_PATH
    return None


# Module-level constant for scripts that just need the path
MAXROLL_PATH = find_maxroll_path() or _DATA_PATH


def load_maxroll() -> dict:
    """Load maxroll data, raising FileNotFoundError if neither location has it."""
    p = find_maxroll_path()
    if p is None:
        raise FileNotFoundError(
            f"maxroll_data.json not found. "
            f"Drop it at {_DATA_PATH} (preferred) or {_TMP_PATH}."
        )
    with open(p) as f:
        return json.load(f)
