# VERDANT Debug Harness — Architecture

**Purpose:** A self-confirming debug/inspection layer that lets an AI agent (Claude) read a slice of VERDANT simulation state, verify physical invariants automatically, and share filterable results with the human operator. Designed as the *first* artifact built — before the Python reference sim, before the CUDA port — so that both implementations are checked against the same JSON contract.

**End target:** CUDA/C++ native kernel on RTX 3090 under Windows 11. The debug harness is the bridge: Python reference → JSON → viewer (human debug) + JSON → invariant checker (AI debug). When the CUDA port emits the same JSON, the same checker validates both and the viewer renders both identically.

---

## The three roles

1. **Human operator (Adrian):** needs a browser-based viewer that shows a single slice of sim state (one tick, one stage within tick, or a diff between two ticks) with aggressive filtering — by element, by phase, by coordinate range, by invariant violation.

2. **AI agent (Claude in a future session):** ingests the same JSON file, runs a built-in invariant checker, and reports:
   - Conservation pass/fail per element
   - Dead-band compliance per phase
   - Mohs ratcheting validity
   - Composition sum = 255 per cell
   - Any stage-specific assertions

3. **The sim itself (Python now, CUDA later):** emits JSON at configurable granularity — every tick, every stage within tick, or on invariant violation.

All three roles agree on **one schema**. Change the schema, all three update. The JSON is the contract.

---

## JSON schema (v1)

```json
{
  "schema_version": 1,
  "run_id": "t0_silicon_drop_2026-04-16T14:30:00",
  "scenario": "t0_silicon_drop",
  "tick": 42,
  "stage": "post_stage_3b",
  "cycle": 2,

  "grid": {
    "shape": "hex_disc",
    "rings": 6,
    "cell_count": 91,
    "coordinate_system": "axial_qr"
  },

  "element_table_hash": "sha256:abc123...",
  "allowed_elements": ["Si"],

  "cells": [
    {
      "id": 0,
      "coord": [0, 0],
      "phase": "solid",
      "mohs_level": 5,
      "pressure_raw": 1234,
      "pressure_decoded": 253800.0,
      "energy": 300,
      "composition": [["Si", 255]],
      "flags": {
        "resolved": true,
        "culled": false,
        "fractured": false,
        "ratcheted_this_tick": false
      },
      "gradient": [0, 0, 0, 0, 0, 0],
      "bids_sent": [],
      "bids_received": []
    }
    // ... 90 more
  ],

  "totals": {
    "mass_by_element": {"Si": 23205.0},
    "energy_total": 27300.0,
    "cells_by_phase": {"solid": 91, "liquid": 0, "gas": 0, "plasma": 0},
    "cells_culled": 0,
    "cells_ratcheted_this_tick": 3
  },

  "invariants": [
    {
      "name": "Si_mass_conservation",
      "expected": 23205.0,
      "actual": 23205.0,
      "tolerance": 0.0,
      "status": "pass"
    },
    {
      "name": "composition_sum_255",
      "status": "pass",
      "violations": []
    },
    {
      "name": "dead_band_compliance",
      "status": "pass",
      "violations": []
    }
  ],

  "stage_timing_ms": {
    "stage_1": 0.02,
    "stage_2": 0.08,
    "stage_3a": 0.11,
    "stage_3b": 0.09
  }
}
```

---

## Schema design principles

**1. Both raw and decoded values for pressure.** `pressure_raw` is the u16 as stored in the cell struct; `pressure_decoded` is the log-scale-decoded absolute value. The viewer shows decoded, the checker can verify both. When CUDA ports this, the raw value round-trips identically.

**2. Bids recorded at cell level for traceability.** A cell lists bids it sent and received this stage. With 6 neighbors max per cell and bids being rare (only cells outside dead-band bid), this is low-volume. Filterable in viewer, machine-checkable for conservation (Σ bids_sent from neighbors == Σ bids_received per cell).

**3. Invariants are first-class output.** The sim itself reports what it checked and what passed. The AI agent's job is to independently verify those invariants against the raw cell data and flag any divergence between "sim says it passed" and "data actually passes."

**4. One file per emission point.** Tick 42 post-Stage-3b is a single JSON file. You can diff tick 42 against tick 41 by loading both. This is more data than streaming but massively simpler to reason about and trivially compressible.

**5. Element table hash baked in.** The sim records the hash of the element table it was run with. If you compare results across runs, mismatched hashes flag immediately — you're comparing runs with different ground-truth physics.

---

## Emission granularity

Controlled by a debug flag at sim start:

- `off` — no JSON emitted
- `frame` — one JSON per rendered frame (every Stage 1)
- `stage` — one JSON per pipeline stage (Stage 1, 2, 3a, 3b each)
- `cycle` — one JSON per cycle (Stage 1, then 2+3 per cycle, emission between each)
- `violation` — emit only when an invariant fails (production-mode debug)

