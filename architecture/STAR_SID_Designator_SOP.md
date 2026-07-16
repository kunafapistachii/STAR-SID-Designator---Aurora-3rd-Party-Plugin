# SOP — STAR/SID Designator Architecture & Operations

This document defines the system boundaries, design choices, data flow protocols, and error-recovery procedures for the STAR/SID Designator plugin.

---

## 🏛️ 1. System Architecture

The STAR/SID Designator operates on a three-tier architecture:

```
┌────────────────────┐      Port 1130      ┌───────────────────────┐
│ Aurora ATC Client  │◄───────────────────►│ Python Bridge Server  │
│ (TCP/ASCII Server) │                     │ (Asyncio Event Loop)  │
└────────────────────┘                     └───────────┬───────────┘
                                                       │ WebSockets
                                                       │ (Port 8080)
                                                       ▼
                                            ┌───────────────────────┐
                                            │ Web Frontend Dashboard│
                                            │ (Dark Aviation Theme) │
                                            └───────────────────────┘
```

1. **Aurora Client**: Serves as the source of flight plan (`#FP`), traffic position (`#TRPOS`), and runway configuration (`#CTRLRWY`) data. Accepts scratchpad label assignments via `#LBWP`.
2. **Python Bridge Server**: 
   - Manages asynchronous connection to Aurora and periodic polling cycles.
   - Hosts the dynamic sector file parser and route matching engine.
   - Serves static files and WebSocket communication for the client dashboard.
3. **Web Frontend Dashboard**: Dark glassmorphic controller interface showing live traffic split into Departures (SID) and Arrivals (STAR) with match suggestions.

---

## 🗺️ 2. Dynamic Sector File Directory Scanning

Instead of targeting a single FIR, the system recursively scans a root directory configured in `SECTOR_FILES_PATH` (normally point to Aurora's `Include` directory):

- The parser traverses directories, detecting folders named `Airports` (case-insensitive).
- Pairs and parses `.sid` and `.str` files inside those folders to build a global cache dictionary:
  ```json
  {
      "WIII": {"sids": [Procedure, ...], "stars": [Procedure, ...]}
  }
  ```
- This enables automatic airport loading dynamically as controllers switch airports or FIRs.

---

## ⚡ 3. Route Matching Algorithm

To resolve conflicts when multiple SIDs/STARs share identical tail segments (e.g. at WIII), the matching algorithm follows a two-tier verification:

1. **Primary - Core Fix Matching**:
   - The procedure name (e.g., "EGUKO 2L") is parsed to extract the eponymous "core" fix (`EGUKO`).
   - The algorithm verifies if this core fix name appears directly in the parsed flight route.
2. **Secondary - Overlap Count**:
   - Compares the intersection count between the route's fixes and the procedure's fixes. Used as a tiebreaker when multiple procedures share the core fix.

---

## 🌐 4. Connection State Machine & Handshake

- **Aurora Link**: The backend monitors the TCP client connection state. If connection fails, it retries every poll cycle and sets `aurora_connected` to `false` in the state JSON broadcast.
- **Frontend Indicator**: 
  - `AURORA: ONLINE` (green, pulsing) — live TCP connection active.
  - `AURORA: OFFLINE` (red, static) — live mode active but TCP connection down.
  - `AURORA: DEMO MODE` (amber) — simulation mode active.
- **Server WebSocket Link**: If the connection between the browser and the Python server breaks, the UI displays a blurred reconnecting modal to block stale interactions.

---

## 🛠️ 5. Error Recovery & Self-Healing Loop

- **Invalid Configuration**: If `SECTOR_FILES_PATH` is missing or invalid, the app enters `needs_setup` state, locking the dashboard and prompting the controller to set the path via the Setup GUI.
- **Protocol Quirks**: Responses from Aurora (like `#CTRLRWY`) do not echo the command name literally (docs showing `#CTRL` prefix mismatch). The bridge parser matches based on semicolon field count and data shapes rather than strict prefix matching.
