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
                logger.info("Demo mode: using mock procedure data for WIII")
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

                if not fp:
                    continue

                dep = fp.get('departure', '')
                arr = fp.get('arrival', '')
                route = fp.get('route', '')
                route_fixes = parse_route_string(route)

                altitude = pos.get('altitude', '') if pos else ''
                squawk = pos.get('squawk', '') if pos else ''
                current_wp = pos.get('waypoint', '') if pos else ''

                # Determine if departure or arrival for a controlled airport
                traffic_type = None
                airport = ''
                runway = ''
                procedures = []

                if dep in self.runway_config and dep in self.procedure_db:
                    traffic_type = 'departure'
                    airport = dep
                    dep_rwys = self.runway_config[dep].get('dep_rwys', [])
                    runway = dep_rwys[0] if dep_rwys else ''
                    procedures = self.procedure_db[dep].get('sids', [])

                elif arr in self.runway_config and arr in self.procedure_db:
                    traffic_type = 'arrival'
                    airport = arr
                    arr_rwys = self.runway_config[arr].get('arr_rwys', [])
                    runway = arr_rwys[0] if arr_rwys else ''
                    procedures = self.procedure_db[arr].get('stars', [])

                if not traffic_type:
                    continue

                # Run matcher
                suggestions = match_procedures(route_fixes, runway, procedures)

                # Get all procedure names for this runway
                all_procs = [
                    p.name for p in procedures
                    if not runway or not p.runways or runway in p.runways
                ]

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
                    'suggestions': suggestions[:5],  # Top 5 matches
                    'all_procedures': all_procs,
                }

            self.traffic_state = new_state

        except Exception as e:
            logger.error(f"Live traffic update failed: {e}", exc_info=True)

    # ─── Demo data ──────────────────────────────────────────────────────

    def _build_demo_procedures(self) -> Dict[str, Dict[str, List[Procedure]]]:
        """Build mock procedure data for WIII (Soekarno-Hatta, Jakarta)."""
        sids = [
            Procedure('WIII', ['25L'], 'EGUKO 2L', 0, [],
                      ['EGUKO', 'ONILI', 'PAPAF']),
            Procedure('WIII', ['25L'], 'ABASA 2L', 0, [],
                      ['ABASA', 'PAPAF', 'ELKIT']),
            Procedure('WIII', ['25L'], 'METRO 1L', 0, [],
                      ['METRO', 'DKI', 'ONILI']),
            Procedure('WIII', ['25L'], 'IRWAK 1L', 0, [],
                      ['IRWAK', 'ELKIT', 'NININ']),
            Procedure('WIII', ['25L'], 'TOPIN 1L', 0, [],
                      ['TOPIN', 'PAPAF', 'ONILI']),
            Procedure('WIII', ['25R'], 'EGUKO 2R', 0, [],
                      ['EGUKO', 'ONILI', 'PAPAF']),
            Procedure('WIII', ['25R'], 'ABASA 2R', 0, [],
                      ['ABASA', 'PAPAF', 'ELKIT']),
            Procedure('WIII', ['25R'], 'METRO 1R', 0, [],
                      ['METRO', 'DKI', 'ONILI']),
        ]

        # STARs with common tail segment (DKI/ONILI/PAPAF/ELKIT/NININ)
        # — the pattern that makes naive overlap matching fail
        common_tail = ['DKI', 'ONILI', 'PAPAF', 'ELKIT', 'NININ']
        stars = [
            Procedure('WIII', ['25R'], 'ABASA 2J', 0, [],
                      ['ABASA'] + common_tail),
            Procedure('WIII', ['25R'], 'EGUKO 2J', 0, [],
                      ['EGUKO'] + common_tail),
            Procedure('WIII', ['25R'], 'IKILO 2J', 0, [],
                      ['IKILO'] + common_tail),
            Procedure('WIII', ['25R'], 'TASIA 2J', 0, [],
                      ['TASIA'] + common_tail),
            Procedure('WIII', ['25R'], 'MESAM 2J', 0, [],
                      ['MESAM'] + common_tail),
            Procedure('WIII', ['25L'], 'ABASA 2K', 0, [],
                      ['ABASA'] + common_tail),
            Procedure('WIII', ['25L'], 'EGUKO 2K', 0, [],
                      ['EGUKO'] + common_tail),
            Procedure('WIII', ['25L'], 'IKILO 2K', 0, [],
                      ['IKILO'] + common_tail),
        ]

        return {'WIII': {'sids': sids, 'stars': stars}}

    def _build_demo_runway_config(self) -> Dict[str, Dict[str, List[str]]]:
        return {
            'WIII': {'dep_rwys': ['25L'], 'arr_rwys': ['25R']},
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
        """Create initial demo traffic for WIII."""
        sids = self.procedure_db.get('WIII', {}).get('sids', [])
        stars = self.procedure_db.get('WIII', {}).get('stars', [])
        dep_rwy = self.runway_config.get('WIII', {}).get('dep_rwys', ['25L'])[0]
        arr_rwy = self.runway_config.get('WIII', {}).get('arr_rwys', ['25R'])[0]

        departures = [
            {
                'callsign': 'GIA123',
                'type': 'departure',
                'departure': 'WIII',
                'arrival': 'WMKK',
                'route': 'EGUKO B576 ONILI PAPAF TOPIN VINAM',
                'altitude': '1200',
                'squawk': '2601',
                'runway': dep_rwy,
                'airport': 'WIII',
            },
            {
                'callsign': 'BTK456',
                'type': 'departure',
                'departure': 'WIII',
                'arrival': 'VHHH',
                'route': 'ABASA PAPAF ELKIT L642 IDOSI',
                'altitude': '850',
                'squawk': '4512',
                'runway': dep_rwy,
                'airport': 'WIII',
            },
            {
                'callsign': 'NAM789',
                'type': 'departure',
                'departure': 'WIII',
                'arrival': 'WADD',
                'route': 'METRO DKI ONILI UL865 VINAM',
                'altitude': '0',
                'squawk': '2345',
                'runway': dep_rwy,
                'airport': 'WIII',
            },
            {
                'callsign': 'LNI012',
                'type': 'departure',
                'departure': 'WIII',
                'arrival': 'WSSS',
                'route': 'IRWAK ELKIT NININ A576 UGAMI',
                'altitude': '2500',
                'squawk': '3218',
                'runway': dep_rwy,
                'airport': 'WIII',
            },
        ]

        arrivals = [
            {
                'callsign': 'SJY234',
                'type': 'arrival',
                'departure': 'WSSS',
                'arrival': 'WIII',
                'route': 'RUTIN B347 ABASA DKI ONILI PAPAF ELKIT NININ',
                'altitude': '15000',
                'squawk': '4521',
                'runway': arr_rwy,
                'airport': 'WIII',
            },
            {
                'callsign': 'CTV567',
                'type': 'arrival',
                'departure': 'WADD',
                'arrival': 'WIII',
                'route': 'TOGAM L517 EGUKO DKI ONILI PAPAF ELKIT NININ',
                'altitude': '22000',
                'squawk': '3456',
                'runway': arr_rwy,
                'airport': 'WIII',
            },
            {
                'callsign': 'AWQ890',
                'type': 'arrival',
                'departure': 'WMKK',
                'arrival': 'WIII',
                'route': 'MESOP B586 IKILO DKI ONILI PAPAF ELKIT NININ',
                'altitude': '18000',
                'squawk': '5678',
                'runway': arr_rwy,
                'airport': 'WIII',
            },
            {
                'callsign': 'GIA345',
                'type': 'arrival',
                'departure': 'VTBS',
                'arrival': 'WIII',
                'route': 'LIMLA A464 TASIA DKI ONILI PAPAF ELKIT NININ',
                'altitude': '35000',
                'squawk': '6789',
                'runway': arr_rwy,
                'airport': 'WIII',
            },
        ]

        traffic = {}
        for ac in departures + arrivals:
            cs = ac['callsign']
            route_fixes = parse_route_string(ac['route'])

            # Pick the right procedure list
            if ac['type'] == 'departure':
                procs = sids
            else:
                procs = stars

            suggestions = match_procedures(route_fixes, ac['runway'], procs)

            all_procs = [
                p.name for p in procs
                if not ac['runway'] or not p.runways or ac['runway'] in p.runways
            ]

            ac['assigned'] = None
            ac['current_waypoint'] = ''
            ac['suggestions'] = suggestions[:5]
            ac['all_procedures'] = all_procs

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
        if callsign in self.traffic_state:
            self.traffic_state[callsign]['assigned'] = None
            self.traffic_state[callsign]['current_waypoint'] = ''

            if not self.demo_mode and self.bridge:
                try:
                    await self.bridge.assign_waypoint(callsign, '')
                except Exception as e:
                    logger.error(f"Failed to clear waypoint for {callsign}: {e}")

        await ws.send_str(json.dumps({
            'type': 'unassign_result',
            'callsign': callsign,
            'success': True,
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
