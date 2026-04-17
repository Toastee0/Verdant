# Debug Harness

The self-confirming debug/inspection layer. Already built in an earlier session; this page is the reference for how it works and how to extend it as the real sim comes online.

## The three artifacts

| Artifact | Path | Role |
|---|---|---|
| **Reference simulator** | `reference_sim/sim_stub.py` (stub, to be replaced) | Emits schema-v1 JSON at each tick/stage |
| **Viewer** | `viewer/viewer.html` | Human-facing: SVG hex grid, filterable sidebar, click-to-inspect |
| **Verifier** | `checker/verify.py` | Independent invariant checks; emits pass/fail reports |

All three agree on one JSON schema (v1, described in `../ARCHITECTURE.md`). Change the schema, all three update together.

## JSON schema summary

One file per emission. Per tick, or per stage if emission granularity is "stage" — configurable. Current fields (v1):

```
schema_version:       always 1
run_id:               scenario + timestamp
scenario:             scenario name
tick:                 integer
stage:                "initial" | "post_stage_1" | "post_stage_3b" | ...
cycle:                sub-iteration index
grid:                 {shape, rings / dimensions, cell_count, coordinate_system}
element_table_hash:   sha256 of the TSV
allowed_elements:     list of element symbols permitted by scenario
cells:                array of per-cell state objects
totals:               aggregates (mass per element, energy, phase counts, flag counts)
invariants:           sim's self-reported pass/fail on each invariant
stage_timing_ms:      per-stage timing
```

See `../ARCHITECTURE.md` for the full schema — that file predates the current physics framework but the schema shape is still accurate.

## Verifier — `verify.py`

Standalone Python script. Usage:

```
python checker/verify.py sample_data/tick_00003_post_stage_3b.json
python checker/verify.py sample_data/tick_99.json --filter element=Si
python checker/verify.py sample_data/tick_99.json --json-report
```

Exit codes:
- `0` — all invariants pass, no divergence
- `1` — at least one invariant failed (independent verdict)
- `2` — **DIVERGENT** — sim's self-report disagrees with the independent verdict (the most-serious outcome — means the sim's bookkeeping is broken)
- `3` — schema error (malformed JSON, wrong schema_version)

### Current invariant checks

