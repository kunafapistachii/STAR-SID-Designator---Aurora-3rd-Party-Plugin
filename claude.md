# 📜 Project Constitution: STAR_SID Designator

This file serves as the Project Constitution for the **STAR_SID Designator** project. It outlines the data schemas, behavioral rules, and architectural invariants.

---

## 🏛️ Architectural Invariants
1. **Deterministic Business Logic**: All business logic must be deterministic and written as testable Python scripts in `tools/`.
2. **SOP Driven**: Layer 1 SOPs in `architecture/` define the "How-To". Code matches the SOPs.
3. **Data-First**: No tools are coded until schemas are defined and confirmed in `gemini.md`.
4. **Local Isolation**: All local intermediate operations must use `.tmp/`.

---

## 📋 Project State Tracking
- **Current Phase**: Phase 1 - Blueprint (Discovery)
- **Status**: Initializing project memory.
