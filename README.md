# ✈️ Aurora STAR/SID Designator

STAR/SID Designator is a 3rd-party plugin for the **IVAO Aurora ATC Client**. This plugin automatically matches an aircraft's filed flight route to the correct Standard Instrument Departure (SID) or Standard Terminal Arrival Route (STAR) procedure name based on the active runway in use, and then writes that matched designator directly into the aircraft's flight strip waypoint/scratchpad label field in Aurora using the `#LBWP` TCP command.

---

## 🏛️ System Architecture

The application operates on a three-tier architecture:

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

1. **Aurora ATC Client**: Serves as the source of flight plan (`#FP`), traffic position (`#TRPOS`), and runway configuration (`#CTRLRWY`) data. Accepts scratchpad label assignments via `#LBWP`.
2. **Python Bridge Server**: 
   - Manages the asynchronous TCP connection to Aurora and handles periodic traffic polling cycles.
   - Hosts the dynamic sector file parser and route matching engine.
   - Serves static files and manages WebSocket communication for the client dashboard.
3. **Web Frontend Dashboard**: A premium dark glassmorphic controller interface showing live traffic split into Departures (SID) and Arrivals (STAR) with intelligent match suggestions.

---

## ✨ Key Features

- **Dynamic Sector Directory Scanning**: Recursively scans the path configured in `SECTOR_FILES_PATH` to discover and pair `.sid` and `.str` files for all airports under the FIR automatically.
- **Smart Route Matching Algorithm**: Solves route matching conflicts (where multiple procedures share a common route segment or entry fix) by prioritizing name-based core fix matching first (e.g. matching fix `EGUKO` for procedure "EGUKO 2L"), and using procedure fix overlap counts as a tiebreaker.
- **Connection State Machine**: Real-time status indicators on the dashboard UI (`AURORA: ONLINE` [pulsing green], `OFFLINE` [static red], or `DEMO MODE` [amber]). The dashboard will display a blurred reconnecting modal to block stale interactions if the websocket connection to the server is lost.
- **Demo Mode**: An offline simulation mode that generates realistic mock traffic for Jakarta Soekarno-Hatta (WIII) for UI development and testing without needing a live connection to Aurora.

---

## ⚙️ Prerequisites

- **Python 3.10** or newer.
- **IVAO Aurora ATC Client** installed on your PC.
- Active FIR Sector Files installed in Aurora (containing the `Airports` folder with `.sid` & `.str` files).

---

## 🔧 Configuration (`.env`)

Copy `.env.example` to `.env` and fill in your values:

```env
# Aurora TCP Connection
AURORA_HOST=localhost
AURORA_PORT=1130

# Path to your Aurora SectorFiles/Include/<FIR>/Airports/ folder
# Example: C:/IVAO/Aurora/SectorFiles/Include
SECTOR_FILES_PATH=F:/Aurora/SectorFiles/Include

# Polling interval in seconds (how often to refresh traffic from Aurora)
POLL_INTERVAL=3

# Web dashboard server port
WEB_PORT=8080

# Demo Mode (set to true for offline simulation, false for live TCP mode)
DEMO_MODE=true
```

> [!NOTE]  
> If `SECTOR_FILES_PATH` is missing or invalid, the app enters a `needs_setup` state, locking the dashboard and prompting you to set the path via the built-in Setup GUI.

---

## 🚀 How to Run

1. **Install Dependencies**:
   Open a terminal in the project root directory and run:
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the Server**:
   Double-click the **`Start-Server.bat`** file on Windows. This launcher automatically detects a local virtual environment (`.venv`) if present, or falls back to your global system Python.

3. **Open the Web Dashboard**:
   Open your browser and navigate to:
   [http://localhost:8080](http://localhost:8080)

4. **Stop the Server**:
   Double-click **`Stop-Server.bat`** or close the server console/terminal window.

---

## 🎮 Using the Dashboard

1. **Connecting to Aurora**:
   - Ensure *3rd Party Connection* is enabled in your IVAO Aurora client settings (using TCP port 1130).
   - Verify the connection status indicator at the top right of the dashboard reads `AURORA: ONLINE` or `AURORA: DEMO MODE`.
2. **Runway Configurations**:
   - The active runways for departures (DEP) and arrivals (ARR) are automatically retrieved from Aurora. You can manually adjust them through the runway configuration button on the dashboard panel if necessary.
3. **Matching & Selection**:
   - Traffic is automatically separated into **Departures (SID)** and **Arrivals (STAR)** panels.
   - The system analyzes flight plan routes and highlights the best matching procedure based on the active runway config.
4. **Assigning to Aurora**:
   - Select an aircraft from the list.
   - Click the **Assign** button to push the procedure designator (up to 12 characters) to Aurora. The label will appear in the waypoint/scratchpad field of the aircraft's flight strip.
   - Click **Clear** to remove the designator label from the strip.

---

## ❌ Troubleshooting

- **`AURORA: OFFLINE` status**:
  Verify that the Aurora client is running, you are connected or simulating, and the *3rd Party Connection* setting is enabled.
- **`traffic not assumed` error during Assign**:
  Before you can assign a scratchpad label or waypoint to an aircraft flight strip via a 3rd party plugin, you must **Assume (F3)** the aircraft in the Aurora client first. Otherwise, Aurora will reject the assignment.
- **No procedures showing**:
  Double-check your `SECTOR_FILES_PATH` in `.env` or in the Setup GUI. It must point to a directory that contains the FIR/Airports subdirectory structure (e.g. `Include/WIIF/Airports/WIII.sid`).