Frame mode: ~60 emissions/sec at 60fps. At ~50 KB per emission (91 cells × ~500 bytes), that's 3 MB/sec. Trivial for a debug build.

Stage or cycle mode at bring-up resolution (91 cells, one test scenario) produces maybe 10-100 MB per scenario run. Fine.

At production grid (129,600 cells), frame mode only. Stage-level emission would be ~70 GB for a minute of sim. That's the price of full observability and it's fine for targeted debugging sessions.

---

## Viewer design

**Single-file HTML** (no build step, no server, no deps beyond browser). Loads JSON via file picker or drag-drop. Renders:

- **Hex grid** rendered in SVG (axial → pixel math, one path per cell)
- **Sidebar with filters:** element, phase, Mohs level, flag states, pressure range
- **Cell inspector:** click a cell, see full JSON for that cell
- **Totals panel:** conservation numbers, invariant pass/fail summary
- **Diff mode:** load a second JSON file, highlight cells that changed

The visualization is **deliberately spartan**. This is a debug tool, not a game. Read-only. High information density. Monospace font everywhere. Data is the point.

Color encoding:
- **Phase:** hue (gas=blue, liquid=cyan, solid=earth tones, plasma=magenta)
- **Pressure within phase:** lightness (low=dim, at-center=mid, high=bright)
- **Flagged cells:** border treatment (culled=dashed, fractured=red outline, ratcheted=glow)

A legend on screen at all times. You should be able to screenshot the viewer and show it to someone cold and they can read the state.

---

## AI-agent checker (Python, reusable by Claude sessions)

A standalone `verify.py` script that takes a JSON file and emits a report:

```
$ python verify.py tick_00042_stage_3b.json

VERDANT Debug Report — tick_00042_stage_3b.json
Scenario: t0_silicon_drop  (element hash: abc123...)
Tick: 42  Stage: post_stage_3b  Cycle: 2

CONSERVATION
  Si mass:          23205.0 / expected 23205.0      PASS
  Total energy:     27300.0 / expected ±1.0 drift   PASS

CELL INTEGRITY
  Composition sums: 91/91 cells sum to 255          PASS
  Dead-band:        91/91 cells within band         PASS
  Mohs monotonic:   3 ratchets, 0 inversions        PASS

FLAGS
  Cells culled:     0
  Cells fractured:  0
  Cells ratcheted:  3  (ids: 14, 22, 31)

WARNINGS
  Cell 22 pressure within 2% of ratchet threshold — expect transition next tick
  Cell 45 has 6 eligible bid targets but sent 0 bids — possible bug?

VERDICT: PASS (3 warnings)
```

The checker is the thing Claude (or any AI agent) runs when the human says *"something looks off at tick 42, can you check it?"* The answer comes from running the checker against the JSON, not from the AI making things up.

**This is the self-confirming part.** The sim claims it's working. The checker independently verifies the claim against the raw data. If they disagree, that's the bug report.

---

## Port path to CUDA

When the CUDA kernel ships:

1. **Same JSON schema.** The CUDA version has a host-side emitter that reads the GPU buffers and writes identical JSON.
2. **Same viewer.** Unchanged. It's a contract consumer.
3. **Same checker.** Unchanged. Same invariants apply regardless of implementation.
4. **Cross-validation mode:** run Python and CUDA on the same scenario, emit JSON at each tick, diff the two JSON streams. Any divergence is a porting bug, and the checker + diff tool will find it.

That last point is the key payoff. **You can't port a parallel sim correctly without a reference implementation to check against.** The debug harness *is* that reference infrastructure, built before either implementation, shared by both.

---

## File layout

```
verdant_debug/
  ARCHITECTURE.md           ← this file
  schema_v1.json            ← JSON schema as a JSON-Schema document
  viewer/
    viewer.html             ← single-file drag-drop viewer
  checker/
    verify.py               ← AI-usable invariant checker
    invariants.py           ← invariant definitions, importable
  reference_sim/
    sim_stub.py             ← minimal Python sim that emits the schema
                              (not the full gen 4 sim — a stub for testing the harness)
  sample_data/
    tick_00000.json         ← a known-good emission for viewer/checker tests
    tick_00001.json
    tick_00002_violation.json  ← deliberately broken, checker should flag
```

The stub sim is important: it's ~200 lines of Python that produces valid schema-v1 JSON for a trivial scenario (drop a Mohs 1 silicon cell on a Mohs 5 floor, run 3 ticks). It exists to test the viewer and checker without waiting for the full gen 4 sim. When the real sim is written, stub is deleted.
