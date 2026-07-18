"""
Bridge server: ties Aurora TCP bridge, sector parser, and route matcher
together, serving a WebSocket API for the web frontend.

Modes:
- Live: connects to Aurora, polls traffic, runs matcher, pushes via #LBWP
- Demo: generates realistic mock traffic for WIII (Jakarta) for UI development

Architecture mirrors Omar's Tower Strip plugin pattern:
  Aurora TCP (1130) ←→ Python asyncio bridge ←→ WebSocket ←→ Web frontend
"""

import asyncio
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Set

from aiohttp import web
import aiohttp

from .parser import Procedure, build_database
from .matcher import match_procedures, parse_route_string

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger('designator')


class DesignatorServer:
    """Main server: Aurora bridge + WebSocket API + static file serving."""

    def __init__(self):
        # Load config from environment
        self.aurora_host = os.getenv('AURORA_HOST', 'localhost')
        self.aurora_port = int(os.getenv('AURORA_PORT', '1130'))
        self.sector_path = os.getenv('SECTOR_FILES_PATH', '')
        self.poll_interval = int(os.getenv('POLL_INTERVAL', '5'))
        self.web_port = int(os.getenv('WEB_PORT', '8080'))
        self.demo_mode = os.getenv('DEMO_MODE', 'true').lower() == 'true'

        self.bridge = None  # AuroraBridge instance (live mode only)
        self.procedure_db: Dict[str, Dict[str, List[Procedure]]] = {}
        self.runway_config: Dict[str, Dict[str, List[str]]] = {}
        self.traffic_state: Dict[str, Dict[str, Any]] = {}
        self.ws_clients: Set[web.WebSocketResponse] = set()

        # aiohttp application
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        """Register HTTP and WebSocket routes."""
        frontend_dir = Path(__file__).parent.parent / 'frontend'

        self.app.router.add_get('/ws', self.websocket_handler)

        # Serve frontend static files
        if frontend_dir.exists():
            # Explicit index route
            self.app.router.add_get(
                '/',
                lambda r: web.FileResponse(frontend_dir / 'index.html')
            )
            self.app.router.add_static('/static', frontend_dir)
        else:
            logger.warning(f"Frontend directory not found: {frontend_dir}")

    def _detect_aurora_path(self) -> Optional[str]:
        """Scan standard local installations to find Aurora's Include directory."""
        common_paths = [
            r"C:\IVAO\Aurora\SectorFiles\Include",
            r"C:\Program Files\IVAO\Aurora\SectorFiles\Include",
            r"C:\Program Files (x86)\IVAO\Aurora\SectorFiles\Include",
        ]
        # Also check standard installation patterns on other drive letters
        for letter in ["D", "E", "F"]:
            common_paths.append(fr"{letter}:\IVAO\Aurora\SectorFiles\Include")

        for path_str in common_paths:
            path = Path(path_str)
            if path.exists() and path.is_dir():
                return path_str
        return None

    def _update_env_path(self, new_path: str) -> bool:
        """Dynamically write the configured folder path back to .env."""
        env_path = Path(__file__).parent.parent / '.env'
        if not env_path.exists():
            # If .env doesn't exist, create it from example
            example_path = Path(__file__).parent.parent / '.env.example'
            if example_path.exists():
                import shutil
                shutil.copy(str(example_path), str(env_path))
            else:
                env_path.touch()

        content = env_path.read_text(encoding='utf-8')
        # Normalize backslashes for .env compatibility
        clean_path = new_path.replace('\\', '/')

        if "SECTOR_FILES_PATH=" in content:
            updated = re.sub(
                r"SECTOR_FILES_PATH=.*",
                f"SECTOR_FILES_PATH={clean_path}",
                content
            )
        else:
            updated = content + f"\nSECTOR_FILES_PATH={clean_path}\n"

        env_path.write_text(updated, encoding='utf-8')
        os.environ['SECTOR_FILES_PATH'] = clean_path
        self.sector_path = clean_path
        return True

    @property
    def app_status(self) -> str:
        """Returns the setup state of the application."""
        if self.demo_mode:
            return 'ready'
        if self.sector_path and Path(self.sector_path).exists() and Path(self.sector_path).is_dir():
            return 'ready'
        return 'needs_setup'

    async def start(self):
        """Initialize procedure database and Aurora connection."""
        status = self.app_status
        if status == 'ready':
            if self.demo_mode:
                logger.info("Demo mode: using mock procedure data for WIII / WADY / WADD / WADL")
                self.procedure_db = self._build_demo_procedures()
                self.runway_config = self._build_demo_runway_config()
            else:
                logger.info(f"Loading sector files from: {self.sector_path}")
                self.procedure_db = build_database(self.sector_path)
                airport_count = len(self.procedure_db)
                total_sids = sum(len(v['sids']) for v in self.procedure_db.values())
                total_stars = sum(len(v['stars']) for v in self.procedure_db.values())
                logger.info(
                    f"Loaded {airport_count} airports: {total_sids} SIDs, {total_stars} STARs"
                )
        else:
            logger.warning(
                "No sector files path configured and not in demo mode. "
                "Setup GUI will block interface until configured."
            )

        # Connect to Aurora (live mode only)
        if not self.demo_mode:
            from .bridge import AuroraBridge
            self.bridge = AuroraBridge(self.aurora_host, self.aurora_port)
            try:
                await self.bridge.connect()
            except Exception as e:
                logger.error(f"Failed to connect to Aurora: {e}")
                logger.info("Server will retry connection during poll loop.")

        # Start the traffic polling loop
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        """Periodically fetch traffic data and broadcast to WebSocket clients."""
        while True:
            try:
                if self.app_status == 'ready':
                    if self.demo_mode:
                        self._update_demo_traffic()
                    else:
                        await self._update_live_traffic()

                await self._broadcast_state()
            except Exception as e:
                logger.error(f"Poll loop error: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    # ─── Live traffic update ────────────────────────────────────────────

    async def _update_live_traffic(self):
        """Fetch live traffic from Aurora, run matcher, update state."""
        if not self.bridge or not self.bridge.connected:
            # Try to reconnect
            try:
                from .bridge import AuroraBridge
                if not self.bridge:
                    self.bridge = AuroraBridge(self.aurora_host, self.aurora_port)
                await self.bridge.connect()
            except Exception:
                return

        try:
            # 1. Get runway config
            self.runway_config = await self.bridge.get_runway_config()
            # 2. Get traffic list
            callsigns = await self.bridge.get_traffic_list()

            # 3. For each callsign, fetch flight plan + position
            new_state: Dict[str, Dict] = {}
            for cs in callsigns:
                fp = await self.bridge.get_flight_plan(cs)
                pos = await self.bridge.get_traffic_position(cs)

                # Safe fallback if flight plan is missing or empty
                dep = fp.get('departure', '') if fp else ''
                arr = fp.get('arrival', '') if fp else ''
                route = fp.get('route', '') if fp else ''
                route_fixes = parse_route_string(route) if route else []

                altitude = pos.get('altitude', '') if pos else ''
                squawk = pos.get('squawk', '') if pos else ''
                current_wp = pos.get('waypoint', '') if pos else ''

                # Determine if departure, arrival, or overfly for controlled airports
                traffic_type = 'overfly'
                airport = ''
                runway = ''
                procedures = []

                if dep and dep in self.runway_config:
                    traffic_type = 'departure'
                    airport = dep
                    dep_rwys = self.runway_config[dep].get('dep_rwys', [])
                    runway = dep_rwys[0] if dep_rwys else ''
                    procedures = self.procedure_db.get(dep, {}).get('sids', [])

                elif arr and arr in self.runway_config:
                    traffic_type = 'arrival'
                    airport = arr
                    arr_rwys = self.runway_config[arr].get('arr_rwys', [])
                    runway = arr_rwys[0] if arr_rwys else ''
                    procedures = self.procedure_db.get(arr, {}).get('stars', [])

                else:
                    # Overfly default: show airport information if available in flight plan
                    airport = dep or arr or ''



                # Run matcher only if procedures are loaded
                suggestions = match_procedures(route_fixes, runway, procedures) if procedures else []

                # Get all procedure names for this runway
                all_procs = [
                    p.name for p in procedures
                    if not runway or not p.runways or runway in p.runways
                ] if procedures else []

                # Preserve existing assignment if any
                assigned = None
                if cs in self.traffic_state:
                    assigned = self.traffic_state[cs].get('assigned')

                new_state[cs] = {
                    'callsign': cs,
                    'type': traffic_type,
                    'departure': dep,
                    'arrival': arr,
                    'route': route,
                    'altitude': altitude,
                    'squawk': squawk,
                    'runway': runway,
                    'airport': airport,
                    'assigned': assigned,
                    'current_waypoint': current_wp,
                    'suggestions': suggestions[:5],
                    'all_procedures': all_procs,
                }

            self.traffic_state = new_state

        except Exception as e:
            logger.error(f"Live traffic update failed: {e}", exc_info=True)

    # ─── Demo data ──────────────────────────────────────────────────────

    def _build_demo_procedures(self) -> Dict[str, Dict[str, List[Procedure]]]:
        """Build mock procedure data for WIII, WADY, WADD, WADL (multi-aerodrome demo)."""

        # ── WIII (Soekarno-Hatta, Jakarta) ──────────────────────────────
        wiii_sids = [
            Procedure('WIII', ['25L'], 'EGUKO 2L', 0, [], ['EGUKO', 'ONILI', 'PAPAF']),
            Procedure('WIII', ['25L'], 'ABASA 2L', 0, [], ['ABASA', 'PAPAF', 'ELKIT']),
            Procedure('WIII', ['25L'], 'METRO 1L', 0, [], ['METRO', 'DKI', 'ONILI']),
            Procedure('WIII', ['25L'], 'IRWAK 1L', 0, [], ['IRWAK', 'ELKIT', 'NININ']),
            Procedure('WIII', ['25L'], 'TOPIN 1L', 0, [], ['TOPIN', 'PAPAF', 'ONILI']),
            Procedure('WIII', ['25R'], 'EGUKO 2R', 0, [], ['EGUKO', 'ONILI', 'PAPAF']),
            Procedure('WIII', ['25R'], 'ABASA 2R', 0, [], ['ABASA', 'PAPAF', 'ELKIT']),
            Procedure('WIII', ['25R'], 'METRO 1R', 0, [], ['METRO', 'DKI', 'ONILI']),
        ]
        wiii_common_tail = ['DKI', 'ONILI', 'PAPAF', 'ELKIT', 'NININ']
        wiii_stars = [
            Procedure('WIII', ['25R'], 'ABASA 2J', 0, [], ['ABASA'] + wiii_common_tail),
            Procedure('WIII', ['25R'], 'EGUKO 2J', 0, [], ['EGUKO'] + wiii_common_tail),
            Procedure('WIII', ['25R'], 'IKILO 2J', 0, [], ['IKILO'] + wiii_common_tail),
            Procedure('WIII', ['25R'], 'TASIA 2J', 0, [], ['TASIA'] + wiii_common_tail),
            Procedure('WIII', ['25R'], 'MESAM 2J', 0, [], ['MESAM'] + wiii_common_tail),
            Procedure('WIII', ['25L'], 'ABASA 2K', 0, [], ['ABASA'] + wiii_common_tail),
            Procedure('WIII', ['25L'], 'EGUKO 2K', 0, [], ['EGUKO'] + wiii_common_tail),
            Procedure('WIII', ['25L'], 'IKILO 2K', 0, [], ['IKILO'] + wiii_common_tail),
        ]

        # ── WADY (Selaparang / Lombok, now closed but used as demo) ────
        wady_sids = [
            Procedure('WADY', ['07'], 'LUBOK 1A', 0, [], ['LUBOK', 'ATLAN', 'OMPON']),
            Procedure('WADY', ['07'], 'SATUS 1A', 0, [], ['SATUS', 'OMPON', 'SALIP']),
            Procedure('WADY', ['25'], 'LUBOK 1B', 0, [], ['LUBOK', 'ATLAN', 'OMPON']),
            Procedure('WADY', ['25'], 'SATUS 1B', 0, [], ['SATUS', 'OMPON', 'SALIP']),
        ]
        wady_stars = [
            Procedure('WADY', ['07'], 'TOGAM 1A', 0, [], ['TOGAM', 'ATLAN', 'OMPON']),
            Procedure('WADY', ['07'], 'MESOP 1A', 0, [], ['MESOP', 'OMPON', 'SALIP']),
            Procedure('WADY', ['25'], 'TOGAM 1B', 0, [], ['TOGAM', 'ATLAN', 'OMPON']),
            Procedure('WADY', ['25'], 'MESOP 1B', 0, [], ['MESOP', 'OMPON', 'SALIP']),
        ]

        # ── WADD (Ngurah Rai, Bali) ─────────────────────────────────────
        wadd_sids = [
            Procedure('WADD', ['09'], 'VINAM 3A', 0, [], ['VINAM', 'TOGAM', 'L517']),
            Procedure('WADD', ['09'], 'IDOSI 2A', 0, [], ['IDOSI', 'TOGAM', 'B480']),
            Procedure('WADD', ['09'], 'LUBOK 1D', 0, [], ['LUBOK', 'TOGAM', 'B462']),
            Procedure('WADD', ['09'], 'BLI 1A', 0, [], ['BLI', 'TOGAM']),
            Procedure('WADD', ['09'], 'DIOLA 1A', 0, [], ['DIOLA', 'TOGAM']),
            Procedure('WADD', ['27'], 'VINAM 3B', 0, [], ['VINAM', 'TOGAM', 'L517']),
            Procedure('WADD', ['27'], 'IDOSI 2B', 0, [], ['IDOSI', 'TOGAM', 'B480']),
            Procedure('WADD', ['27'], 'LUBOK 1C', 0, [], ['LUBOK', 'TOGAM', 'B462']),
        ]
        wadd_stars = [
            Procedure('WADD', ['09'], 'TOGAM 2A', 0, [], ['TOGAM', 'L517', 'EGUKO']),
            Procedure('WADD', ['09'], 'RUTIN 1A', 0, [], ['RUTIN', 'B347', 'TOGAM']),
            Procedure('WADD', ['09'], 'BLI 1B', 0, [], ['BLI', 'TOGAM']),
            Procedure('WADD', ['09'], 'DIOLA 1B', 0, [], ['DIOLA', 'TOGAM']),
            Procedure('WADD', ['27'], 'TOGAM 2B', 0, [], ['TOGAM', 'L517', 'EGUKO']),
            Procedure('WADD', ['27'], 'RUTIN 1B', 0, [], ['RUTIN', 'B347', 'TOGAM']),
            Procedure('WADD', ['27'], 'LIMLA 1A', 0, [], ['LIMLA', 'A464', 'TOGAM']),
        ]

        # ── WADL (Lombok Praya International) ──────────────────────────
        wadl_sids = [
            Procedure('WADL', ['14'], 'OMPON 1A', 0, [], ['OMPON', 'SALIP', 'B462']),
            Procedure('WADL', ['14'], 'ATLAN 1A', 0, [], ['ATLAN', 'OMPON', 'SALIP']),
            Procedure('WADL', ['32'], 'OMPON 1B', 0, [], ['OMPON', 'SALIP', 'B462']),
            Procedure('WADL', ['32'], 'ATLAN 1B', 0, [], ['ATLAN', 'OMPON', 'SALIP']),
        ]
        wadl_stars = [
            Procedure('WADL', ['14'], 'SALIP 1A', 0, [], ['SALIP', 'OMPON', 'ATLAN']),
            Procedure('WADL', ['14'], 'TOGAM 1C', 0, [], ['TOGAM', 'B462', 'OMPON']),
            Procedure('WADL', ['32'], 'SALIP 1B', 0, [], ['SALIP', 'OMPON', 'ATLAN']),
            Procedure('WADL', ['32'], 'TOGAM 1D', 0, [], ['TOGAM', 'B462', 'OMPON']),
        ]

        return {
            'WIII': {'sids': wiii_sids, 'stars': wiii_stars},
            'WADY': {'sids': wady_sids, 'stars': wady_stars},
            'WADD': {'sids': wadd_sids, 'stars': wadd_stars},
            'WADL': {'sids': wadl_sids, 'stars': wadl_stars},
        }

    def _build_demo_runway_config(self) -> Dict[str, Dict[str, List[str]]]:
        """Multi-aerodrome runway config — each with potentially multiple active runways."""
        return {
            'WIII': {'dep_rwys': ['25L', '25R'], 'arr_rwys': ['25R', '25L']},
            'WADY': {'dep_rwys': ['25'],          'arr_rwys': ['07']},
            'WADD': {'dep_rwys': ['09', '27'],    'arr_rwys': ['09']},
            'WADL': {'dep_rwys': ['14'],          'arr_rwys': ['32']},
        }

    def _update_demo_traffic(self):
        """Generate/update mock traffic state for demo mode."""
        # Only generate once, then just update altitudes slightly for "liveness"
        if not self.traffic_state:
            self.traffic_state = self._generate_demo_traffic()
        else:
            # Simulate altitude changes for visual feedback
            for cs, data in self.traffic_state.items():
                if data.get('assigned'):
                    continue  # Don't change assigned aircraft
                alt = data.get('altitude', '0')
                try:
                    alt_num = int(alt)
                    # Small random altitude change for realism
                    alt_num += random.randint(-200, 200)
                    alt_num = max(0, alt_num)
                    data['altitude'] = str(alt_num)
                except (ValueError, TypeError):
                    pass

    def _generate_demo_traffic(self) -> Dict[str, Dict[str, Any]]:
        """Create initial demo traffic for WIII, WADY, WADD, and WADL."""

        def get_rwy(apt, kind):
            """Helper: get first dep/arr runway for an aerodrome."""
            key = 'dep_rwys' if kind == 'dep' else 'arr_rwys'
            return self.runway_config.get(apt, {}).get(key, ['--'])[0]

        raw_traffic = [
            # ── WIII departures ────────────────────────────────────────
            {
                'callsign': 'GIA123', 'type': 'departure',
                'departure': 'WIII',  'arrival': 'WMKK',
                'route':    'EGUKO B576 ONILI PAPAF TOPIN VINAM',
                'altitude': '1200', 'squawk': '2601',
                'runway': get_rwy('WIII', 'dep'), 'airport': 'WIII',
            },
            {
                'callsign': 'BTK456', 'type': 'departure',
                'departure': 'WIII',  'arrival': 'VHHH',
                'route':    'ABASA PAPAF ELKIT L642 IDOSI',
                'altitude': '850',  'squawk': '4512',
                'runway': get_rwy('WIII', 'dep'), 'airport': 'WIII',
            },
            {
                'callsign': 'NAM789', 'type': 'departure',
                'departure': 'WIII',  'arrival': 'WADD',
                'route':    'METRO DKI ONILI UL865 VINAM',
                'altitude': '0',    'squawk': '2345',
                'runway': get_rwy('WIII', 'dep'), 'airport': 'WIII',
            },
            {
                'callsign': 'LNI012', 'type': 'departure',
                'departure': 'WIII',  'arrival': 'WSSS',
                'route':    'IRWAK ELKIT NININ A576 UGAMI',
                'altitude': '2500', 'squawk': '3218',
                'runway': get_rwy('WIII', 'dep'), 'airport': 'WIII',
            },
            # ── WIII arrivals ──────────────────────────────────────────
            {
                'callsign': 'SJY234', 'type': 'arrival',
                'departure': 'WSSS',  'arrival': 'WIII',
                'route':    'RUTIN B347 ABASA DKI ONILI PAPAF ELKIT NININ',
                'altitude': '15000', 'squawk': '4521',
                'runway': get_rwy('WIII', 'arr'), 'airport': 'WIII',
            },
            {
                'callsign': 'CTV567', 'type': 'arrival',
                'departure': 'WADD',  'arrival': 'WIII',
                'route':    'TOGAM L517 EGUKO DKI ONILI PAPAF ELKIT NININ',
                'altitude': '22000', 'squawk': '3456',
                'runway': get_rwy('WIII', 'arr'), 'airport': 'WIII',
            },
            {
                'callsign': 'AWQ890', 'type': 'arrival',
                'departure': 'WMKK',  'arrival': 'WIII',
                'route':    'MESOP B586 IKILO DKI ONILI PAPAF ELKIT NININ',
                'altitude': '18000', 'squawk': '5678',
                'runway': get_rwy('WIII', 'arr'), 'airport': 'WIII',
            },
            {
                'callsign': 'GIA345', 'type': 'arrival',
                'departure': 'VTBS',  'arrival': 'WIII',
                'route':    'LIMLA A464 TASIA DKI ONILI PAPAF ELKIT NININ',
                'altitude': '35000', 'squawk': '6789',
                'runway': get_rwy('WIII', 'arr'), 'airport': 'WIII',
            },
            # ── WADY departures ────────────────────────────────────────
            {
                'callsign': 'GIA501', 'type': 'departure',
                'departure': 'WADY',  'arrival': 'WIII',
                'route':    'LUBOK ATLAN OMPON L517 DKI',
                'altitude': '0',    'squawk': '2701',
                'runway': get_rwy('WADY', 'dep'), 'airport': 'WADY',
            },
            {
                'callsign': 'LNI203', 'type': 'departure',
                'departure': 'WADY',  'arrival': 'WSSS',
                'route':    'SATUS OMPON SALIP B462 UGAMI',
                'altitude': '1500', 'squawk': '3301',
                'runway': get_rwy('WADY', 'dep'), 'airport': 'WADY',
            },
            # ── WADY arrivals ──────────────────────────────────────────
            {
                'callsign': 'BTK701', 'type': 'arrival',
                'departure': 'WIII',  'arrival': 'WADY',
                'route':    'METRO DKI OMPON TOGAM ATLAN',
                'altitude': '12000', 'squawk': '4601',
                'runway': get_rwy('WADY', 'arr'), 'airport': 'WADY',
            },
            {
                'callsign': 'SJY402', 'type': 'arrival',
                'departure': 'WADD',  'arrival': 'WADY',
                'route':    'TOGAM B462 OMPON ATLAN MESOP',
                'altitude': '8000',  'squawk': '5401',
                'runway': get_rwy('WADY', 'arr'), 'airport': 'WADY',
            },
            # ── WADD departures ────────────────────────────────────────
            {
                'callsign': 'GIA611', 'type': 'departure',
                'departure': 'WADD',  'arrival': 'WIII',
                'route':    'VINAM TOGAM L517 EGUKO DKI',
                'altitude': '2000', 'squawk': '2801',
                'runway': get_rwy('WADD', 'dep'), 'airport': 'WADD',
            },
            {
                'callsign': 'CTV312', 'type': 'departure',
                'departure': 'WADD',  'arrival': 'VHHH',
                'route':    'IDOSI TOGAM B480 UGAMI',
                'altitude': '3500', 'squawk': '3811',
                'runway': get_rwy('WADD', 'dep'), 'airport': 'WADD',
            },
            {
                'callsign': 'NAM550', 'type': 'departure',
                'departure': 'WADD',  'arrival': 'WSSS',
                'route':    'LUBOK TOGAM B462 UGAMI',
                'altitude': '0',    'squawk': '2811',
                'runway': get_rwy('WADD', 'dep'), 'airport': 'WADD',
            },
            # ── WADD arrivals ──────────────────────────────────────────
            {
                'callsign': 'AWQ210', 'type': 'arrival',
                'departure': 'WSSS',  'arrival': 'WADD',
                'route':    'RUTIN B347 TOGAM L517 EGUKO',
                'altitude': '28000', 'squawk': '4811',
                'runway': get_rwy('WADD', 'arr'), 'airport': 'WADD',
            },
            {
                'callsign': 'GIA712', 'type': 'arrival',
                'departure': 'VTBS',  'arrival': 'WADD',
                'route':    'LIMLA A464 TOGAM L517',
                'altitude': '32000', 'squawk': '5811',
                'runway': get_rwy('WADD', 'arr'), 'airport': 'WADD',
            },
            # ── WADL departures ────────────────────────────────────────
            {
                'callsign': 'LNI450', 'type': 'departure',
                'departure': 'WADL',  'arrival': 'WIII',
                'route':    'OMPON SALIP B462 DKI ONILI',
                'altitude': '0',    'squawk': '2901',
                'runway': get_rwy('WADL', 'dep'), 'airport': 'WADL',
            },
            {
                'callsign': 'BTK820', 'type': 'departure',
                'departure': 'WADL',  'arrival': 'WADD',
                'route':    'ATLAN OMPON TOGAM B462',
                'altitude': '1000', 'squawk': '3901',
                'runway': get_rwy('WADL', 'dep'), 'airport': 'WADL',
            },
            # ── WADL arrivals ──────────────────────────────────────────
            {
                'callsign': 'CTV650', 'type': 'arrival',
                'departure': 'WADD',  'arrival': 'WADL',
                'route':    'TOGAM B462 OMPON SALIP ATLAN',
                'altitude': '10000', 'squawk': '4901',
                'runway': get_rwy('WADL', 'arr'), 'airport': 'WADL',
            },
        ]

        traffic = {}
        for ac in raw_traffic:
            cs  = ac['callsign']
            apt = ac['airport']
            procs = self.procedure_db.get(apt, {})

            if ac['type'] == 'departure':
                procedure_list = procs.get('sids', [])
            else:
                procedure_list = procs.get('stars', [])

            route_fixes = parse_route_string(ac['route'])
            suggestions = match_procedures(route_fixes, ac['runway'], procedure_list)
            all_procs   = [
                p.name for p in procedure_list
                if not ac['runway'] or not p.runways or ac['runway'] in p.runways
            ]

            ac['assigned']         = None
            ac['current_waypoint'] = ''
            ac['suggestions']      = suggestions[:5]
            ac['all_procedures']   = all_procs
            traffic[cs] = ac

        return traffic

    # ─── WebSocket API ──────────────────────────────────────────────────

    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connections from the frontend."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_clients.add(ws)
        logger.info(f"WebSocket client connected ({len(self.ws_clients)} total)")

        # Send initial state immediately
        try:
            await ws.send_str(json.dumps({
                'type': 'traffic_update',
                'status': self.app_status,
                'aurora_connected': self.bridge.connected if self.bridge else False,
                'demo_mode': self.demo_mode,
                'detected_path': self._detect_aurora_path(),
                'traffic': self.traffic_state,
                'runway_config': self.runway_config,
            }))
        except Exception:
            pass

        # Listen for commands from frontend
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        cmd_type = data.get('type', '')

                        if cmd_type == 'assign':
                            await self._handle_assign(data, ws)
                        elif cmd_type == 'unassign':
                            await self._handle_unassign(data, ws)
                        elif cmd_type == 'set_path':
                            await self._handle_set_path(data, ws)
                        else:
                            logger.warning(f"Unknown command type: {cmd_type}")

                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from client: {msg.data}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    break
        finally:
            self.ws_clients.discard(ws)
            logger.info(f"WebSocket client disconnected ({len(self.ws_clients)} total)")

        return ws

    async def _handle_set_path(self, data: dict, ws: web.WebSocketResponse):
        """Validate and set the configured Aurora path."""
        path = data.get('path', '').strip()
        path_obj = Path(path)
        if path_obj.exists() and path_obj.is_dir():
            self._update_env_path(path)
            self.procedure_db = build_database(path)
            logger.info(f"Sector database re-loaded from user-configured path: {path}")

            # Count procedures loaded
            airport_count = len(self.procedure_db)
            total_sids = sum(len(v['sids']) for v in self.procedure_db.values())
            total_stars = sum(len(v['stars']) for v in self.procedure_db.values())
            logger.info(
                f"Parsed {airport_count} airports: {total_sids} SIDs, {total_stars} STARs"
            )

            # If demo mode is active but we just successfully loaded sector files,
            # we should keep demo mode unless user wants it off, but we can verify it parsed.
            await ws.send_str(json.dumps({
                'type': 'set_path_result',
                'success': True,
                'airport_count': airport_count,
                'sid_count': total_sids,
                'star_count': total_stars,
            }))
        else:
            await ws.send_str(json.dumps({
                'type': 'set_path_result',
                'success': False,
                'error': 'The folder does not exist or is not a directory.',
            }))

        await self._broadcast_state()


    async def _handle_assign(self, data: dict, ws: web.WebSocketResponse):
        """Handle an assign command from the frontend."""
        callsign = data.get('callsign', '')
        procedure = data.get('procedure', '')

        if not callsign or not procedure:
            await ws.send_str(json.dumps({
                'type': 'assign_result',
                'callsign': callsign,
                'procedure': procedure,
                'success': False,
                'error': 'Missing callsign or procedure',
            }))
            return

        if self.demo_mode:
            # Simulate assignment success
            if callsign in self.traffic_state:
                self.traffic_state[callsign]['assigned'] = procedure
                self.traffic_state[callsign]['current_waypoint'] = procedure

            await ws.send_str(json.dumps({
                'type': 'assign_result',
                'callsign': callsign,
                'procedure': procedure,
                'success': True,
            }))
            logger.info(f"[DEMO] Assigned {procedure} to {callsign}")

        else:
            # Live: push via #LBWP
            try:
                resp = await self.bridge.assign_waypoint(callsign, procedure)
                if resp.startswith('@ERR'):
                    error_msg = resp.split(';')[-1].strip() if ';' in resp else resp
                    if not error_msg:
                        error_msg = "Command failed"
                    if "traffic not assumed" in error_msg.lower():
                        error_msg += " (Please assume the aircraft in Aurora first)"

                    await ws.send_str(json.dumps({
                        'type': 'assign_result',
                        'callsign': callsign,
                        'procedure': procedure,
                        'success': False,
                        'error': error_msg,
                    }))
                else:
                    if callsign in self.traffic_state:
                        self.traffic_state[callsign]['assigned'] = procedure

                    await ws.send_str(json.dumps({
                        'type': 'assign_result',
                        'callsign': callsign,
                        'procedure': procedure,
                        'success': True,
                        'aurora_response': resp,
                    }))
            except Exception as e:
                await ws.send_str(json.dumps({
                    'type': 'assign_result',
                    'callsign': callsign,
                    'procedure': procedure,
                    'success': False,
                    'error': str(e),
                }))

        # Broadcast updated state to all clients
        await self._broadcast_state()

    async def _handle_unassign(self, data: dict, ws: web.WebSocketResponse):
        """Handle unassign/clear command."""
        callsign = data.get('callsign', '')
        if not callsign:
            return

        if self.demo_mode:
            if callsign in self.traffic_state:
                self.traffic_state[callsign]['assigned'] = None
                self.traffic_state[callsign]['current_waypoint'] = ''
            await ws.send_str(json.dumps({
                'type': 'unassign_result',
                'callsign': callsign,
                'success': True,
            }))
        else:
            # Live unassign
            try:
                resp = await self.bridge.assign_waypoint(callsign, '')
                if resp.startswith('@ERR'):
                    error_msg = resp.split(';')[-1].strip() if ';' in resp else resp
                    if not error_msg:
                        error_msg = "Command failed"
                    if "traffic not assumed" in error_msg.lower():
                        error_msg += " (Please assume the aircraft in Aurora first)"

                    await ws.send_str(json.dumps({
                        'type': 'unassign_result',
                        'callsign': callsign,
                        'success': False,
                        'error': error_msg,
                    }))
                else:
                    if callsign in self.traffic_state:
                        self.traffic_state[callsign]['assigned'] = None
                        self.traffic_state[callsign]['current_waypoint'] = ''
                    await ws.send_str(json.dumps({
                        'type': 'unassign_result',
                        'callsign': callsign,
                        'success': True,
                    }))
            except Exception as e:
                await ws.send_str(json.dumps({
                    'type': 'unassign_result',
                    'callsign': callsign,
                    'success': False,
                    'error': str(e),
                }))

        await self._broadcast_state()

    async def _broadcast_state(self):
        """Send current traffic state to all connected WebSocket clients."""
        if not self.ws_clients:
            return

        msg = json.dumps({
            'type': 'traffic_update',
            'status': self.app_status,
            'aurora_connected': self.bridge.connected if self.bridge else False,
            'demo_mode': self.demo_mode,
            'detected_path': self._detect_aurora_path(),
            'traffic': self.traffic_state,
            'runway_config': self.runway_config,
        })

        dead: Set[web.WebSocketResponse] = set()
        for ws in self.ws_clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        self.ws_clients -= dead


# ─── Entry point ────────────────────────────────────────────────────────

async def main():
    from dotenv import load_dotenv

    # Load .env from project root
    env_path = Path(__file__).parent.parent / '.env'
    load_dotenv(env_path)

    server = DesignatorServer()
    await server.start()

    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', server.web_port)
    await site.start()

    mode = "DEMO" if server.demo_mode else "LIVE"
    logger.info(f"✈  STAR/SID Designator [{mode}] running at http://localhost:{server.web_port}")

    # Keep running forever
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await runner.cleanup()
        if server.bridge and server.bridge.connected:
            await server.bridge.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