- composition_sum_255 (each cell's composition fractions sum to 255)
- mass_conservation (currently TAUTOLOGICAL — needs fix, see below)
- pressure_decoding (round-trip through log-scale encoding)
- mohs_range (1–10 for solids, 0 for non-solids)
- bid_conservation (every bid_sent has a matching bid_received on the target)
- flags_consistency (RATCHETED implies solid, CULLED doesn't send bids, etc.)

### Known bug: mass conservation tautology

`infer_expected_mass()` sums current cells' compositions to derive "expected" mass, then compares to the current mass. Always equal. Cannot detect actual mass loss.

Fix planned in M2 (`../PLAN.md`): pass a baseline (tick 0 of the run) via `--baseline`. Expected mass = baseline's totals. Without baseline, check emits a "cannot verify — no baseline" warning.

## Viewer — `viewer.html`

Single-file HTML. No build step, no server, no deps beyond browser. Drag a JSON file onto it or use file picker.

### What it renders

- **Hex grid** as SVG, one path per cell.
- **Color encoding**: phase → hue (gas=blue, liquid=cyan, solid=earth tones), pressure within phase → lightness.
- **Flag overlays**: culled = dashed border; fractured = red outline; ratcheted = glow; excluded = crossed-out.
- **Sidebar filters**: by element, by phase, by Mohs level, by flag state.
- **Cell inspector**: click a cell, see its full JSON in a side panel.
- **Totals panel**: conservation numbers, invariant pass/fail summary.
- **Diff mode**: load a second JSON file, highlight changed cells.

### Extending for new fields

When adding cell fields (e.g., `elastic_strain`, `magnetization`), update:
- Color encoding logic (if the field is a visual primary).
- Cell-inspector panel (always, for display).
- Sidebar filters (if the field is useful for filtering).

Each of these is a ~10-line JS change in `viewer.html`.

## Emission granularity

Scenario config decides when JSON is emitted:

- `off` — no emission (production)
- `frame` — one per sim tick (default for bring-up)
- `stage` — one per pipeline stage within each tick (intensive, for detailed debug)
- `cycle` — one per sub-iteration of propagate stages (very intensive)
- `violation` — emit only when an invariant fails (production-mode debug)

At bring-up resolution (91 cells, Si only) even stage-level emission is manageable (~1 MB/sec).

## Cross-validation — Python vs. CUDA

When the CUDA port lands, the same scenario runs on both implementations. Both emit JSON at each tick. `diff_ticks.py` (planned, M4 in PLAN.md) loads two JSON files and compares cell-by-cell.

```
python checker/diff_ticks.py python_output/tick_00042.json cuda_output/tick_00042.json
```

Reports cells whose fields differ beyond tolerance. Any divergence is a porting bug, localized to specific cells.

Tolerances:
- Integer fields (phase, mohs, flags, raw pressure): exact match required.
- Float fields (decoded pressure, energy, temperature): within ~1e-4 relative tolerance.

The `element_table_hash` must match — can't diff runs with different ground truth.

## What emission carries

**Per cell** (in emitted JSON):
```
id, coord, phase, mohs_level, pressure_raw, pressure_decoded, energy, 
composition, flags, gradient, bids_sent, bids_received, 
elastic_strain, magnetization
```

**Per totals**:
```
mass_by_element, energy_total, cells_by_phase, 
cells_culled, cells_ratcheted_this_tick, cells_fractured, 
cells_excluded, cells_excluded_this_tick, cells_rejoined_this_tick
```

**Per invariants** (sim self-report):
```
name, status, expected, actual, tolerance, violations[]
```

**Per timing**:
```
stage_1_ms, stage_2_ms, stage_3_ms, stage_4_ms, stage_5_ms,
sub_iterations_used[phase][stage]
```

## What emission doesn't carry

- Scratch buffers (Φ, T, B, μ) by default. Can be requested via `debug_emit_derived_fields=true` for deep inspection.
- Per-sub-iteration deltas. Only final-tick state is emitted in default mode.

## File paths and organization

```
C:/projects/VerdantSim/
├── reference_sim/
│   ├── sim_stub.py              # current stub, to be replaced by real sim
│   └── sim.py                   # the real sim (M3, forthcoming)
├── checker/
│   ├── verify.py                # invariant checker
│   └── diff_ticks.py            # cross-validator (M4, forthcoming)
├── viewer/
│   └── viewer.html              # drag-drop hex grid viewer
├── sample_data/                 # reference emissions for testing
│   ├── tick_00000_initial.json
│   ├── tick_00001_post_stage_3b.json
│   ├── tick_00002_post_stage_3b.json
│   ├── tick_00003_post_stage_3b.json
│   └── tick_00099_post_stage_3b_violation.json
├── data/
│   ├── element_table.tsv        # (M1, forthcoming)
│   └── scenarios/
├── wiki/                        # this knowledge base
└── ARCHITECTURE.md              # debug harness architecture (current)
```

## Debug workflow example

Scenario: sim is producing unexpected stalactite shapes; something is off at tick 1200.

```
# 1. Look at what the sim reports
python checker/verify.py run_42/tick_01200_post_stage_3b.json

# 2. If sim reports PASS but stalactite looks wrong, compare to baseline
python checker/diff_ticks.py run_42/tick_00000_initial.json run_42/tick_01200_post_stage_3b.json

# 3. Open the viewer, load the suspicious tick
# (in browser) drag tick_01200_post_stage_3b.json onto viewer.html

# 4. Filter to only cells with element=Si, phase=solid, check Mohs levels
# Use sidebar filters; click suspicious cells for full state

# 5. If still unclear, emit at stage granularity, re-run, diff each stage
```

This is the inspection loop: run → verify → view → diff. Closed and usable without any remote tooling.

## What the harness doesn't do

- No time-series analysis across many ticks.
- No dashboards / alerts / CI integration.
- No automatic regression detection across scenario runs.

Each of these is a future addition. The current harness covers: "I have a scenario run, I want to know if it's broken, and where."
