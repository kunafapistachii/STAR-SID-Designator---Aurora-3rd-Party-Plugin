# 🟢 gemini.md - System Law & Data Schema

This file defines the JSON Data Schema, system rules, and the maintenance log.

---

## 🗂️ Data Schema

### WebSocket Traffic Update State Payload
```json
{
  "type": "traffic_update",
  "status": "ready" | "needs_setup",
  "aurora_connected": true | false,
  "demo_mode": true | false,
  "detected_path": "string_or_null",
  "traffic": {
    "CALLSIGN": {
      "callsign": "string",
      "type": "departure" | "arrival",
      "departure": "string",
      "arrival": "string",
      "route": "string",
      "altitude": "string",
      "squawk": "string",
      "runway": "string",
      "airport": "string",
      "assigned": "string_or_null",
      "current_waypoint": "string",
      "suggestions": [
        {
          "name": "string",
          "core_match": true | false,
          "overlap": 0,
          "core_fix": "string_or_null"
        }
      ],
      "all_procedures": ["string", ...]
    }
  },
  "runway_config": {
    "ICAO": {
      "dep_rwys": ["string", ...],
      "arr_rwys": ["string", ...]
    }
  }
}
```

---

## 🛠️ Behavioral Rules
- **No Guessing**: The pilot never guesses business logic or external integrations.
- **Error Repair**: Errors triggers the self-annealing repair loop: analyze -> patch -> test -> update architecture SOP.

---

## 📅 Maintenance Log
| Date | Action / Modification | Phase | Result |
|------|----------------------|-------|--------|
| 2026-07-16 | Project Initialization | Phase 1 | Initialized files. |
| 2026-07-16 | Core System Implementation | Phase 3 | Scaffolding, parsing, matching, TCP bridge, server and frontend. |
| 2026-07-16 | Dynamic scan, setup GUI & true Aurora status | Phase 4 | Config overlays, recursive file directories scanner, TCP handshake trackers. |

