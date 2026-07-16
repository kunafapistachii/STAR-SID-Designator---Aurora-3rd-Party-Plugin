"""
Sector file procedure parser for Aurora .SID / .STR files.

Parses procedure definitions from Aurora sector files, extracting:
- Procedure name, ICAO code, compatible runways
- Procedure type (SID/STAR=0, Transition=1, Holding=2, IAP=3, FAP=4, GoAround=5)
- Named fixes along the procedure route

Data format reference (from star-sid-designator-context.md):
- LABEL line: ICAO;RWY1:RWY2;ProcedureName;LabelLat;LabelLon;[Type];[Transition1 Transition2...]
- TRACK lines: either lat/lon coordinate pairs (geometry) or FIXNAME;FIXNAME; (named fixes)
- <br> tags mark drawing discontinuities — stripped before parsing

Validated against: WIII (89 SID, 33 STAR), full WIIF FIR (23 airports).
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class Procedure:
    """A single SID/STAR/approach procedure parsed from a sector file."""
    icao: str
    runways: List[str]
    name: str
    proc_type: int  # 0=SID/STAR, 1=Transition, 2=Holding, 3=IAP, 4=FAP, 5=GoAround
    transitions: List[str]
    fixes: List[str]

    def to_dict(self) -> dict:
        return {
            "icao": self.icao,
            "runways": self.runways,
            "name": self.name,
            "proc_type": self.proc_type,
            "transitions": self.transitions,
            "fixes": self.fixes,
        }


def _is_coordinate(value: str) -> bool:
    """Check if a string looks like a lat/lon coordinate.
    Aurora format: S06.07.22.000 / E106.39.17.000 / N12.34.56.000 / W045.12.00.000
    """
    return bool(re.match(r'^[NSEW]-?\d', value))


def _is_header_line(fields: List[str]) -> bool:
    """Check if a semicolon-split line is a procedure LABEL (header) line.

    Requires:
    - First field is exactly 4 uppercase letters (ICAO code)
    - At least 3 fields total (ICAO, runways, procedure name)

    This strict check prevents 3-letter fix names (e.g. "DKI") on track lines
    from being misidentified as ICAO headers — a bug already caught and fixed
    during real-data testing.
    """
    if len(fields) < 3:
        return False
    icao_candidate = fields[0].strip()
    return bool(re.match(r'^[A-Z]{4}$', icao_candidate))


def parse_procedure_file(path: str) -> List[Procedure]:
    """Parse a single .SID or .STR file into a list of Procedure objects.

    Handles messy real-world data: missing trailing semicolons, <br> tags,
    stray whitespace, inconsistent formatting.
    """
    procedures: List[Procedure] = []
    current_proc: Optional[Procedure] = None

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith('//'):
                continue

            # Strip <br> tags (drawing discontinuity markers)
            line = re.sub(r'<br\s*/?\s*>', '', line, flags=re.IGNORECASE)
            line = line.strip()
            if not line:
                continue

            # Split by semicolon, keep non-empty fields
            fields = [f.strip() for f in line.split(';') if f.strip()]
            if not fields:
                continue

            # --- LABEL (header) line ---
            if _is_header_line(fields):
                # Save the previous procedure before starting a new one
                if current_proc is not None:
                    procedures.append(current_proc)

                icao = fields[0].strip()
                runways_raw = fields[1] if len(fields) > 1 else ''
                runways = [r.strip() for r in runways_raw.split(':') if r.strip()]
                name = fields[2].strip() if len(fields) > 2 else ''
                # Fields 3,4 = label lat/lon (skip)
                proc_type_str = fields[5].strip() if len(fields) > 5 else '0'
                proc_type = int(proc_type_str) if proc_type_str.isdigit() else 0
                transitions_raw = fields[6].strip() if len(fields) > 6 else ''
                transitions = transitions_raw.split() if transitions_raw else []

                current_proc = Procedure(
                    icao=icao,
                    runways=runways,
                    name=name,
                    proc_type=proc_type,
                    transitions=transitions,
                    fixes=[],
                )

            # --- TRACK line (belongs to current procedure) ---
            elif current_proc is not None:
                # Skip lines that contain coordinate data (geometry only)
                if any(_is_coordinate(f) for f in fields):
                    continue

                # Remaining fields should be fix names.
                # Pattern: FIXNAME;FIXNAME; — same name repeated (one fix per line)
                # Collect unique fix names, preserving order of first appearance.
                for f_val in fields:
                    f_val = f_val.strip()
                    # Valid fix name: 2-5 uppercase alphanumeric, starts with letter
                    if re.match(r'^[A-Z][A-Z0-9]{1,4}$', f_val):
                        if f_val not in current_proc.fixes:
                            current_proc.fixes.append(f_val)

    # Don't forget the last procedure in the file
    if current_proc is not None:
        procedures.append(current_proc)

    return procedures


def scan_and_pair_procedure_files(folder: str) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """Recursively scan a folder for .sid/.str file pairs, grouped by ICAO.

    Returns: {ICAO: (sid_path_or_None, str_path_or_None)}
    Case-insensitive file matching (e.g. wiii.SID and WIII.str both map to WIII).
    """
    pairs: Dict[str, list] = {}
    folder_path = Path(folder)

    for fpath in folder_path.rglob('*'):
        ext = fpath.suffix.lower()
        if ext in ('.sid', '.str'):
            icao = fpath.stem.upper()
            if icao not in pairs:
                pairs[icao] = [None, None]
            if ext == '.sid':
                pairs[icao][0] = str(fpath)
            else:
                pairs[icao][1] = str(fpath)

    return {icao: (paths[0], paths[1]) for icao, paths in pairs.items()}


def build_database(folder: str) -> Dict[str, Dict[str, List[Procedure]]]:
    """Recursively scan an Include folder for <FIR>/Airports/ subfolders,
    and parse all procedure files found inside them.

    Returns: {ICAO: {"sids": [Procedure, ...], "stars": [Procedure, ...]}}
    """
    database: Dict[str, Dict[str, List[Procedure]]] = {}
    folder_path = Path(folder)
    if not folder_path.exists():
        return database

    # Search for directories named 'airports' under the parent folder
    for airports_dir in folder_path.rglob('*'):
        if airports_dir.is_dir() and airports_dir.name.lower() == 'airports':
            # Found an Airports directory, scan it for sids/stars
            pairs = scan_and_pair_procedure_files(str(airports_dir))
            for icao, (sid_path, str_path) in pairs.items():
                sids: List[Procedure] = []
                stars: List[Procedure] = []

                if sid_path:
                    all_procs = parse_procedure_file(sid_path)
                    sids = [p for p in all_procs if p.proc_type == 0]

                if str_path:
                    all_procs = parse_procedure_file(str_path)
                    stars = [p for p in all_procs if p.proc_type == 0]

                if sids or stars:
                    if icao not in database:
                        database[icao] = {"sids": [], "stars": []}
                    database[icao]["sids"].extend(sids)
                    database[icao]["stars"].extend(stars)

    return database

