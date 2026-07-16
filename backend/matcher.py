"""
Route-to-procedure matching engine.

Core algorithm insight (from real WIII testing):
  Naive fix-overlap counting is insufficient — multiple STARs share long common
  tail segments (e.g., 7 WIII STARs all share DKI/ONILI/PAPAF/ELKIT/NININ).

  The reliable disambiguator: the procedure's name corresponds to a specific
  "core" fix (e.g., "EGUKO 2L" → fix EGUKO). Matching checks whether that
  core fix appears in the filed route, using overlap count only as a tiebreaker.
"""

import re
from typing import List, Optional, Tuple

# Use try/except for flexible importing (module vs standalone)
try:
    from .parser import Procedure
except ImportError:
    from parser import Procedure


def extract_core_fix(procedure_name: str) -> Optional[str]:
    """Extract the core fix name from a procedure name.

    Examples:
        "EGUKO 2L"  → "EGUKO"
        "ABASA 2J"  → "ABASA"
        "METRO 1L"  → "METRO"
        "IKILO 2J"  → "IKILO"

    Strips the trailing version/designator suffix (digit + letter).
    Falls back to the first word if it looks like a fix name.
    """
    name = procedure_name.strip()
    if not name:
        return None

    # Primary pattern: FIXNAME followed by space + digit(s) + optional letter(s)
    match = re.match(r'^([A-Z][A-Z0-9]{1,4})\s+\d+[A-Z]*$', name)
    if match:
        return match.group(1)

    # Fallback: first word if it's a valid fix name pattern
    parts = name.split()
    if parts and re.match(r'^[A-Z][A-Z0-9]{1,4}$', parts[0]):
        return parts[0]

    return None


def parse_route_string(route: str) -> List[str]:
    """Extract fix/waypoint names from a filed route string.

    Strips out:
    - "DCT" (direct-to)
    - Airway identifiers (e.g., A1, B576, L642, UL865, UM533)
    - SID/STAR procedure names embedded in route (handled by ignoring
      strings that don't match fix patterns)

    Returns: ordered list of fix names as they appear in the route.
    """
    fixes: List[str] = []
    parts = route.upper().split()

    for part in parts:
        part = part.strip()
        if not part or part == 'DCT':
            continue
        # Skip airway identifiers: 1-2 letters + digits (A1, B576, UL865, etc.)
        if re.match(r'^[A-Z]{1,2}\d+$', part):
            continue
        # Skip pure numbers (flight levels, speeds, etc.)
        if part.isdigit():
            continue
        # Keep valid fix names: 2-5 uppercase alphanumeric, starts with letter
        if re.match(r'^[A-Z][A-Z0-9]{1,4}$', part):
            fixes.append(part)

    return fixes


def match_procedures(
    route_fixes: List[str],
    runway: str,
    procedures: List[Procedure],
) -> List[dict]:
    """Match route fixes against available procedures for a given runway.

    Scoring (two-tier):
    1. Primary: does the procedure's core fix (derived from its name) appear
       in the filed route? This reliably disambiguates procedures sharing
       common fix segments.
    2. Secondary: count of overlapping fixes between route and procedure
       (tiebreaker when multiple procedures match the core fix, or when
       no core fix matches).

    Args:
        route_fixes: ordered list of fix names from the filed route
        runway: runway identifier (e.g., "25L") to filter compatible procedures
        procedures: list of Procedure objects to match against

    Returns:
        Sorted list of match result dicts (best match first):
        [{"name": str, "core_match": bool, "overlap": int, "core_fix": str|None}, ...]
    """
    route_set = set(route_fixes)
    results: List[dict] = []

    for proc in procedures:
        # Filter by runway compatibility
        # If procedure has no runways listed, it's compatible with all
        if runway and proc.runways and runway not in proc.runways:
            continue

        # Extract core fix from procedure name
        core_fix = extract_core_fix(proc.name)
        core_match = core_fix in route_set if core_fix else False

        # Count fix overlap
        proc_set = set(proc.fixes)
        overlap = len(route_set & proc_set)

        results.append({
            "name": proc.name,
            "core_match": core_match,
            "overlap": overlap,
            "core_fix": core_fix,
            "runways": proc.runways,
            "fixes": proc.fixes,
        })

    # Sort: core_match True first, then by overlap count descending
    results.sort(key=lambda x: (x["core_match"], x["overlap"]), reverse=True)

    return results
