"""
Async TCP client for the Aurora 3rd Party protocol (port 1130).

Protocol: ASCII, semicolon-delimited fields, CR/LF terminated.
Commands use 1-byte identifier + 2-5 byte command name.

IMPORTANT (from context doc):
  The docs show #CTRLRWY, #CONN, #CTO, and #ZTO all returning a success
  result prefixed with literal #CTRL rather than echoing their own command
  name. This matches a bug Omar already hit with #CONN in Tower Strip.
  → Do NOT hardcode response.startswith(command) assumptions.
  → Dispatch on field shape/count instead.
  → Field indices below are best-effort from context doc and NEED
    field-testing against live Aurora before relying on them.
"""

import asyncio
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class AuroraBridge:
    """Async TCP client for Aurora ATC on port 1130."""

    def __init__(self, host: str, port: int = 1130):
        self.host = host
        self.port = port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self.writer is not None

    async def connect(self) -> None:
        """Open TCP connection to Aurora."""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            self._connected = True
            logger.info(f"Connected to Aurora at {self.host}:{self.port}")
        except Exception as e:
            self._connected = False
            logger.error(f"Failed to connect to Aurora at {self.host}:{self.port}: {e}")
            raise

    async def disconnect(self) -> None:
        """Close the TCP connection."""
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None
        self._connected = False
        logger.info("Disconnected from Aurora")

    async def send_command(self, command: str) -> str:
        """Send a command and read one line of response.

        Thread-safe via asyncio.Lock (only one command in flight at a time).
        """
        async with self._lock:
            if not self.writer or not self.reader:
                raise ConnectionError("Not connected to Aurora")
            try:
                self.writer.write(f"{command}\r\n".encode('ascii'))
                await self.writer.drain()
                response = await asyncio.wait_for(
                    self.reader.readline(), timeout=5.0
                )
                return response.decode('ascii').strip()
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for response to: {command}")
                return ""
            except Exception as e:
                self._connected = False
                logger.error(f"Connection error during command '{command}': {e}")
                raise

    async def get_traffic_list(self) -> List[str]:
        """Get list of callsigns in range via #TR.

        Response parsing dispatches on field shape, not command echo prefix.
        """
        resp = await self.send_command("#TR")
        if not resp:
            return []

        fields = [f.strip() for f in resp.split(';') if f.strip()]
        callsigns = []
        for f in fields:
            # Skip command echoes and empty fields
            if f.startswith('#') or not f:
                continue
            # Callsigns: alphanumeric, typically 3-10 chars
            if 2 <= len(f) <= 10:
                callsigns.append(f)
        return callsigns

    async def get_flight_plan(self, callsign: str) -> Optional[Dict[str, Any]]:
        """Get flight plan via #FP;CALLSIGN.

        15 fields expected. Field 14 (0-indexed) = Route.
        Other field indices are UNVERIFIED — need live Aurora testing.
        """
        resp = await self.send_command(f"#FP;{callsign}")
        if not resp:
            return None

        fields = [f.strip() for f in resp.split(';')]
        if len(fields) < 15:
            logger.warning(
                f"FP response for {callsign}: expected 15 fields, got {len(fields)}"
            )
            return None

        # Corrected verified indices from raw #FP response:
        # fields[0]: command prefix, fields[1]: callsign, fields[2]: dep, fields[3]: arr
        # fields[6]: aircraft_type, fields[11]: cruise_alt, fields[15]: route
        return {
            "callsign": callsign,
            "aircraft_type": fields[6] if len(fields) > 6 else "",
            "departure": fields[2] if len(fields) > 2 else "",
            "arrival": fields[3] if len(fields) > 3 else "",
            "cruise_alt": fields[11] if len(fields) > 11 else "",
            "route": fields[15] if len(fields) > 15 else "",
        }

    async def get_runway_config(self) -> Dict[str, Dict[str, List[str]]]:
        """Get runway config for all controlled airports via #CTRLRWY.

        Response format: ICAO1;DEP_RWY1:DEP_RWY2;ARR_RWY1:ARR_RWY2;ICAO2;...
        Groups of 3 fields per airport.

        NOTE: Response may be prefixed with #CTRL instead of #CTRLRWY (known
        docs quirk). We dispatch on field structure, not command echo.
        """
        resp = await self.send_command("#CTRLRWY")
        if not resp:
            return {}

        fields = [f.strip() for f in resp.split(';') if f.strip()]
        config: Dict[str, Dict[str, List[str]]] = {}

        i = 0
        while i < len(fields):
            # Skip command echo prefix
            if fields[i].startswith('#'):
                i += 1
                continue

            # Need at least 3 fields: ICAO, dep_rwys, arr_rwys
            if i + 2 >= len(fields):
                break

            icao = fields[i].strip()
            # Validate this looks like an ICAO code
            if len(icao) == 4 and icao.isalpha() and icao.isupper():
                dep_rwys = [r.strip() for r in fields[i + 1].split(':') if r.strip()]
                arr_rwys = [r.strip() for r in fields[i + 2].split(':') if r.strip()]
                config[icao] = {"dep_rwys": dep_rwys, "arr_rwys": arr_rwys}
                i += 3
            else:
                i += 1

        return config

    async def get_traffic_position(self, callsign: str) -> Optional[Dict[str, Any]]:
        """Get position data via #TRPOS;CALLSIGN.

        21 fields expected. Field 9 (0-indexed) = Waypoint label.
        Other field indices are UNVERIFIED.
        """
        resp = await self.send_command(f"#TRPOS;{callsign}")
        if not resp:
            return None

        fields = [f.strip() for f in resp.split(';')]
        if len(fields) < 10:
            logger.warning(
                f"TRPOS response for {callsign}: expected 21 fields, got {len(fields)}"
            )
            return None

        # NOTE: Only field 9 = waypoint is confirmed from context doc.
        # Other indices are best-effort guesses.
        return {
            "callsign": callsign,
            "latitude": fields[1] if len(fields) > 1 else "",
            "longitude": fields[2] if len(fields) > 2 else "",
            "altitude": fields[5] if len(fields) > 5 else "",
            "groundspeed": fields[6] if len(fields) > 6 else "",
            "squawk": fields[8] if len(fields) > 8 else "",
            "waypoint": fields[9] if len(fields) > 9 else "",
        }

    async def assign_waypoint(self, callsign: str, value: str) -> str:
        """Set waypoint/scratchpad label via #LBWP;CALLSIGN;VALUE.

        Max 12 characters for the value (protocol limit).
        This is the mechanism for pushing the matched SID/STAR designator.
        """
        value = value[:12]  # Enforce max length
        resp = await self.send_command(f"#LBWP;{callsign};{value}")
        logger.info(f"Assigned waypoint '{value}' to {callsign}, response: {resp}")
        return resp
