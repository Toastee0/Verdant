# VerdantSim gen5 — Python Reference Sim Roadmap

**Branch:** `gen5` (off `sim-core`)
**Date:** 2026-05-02
**Companion docs:** `gen5_implementation_spec.md`, `tier0_reusability_audit.md`
**Authoritative design:** `verdant_sim_design.md`
**Frozen reference:** `sim-core` branch + `reference_sim/` (schema-v1, Tier 0)

This roadmap replaces M5–M9 of the previous `PLAN.md`. M1–M4 stay as recorded history; their deliverables remain as the cross-validation oracle ("Tier 0 frozen reference") on `sim-core` until M5'.7 retires them.

---

## 0. Architectural premise (must be locked in before M5'.0)

Gen5 is a different physics shape than Tier 0. The framework wiki (`framework.md`, `pipeline.md`, `auction.md`, `mass-flow.md`) describes a **single-phase-per-cell, μ-gradient mass auction** with explicit pressure encoding. Gen5 replaces this with:

| Tier 0 (sim-core)                       | gen5 (target)                                            |
| --------------------------------------- | -------------------------------------------------------- |
| 1 phase per cell (`phase: u2`)          | 4-channel phase distribution (solid+liquid+gas+plasma fractions, vacuum implicit) |
| 4 composition slots                     | 16 composition slots                                     |
| Pressure as absolute encoded value      | Pressure as deviation from phase-density equilibrium center (42 / 1764 / 74088) |
| μ-gradient bidder/recipient auction     | Blind-sum flux records on edges, computed by overlapping 7-cell regions |
| Sequential stages 0a–6                  | Concurrent per-phase sub-pass scheduler (gas:3, liquid:5, solid:7) |
| Fixed gravity field (Φ scalar Poisson)  | Gravity vector field (Jacobi diffusion, border-seeded)   |
| Cohesion = bool per bond, recomputed    | Cohesion = f32 per-cell-per-direction blind damping coefficient |
| `elastic_strain: i8`, `mohs_level: u4`  | f32 sustained-overpressure integrator + petal stress     |
| Computed `phase` from (P,T,composition) | Computed identity from (phase fractions, composition) per cycle |

The Python reference sim is being greenfielded because the data shape changes everywhere. This is not a refactor.

**One non-negotiable:** the sim runs on a static convex hex disc. Cells never move. Properties move via flux records on edges.

---

## 1. Pre-M5' decisions (resolve before any code lands)

Each is an architectural fork that shapes Cell SoA, schema, and verifier together. Each needs an explicit "yes/no" before M5'.0 starts.

### D1. Directory layout
**Recommendation: new `reference_sim_v2/` alongside the existing `reference_sim/`.**

Reasoning: schema-v1 and schema-v2 must coexist for cross-validation during M5' bring-up. `diff_ticks.py v1` keeps validating the frozen sim-core scenarios. `verify_v2.py` runs against gen5 emissions. Once M5'.7 lands and we trust gen5, we git-rm the v1 tree (it stays in `sim-core` history). Sharing a directory by mutating `reference_sim/cell.py` to v2 destroys the ability to A/B both.

### D2. Schema-v2 file naming
**Recommendation:** emit to `runs/<scenario>/<run_id>/tick_NNNNN_<stage>.json`. Add `"schema_version": 2` at top level. The viewer detects and routes to v1/v2 renderer; checker has separate entry points (`verify_v1.py` and `verify_v2.py`).

`diff_ticks.py` becomes schema-aware: refuses to compare v1 against v2.

### D3. Edge-centric vs cell-centric flux record storage
**Recommendation: cell-centric SoA (per-direction outgoing) for the Python reference.**

- `flux.mass[N, 6, 16, 4]` — N cells × 6 directions × 16 composition slots × 4 phases
- `flux.momentum[N, 6, 2]` — 2D vector per edge per cell
- `flux.energy[N, 6]` — scalar per edge
- `flux.stress[N, 6]` — directional stress

Authorship convention: cell A owns its outgoing record; cell B's incoming is `-A.outgoing[opposite]`. Blind summation over overlapping regions still works because each region authors the same edge from the same side and they add up. The CUDA port is free to choose edge-centric for perf; the schema doesn't lock this in.

### D4. Per-purpose vs unified identity
**Recommendation: unified for M5'-M5'.4, then revisit at M5'.5 (humid air scenarios).**

If a Tier 1 humid-air scenario produces visibly wrong rendering (the "barely-present liquid wins by mass" pathology in shelved-question #1), split into `identity_for_render` vs `identity_for_cohesion` then.

### D5. Majority-by-mass vs majority-by-fraction-of-equilibrium
**Recommendation: by-fraction-of-equilibrium.** A phase that holds 100 mass units when its center is 1764 is at 5.7% saturation; a phase at 42/42 is at 100%. The latter is "physically present"; the former is dispersed solute. By-fraction-of-equilibrium gets condensation/aquifers right by construction.

`presence[phase] = mass[phase] / equilibrium_center[phase, composition]`. Argmax wins.

### D6. Per-element energy_scale at low T
**Tier 0 had u16-floor issues** at low T because energy_scale was uniform. Gen5 fix: keep f32 working state internally, encode to u16 only at canonical-state boundaries. Per-element energy_scale stays in the element table for the encode/decode round-trip but the kernel never sees encoded values mid-cycle. The "low-T floor" problem disappears.

### D7. Retire vs retain Tier 0 reference + golden emissions
**Recommendation: retain through M5'.4, retire at M5'.7.**

Keep `reference_sim/` and `golden/t0_*.json` until M5'.7 ships. Run both regressions in CI. After M5'.7, archive to `reference_sim_tier0_archive/` with a README pointing at the `sim-core` git ref.

---

## 2. Milestones

Each milestone is one focused work session (~half-day to full-day). Each lands on `gen5` as one or more commits, with the verifier passing every commit.

### M5'.0 — gen5 scaffolding + scenarios/ stub

**Goal:** new directory tree, empty modules, one no-op scenario, tick loop runs and emits valid schema-v2 JSON for a 91-cell static disc.

**Deliverable code:**
- `reference_sim_v2/__init__.py`
- `reference_sim_v2/grid.py` — copy from v1 verbatim (hex topology unchanged)
- `reference_sim_v2/cell.py` — **new SoA layout** (see §3.1)
- `reference_sim_v2/element_table.py` — extend v1 loader to add `density_scale_per_phase`, `phase_diagram_csv` columns
- `reference_sim_v2/sim.py` — top-level driver with cycle structure
- `reference_sim_v2/emit.py` — schema-v2 writer (see §3.2)
- `reference_sim_v2/scenarios/g5_static.py` — uniform Si solid disc, all 4 phase fractions = (74088, 0, 0, 0) on solid Si, zero motion
- `checker/verify_v2.py` — schema-v2 validator skeleton, conservation invariants only

**Validation:** `g5_static` runs 5 ticks. Every emission is valid schema-v2. Mass per element conserved exactly. Phase-fraction sums conserved. No flux records non-zero.

**Invariants checked by verify_v2:**
- `schema_version == 2`
- `composition_sum_255` per cell
- `phase_fraction_sum <= 1.0` per cell (with vacuum complement)
- `mass_per_element_per_phase` conserved across ticks
- `petal_count == 6` per cell
- `cell_count` matches grid

**Open question to resolve before starting:** D1, D2 confirmed.

---

### M5'.1 — derive stage: identity + cohesion + temperature

**Goal:** Stage 0 (derive) computes per-cell f32 working state. No flow yet. Per-cycle scratch buffers allocated once and reused.

**Deliverable code:**
- `reference_sim_v2/derive.py`:
  - `compute_identity(cells) → (majority_phase[N], majority_element[N])` — by-fraction-of-equilibrium (D5)
  - `compute_cohesion(cells, identity) → cohesion[N, 6]` f32 — blind, per-cell-per-direction
  - `compute_temperature(cells, element_table) → T[N]` f32 — composition-and-phase-weighted c_p
  - `decode_pressure_to_f32(cells) → P[N]` — log-scale u16 → f32 absolute
- `reference_sim_v2/scenarios/g5_temp_gradient.py` — disc with linear T gradient, no motion. Verifies T derive is correct.

**Validation:** identity matches expected on `g5_static`; cohesion is uniform ~1.0 inside a same-material disc, drops to 0 across phase boundaries when scenario has any; T derive matches manual calculation.

**Invariants:**
- `cohesion ∈ [0, 1]`
- `cohesion(self, dir) == 0` when neighbor doesn't exist (grid edge)
- `T > 0` for every cell with mass > 0
- `identity` is deterministic (same state → same identity)

**Dependencies:** M5'.0.

**Open question:** D4 (one identity vs many) — keep unified.

---

### M5'.2 — gravity vector field (Jacobi diffusion)

**Goal:** Stage 0a replacement. Per-cell f32 (gx, gy) vector. Border-seeded by point sources. Active cells contribute their mass. Diffuses by Jacobi.

**Deliverable code:**
- `reference_sim_v2/gravity.py`:
  - `seed_borders(grid, sources, world) → border_g[boundary_cells, 2]`
  - `jacobi_diffuse_gravity(g_in, g_out, grid, n_iters=N) → g_field[N, 2]`
  - Pure Jacobi, dual-buffer, fixed iter count for now (10–20)
- Hook into `derive.py` to compute `g_field` once per cycle (gravity changes slowly per gen5 §Runtime phase)
- `reference_sim_v2/scenarios/g5_grav_uniform.py` — single point source far below disc, expected g vector ≈ uniform downward inside the active region
- `reference_sim_v2/scenarios/g5_grav_two_body.py` — two point sources at opposite borders, Lagrange-like neutral line in middle

**Validation:**
- `g5_grav_uniform`: max |g - g_expected| / |g_expected| < 1e-3 across the disc
- `g5_grav_two_body`: vector sum at midline cell is ≈ zero
- Border vectors are frozen across ticks (verified by re-running and comparing)

**Invariants:**
- gravity field is finite (no NaN, no inf) for any scenario passing setup bounds
- `|g| < g_max_bound` (per gen5 §Scenario bounds)
- gravity field converged before flux compute (delta between Jacobi iterations < tolerance)

**Dependencies:** M5'.1. The convex-region requirement (gen5 §Setup phase) lands here as a setup-time check.

---

### M5'.3 — region kernel + blind flux summation (mass + momentum, no phase transitions)

**Goal:** the heart of gen5. Implement the 7-cell flower compute, per-edge flux records, blind summation across overlapping regions, and integration. Single phase only (start with all-solid Si for sanity), no phase transitions.

**Deliverable code:**
- `reference_sim_v2/region.py`:
  - `region_kernel(center_id, cells, derived, world) → flux_contributions[6]` — pure function, reads canonical state, computes mass + momentum flux per edge based on pressure deviation from equilibrium center, gravity vector, cohesion damping
- `reference_sim_v2/flux.py`:
  - `FluxBuffer` SoA: mass[N,6,16,4], momentum[N,6,2], energy[N,6], stress[N,6] (D3)
  - `accumulate_region_contributions(flux, contributions, center_id)` — blind sum
  - `integrate(cells, flux, dt)` — apply incoming − outgoing per cell, re-encode to canonical
- Vetoes for hard constraints (NO_FLOW edges, grid borders) applied between region compute and integration (gen5 §Veto stage)
- Update `sim.py` to drive: derive → veto-list-build → for each cell: region_kernel → blind sum → integrate

**Test scenarios:**
- `g5_static_v2` — 91-cell uniform solid Si at exact equilibrium center. Expect zero flux, zero deltas.
- `g5_pressure_drop` — center cell at +ΔP, neighbors at center. Expect mass to flow out of center toward neighbors, monotonic equalization across ticks.
- `g5_gravity_settle` — gravity downward, single column. Expect mass to redistribute toward bottom over many cycles (slow, since solid is non-opportunistic).

**Validation:**
- `g5_static_v2`: bit-exact zero deltas every tick
- `g5_pressure_drop`: monotonic decrease in max(|P - P_center|), mass per element conserved exactly
- `g5_gravity_settle`: center-of-mass moves downward over ticks

**Invariants:**
- **mass conservation per element per phase** (Σ flux_in − Σ flux_out = 0 across grid)
- **flux summation symmetry**: for every edge (A, dir d), `flux[A,d] + flux[B,opp(d)] == 0` after veto, before integration (this is the architectural commitment that makes blind sum conservative)
- **momentum conservation**: Σ momentum across grid is preserved modulo gravity contribution
- **vetoed fluxes never sum**: NO_FLOW and out-of-grid edges have zero contribution

**Dependencies:** M5'.0–.2. Critical: the verifier's flux-summation-symmetry check is what catches authoring-convention bugs. Land it before any phase complexity.

---

### M5'.4 — concurrent per-phase sub-pass scheduler

**Goal:** gen5 commits to **gas: 3 sub-passes, liquid: 5, solid: 7 — all running concurrently within one cycle**. The Python ref doesn't get GPU concurrency, but it must implement the **correctness shape**: each phase advances on its own schedule, phase freezes after its budget, cross-phase boundaries update live.

**Deliverable code:**
- `reference_sim_v2/scheduler.py`:
  - `cycle()` runs 7 sub-passes (the longest budget)
  - Sub-pass N runs region kernels for cells whose dominant phase is still active (gas active for N≤3, liquid for N≤5, solid for N≤7)
  - Mixed-phase cells: each phase fraction runs on its own schedule (gen5 §Mixed-phase cells)
  - Each sub-pass: derive → region compute → veto → blind sum → integrate → swap buffers
- `reference_sim_v2/scenarios/g5_mixed_phase.py` — wet sand (solid Si + liquid H2O composition fractions in same cell). Solid fraction stays put for 7 sub-passes, liquid fraction settles in 5.

**Validation:**
- `g5_static_v2` (now run through full sub-pass schedule): zero deltas after every sub-pass
- `g5_mixed_phase`: liquid fraction equilibrates faster than solid; sub-pass 4–7 only solid still updating; gas/liquid frozen after their budgets

**Invariants:**
- **per-sub-pass conservation** (mass, momentum, energy each separately conserved at every sub-pass boundary, not just per cycle)
- **phase-freeze monotonic**: once a phase is frozen, its phase-fraction mass for that phase doesn't change in subsequent sub-passes
- **cross-phase live update**: when a gas-phase fraction is frozen but a neighbor liquid is still active, flux at the boundary still flows

**Dependencies:** M5'.3.

**Open question:** how does Tail-at-Scale culling interact with the Python ref? Recommendation: implement noise-floor ε check in the scheduler but make it a no-op pass-through (always-active) for M5'.4. Add real culling in M5'.6.

---

### M5'.5 — phase-diagram lookup + phase transitions + sustained-overpressure ratchet

**Goal:** Stage 1 replacement. Each cycle, the kernel checks each cell's (P, T, composition) against the per-element phase diagram and writes phase-transition fluxes (mass moves between phase channels within a cell, energy adjusts for latent heat). Mohs ratchet driven by f32 sustained-overpressure integrator (no u8 counter).

**Deliverable code:**
- `data/phase_diagrams/Si.csv` — (T, P, phase_id, initial_mohs) lookup table, sourced from Si phase diagram
- `reference_sim_v2/phase_diagram.py`:
  - `PhaseDiagram` class loaded at init; lookup is a 2D bilinear interpolation on T,P → phase_id
  - For composition: composition-weighted phase boundaries (gen5 §Phase resolve); for Tier 0/1 single-element cells this is trivial
- `reference_sim_v2/transitions.py`:
  - `apply_phase_transitions(cells, derived, dt)` — per-cell, may move mass between phase channels in-place; queues energy delta for latent heat
  - `apply_ratchet(cells, derived, dt)` — accumulates `sustained_overpressure[N]` f32, fires `mohs_level++` when threshold crossed, dumps compression work to energy
- `reference_sim_v2/scenarios/g5_melt.py` — solid Si disc heated to 1700+ K boundary. Melt front propagates inward.
- `reference_sim_v2/scenarios/g5_ratchet.py` — sustained over-equilibrium pressure on center cell; expects mohs_level++ after threshold time.

**Phase-diagram data format:**
```
# Si.csv
# T_K, P_Pa, phase, initial_mohs
300,    1e5,   solid,  6
1687,   1e5,   solid,  6
1687.1, 1e5,   liquid, 0
3538,   1e5,   liquid, 0
3538.1, 1e5,   gas,    0
...
```
Bilinear interpolation between rows. Latent heat for transitions read from element_table (`L_fusion`, `L_vaporization`).

**Validation:**
- `g5_melt`: monotonic shrinkage of solid fraction over ticks; total mass conserved; total energy conserved (energy field absorbs latent heat)
- `g5_ratchet`: `sustained_overpressure` integrates upward, fires ratchet at expected tick, energy spikes by compression work

**Invariants:**
- **phase-transition mass conservation**: mass moved from solid→liquid channel equals mass added to liquid channel (no leak between phases within a cell)
- **latent heat energy balance**: ΔU = L_phase × Δm to within float relative tolerance 1e-6
- **mohs monotonic**: ratchet only increases; `RATCHETED` flag set the cycle it fires
- **sustained_overpressure decay**: when cell drops below equilibrium, integrator decays toward zero (no unbounded accumulation of stale stress)

**Dependencies:** M5'.4 (need concurrent phase passes for cross-phase live updates).

---

### M5'.6 — Tail-at-Scale culling + per-channel borders + petal stress integration

**Goal:** the remaining gen5 features for a complete, validated single-cycle architecture. Noise-floor culling, configurable per-channel boundary conditions, and petal stress that integrates from flux records.

**Deliverable code:**
- `reference_sim_v2/culling.py`:
  - Per-cell, per-cycle: if all six flux contributions below ε, mark CULLED. Skip in subsequent sub-passes within this cycle. Wake on incoming flux > ε.
- `reference_sim_v2/borders.py`:
  - `BorderTable` lookup loaded from `data/borders.csv` — per-border-type (insulated, radiative, fixed-T, etc.) with per-channel parameters (mass-permeable, thermally-conductive, fixed-flux, etc.)
  - Topology cache: petal `is_border`, `border_type_index` populated on first contact; never re-validated
- `reference_sim_v2/petals.py`:
  - SoA: `petal.stress[N, 6]`, `petal.velocity[N, 6, 2]`, `petal.topology[N, 6]` (u8 packed)
  - `update_from_flux(petals, flux)` — stress flux increments both sides' stress; momentum flux updates velocity
- Scenarios:
  - `g5_radiative_boundary.py` — hot disc with radiative ring (gen5 port of t0_radiate)
  - `g5_sealed_chamber.py` — fully sealed; no mass/energy/momentum crosses border

**Validation:**
- `g5_radiative_boundary`: ring cells emit Stefan-Boltzmann; total energy decreases monotonically; reproduces Tier 0 t0_radiate qualitative behavior
- `g5_sealed_chamber`: total mass + total energy strictly constant across 100 ticks (within 1e-9 relative)
- `g5_static_v2` after culling: confirms most cells are CULLED most of the time on equilibrium scenarios

**Invariants:**
- **CULLED cells have all-zero flux** in their cycle
- **petal stress symmetric on intact bonds**: `petal.stress[A, d] + petal.stress[B, opp(d)] ≈ 0` modulo decay
- **border veto consistent**: no flux ever crosses a `NO_FLOW`-channel border (per channel, not per cell)

**Dependencies:** M5'.5.

---

### M5'.7 — verifier v2 + golden emissions + Tier 0 retirement

**Goal:** complete the gen5 verifier with all gen5-specific invariants. Record golden emissions for every g5_* scenario. Retire `reference_sim/` (Tier 0) by archiving and removing from regression CI.

**Deliverable code:**
- `checker/verify_v2.py` — full invariant suite (see §3.7)
- `checker/diff_ticks_v2.py` — schema-v2-aware comparator with per-field tolerances (see §3.7)
- `checker/regression_v2.py` — runs every g5_* scenario, verifies + diffs against golden
- `golden_v2/g5_*.json` — recorded reference emissions for each scenario at picked-tick
- Move `reference_sim/` → `reference_sim_tier0_archive/` (or git-rm and rely on `sim-core` branch as the archive — D7 confirms which)
- Update `PLAN.md`: M5'.0–.7 complete, M5/Tier 1 prep starts

**Validation:** all g5_* scenarios green against verify_v2 + diff_ticks_v2.

**Dependencies:** all prior. This is the gate before Tier 1 (H₂O) physics work begins.

---

### M6'.x — Tier 1 (H + O) — gen5 reduction of old M5

After M5'.7 ships, real Tier 1 starts. These are smaller increments, each landing one gen5-emergent behavior:

- **M6'.0** — element table H + O rows + H₂O phase diagram CSV
- **M6'.1** — `g5_ice_melt` scenario (single-phase → mixed-phase transition through composition transport between phase channels)
- **M6'.2** — `g5_humidity` scenario (gas cell with water-vapor composition, validates mixed-composition gas dynamics)
- **M6'.3** — `g5_condensation` scenario (humid gas drops below condensation T, water flips to liquid channel within cell, cohesion gradient pulls liquid mass to nucleus). Validates D4 (revisit identity-for-rendering if visually wrong)
- **M6'.4** — `g5_evaporation` scenario (liquid + gas adjacent; water composition fluxes from liquid to gas)

Each is an independently testable gen5 emergent behavior. None requires new framework; they all exercise M5' machinery with new element data.

---

## 3. Critical files — concrete shapes

### 3.1 `reference_sim_v2/cell.py`

```python
COMPOSITION_SLOTS = 16    # gen5 commitment

@dataclass
class CellArrays:
    grid: HexGrid

    # === stored canonical state (the "cell struct" gen5 §State representation) ===
    composition: np.ndarray            # int16, shape (N, 16, 2) — (element_id, fraction)
    phase_fraction: np.ndarray         # float32, shape (N, 4)   — [solid, liquid, gas, plasma]
    phase_mass: np.ndarray             # float32, shape (N, 4)
    pressure_raw: np.ndarray           # uint16, shape (N,)      — deviation from equilibrium center
    energy_raw: np.ndarray             # uint16, shape (N,)
    mohs_level: np.ndarray             # uint8, shape (N,)
    sustained_overpressure: np.ndarray # float32, shape (N,)     — gen5 commitment, not u8 counter

    # Petal data (persistent per-cell-per-direction state)
    petal_stress: np.ndarray           # float32, shape (N, 6)
    petal_velocity: np.ndarray         # float32, shape (N, 6, 2)
    petal_topology: np.ndarray         # uint8, shape (N, 6)     — bit-packed flags
    flags: np.ndarray                  # uint8, shape (N,)
```

Memory at 91 cells: ~30 KB. At 250k cells: ~80 MB.

### 3.2 Schema v2 JSON shape

```json
{
  "schema_version": 2,
  "scenario": "g5_static",
  "tick": 42, "cycle": 42, "sub_pass": 7,
  "stage": "post_integration",
  "grid": {"shape": "hex_disc", "rings": 5, "cell_count": 91, "coordinate_system": "axial_qr"},
  "element_table_hash": "sha256:...",
  "phase_diagram_hash": "sha256:...",
  "border_table_hash": "sha256:...",
  "cells": [
    {
      "id": 0, "coord": [0, 0],
      "composition": [["Si", 255]],
      "phase_fraction": [1.0, 0.0, 0.0, 0.0],
      "phase_mass": [74088.0, 0.0, 0.0, 0.0],
      "pressure_raw": 32768, "pressure_decoded": 0.0,
      "energy_raw": 30000, "energy_decoded": 12345.6,
      "temperature_K": 300.0,
      "mohs_level": 6, "sustained_overpressure": 0.0,
      "identity": {"phase": "solid", "element": "Si"},
      "petals": [{"direction": 0, "stress": 0.0, "velocity": [0.0, 0.0],
                  "topology": {"is_border": false, "border_type": null, "is_grid_edge": false}}],
      "gravity_vec": [0.0, -9.8],
      "cohesion": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
      "flags": {"culled": false, "fractured": false, "ratcheted_this_tick": false,
                "excluded": false, "fixed_state": false, "insulated": false,
                "radiates": false, "no_flow": false}
    }
  ],
  "totals": {
    "mass_by_element_by_phase": {"Si": {"solid": 6741968.0, "liquid": 0.0, "gas": 0.0, "plasma": 0.0}},
    "energy_total": 1123456.0, "momentum_total": [0.0, 0.0],
    "cells_by_dominant_phase": {"solid": 91, "liquid": 0, "gas": 0, "plasma": 0},
    "cells_culled": 0, "cells_ratcheted_this_tick": 0
  }
}
```

### 3.7 Verifier v2 invariant list

```
INVARIANTS_V2 = [
    "mass_per_element_per_phase_conserved",
    "momentum_total_conserved_modulo_gravity",
    "energy_conserved_with_radiation_accounting",
    "composition_sum_255",
    "phase_fraction_sum_le_1",
    "phase_mass_non_negative",
    "petal_count_6",
    "flux_summation_symmetric_per_edge",   # crucial; catches blind-sum bugs
    "vetoed_fluxes_zero_after_integration",
    "mohs_monotonic_non_decreasing_per_tick",
    "sustained_overpressure_decay_when_below_threshold",
    "cohesion_in_unit_interval",
    "gravity_field_finite_bounded",
    "petal_stress_symmetric_on_intact_bonds",
    "culled_cells_emit_zero_flux",
    "border_no_flow_channel_zero_mass",
    "border_insulated_channel_zero_energy",
    "fixed_state_cells_unchanged",
    "schema_version_2",
    "element_table_hash_match_baseline",
    "phase_diagram_hash_match_baseline",
]
```

`diff_ticks_v2.py` per-field tolerances:
- `pressure_raw`, `energy_raw`, `mohs_level`, `flags`, `petal_topology`: **exact**
- `phase_fraction`, `phase_mass`, `pressure_decoded`, `energy_decoded`, `temperature_K`, `sustained_overpressure`: **rel_tol=1e-6**
- `composition`: **exact (element, fraction) pairs**
- `petal_stress`, `petal_velocity`, `gravity_vec`, `cohesion`: **rel_tol=1e-5** (more permissive, derived/working state)

---

## 4. Risk register & flagged decisions

| # | Risk / decision                                  | Status     | Resolve when                  |
| - | ------------------------------------------------ | ---------- | ----------------------------- |
| D1 | Directory layout (v2 dir vs replace)            | RECOMMEND new dir | Before M5'.0           |
| D2 | Schema-v2 file naming + co-existence            | RECOMMEND `tick_NNNNN_<stage>.json` + `schema_version: 2` | Before M5'.0 |
| D3 | Edge-centric vs cell-centric flux storage       | RECOMMEND cell-centric SoA | Before M5'.3   |
| D4 | Unified vs per-purpose identity                 | RECOMMEND unified, revisit at M5'.5 | Before M5'.1, revisit M5'.5 |
| D5 | Majority-by-mass vs by-fraction-of-equilibrium  | RECOMMEND by-fraction-of-equilibrium | Before M5'.1 |
| D6 | Per-element energy_scale at low T               | RECOMMEND f32 working state, encode at boundary | Before M5'.0 |
| D7 | Retain vs retire Tier 0 reference + golden      | RECOMMEND retain through M5'.4, retire at M5'.7 | M5'.7 |
| R1 | Gravity Jacobi convergence on non-convex regions | KNOWN; convex assertion at setup | M5'.2 setup time |
| R2 | Phase-diagram data sourcing for Si              | OPEN — Si phase diagram CSV needs NIST sources cited | M5'.5 |
| R3 | Latent-heat data per element                    | OPEN — extend element_table.tsv with L_fusion, L_vaporization | M5'.5 |
| R4 | Border table format and parameter list          | OPEN — needs border-types enumeration | M5'.6 |
| R5 | Tail-at-Scale ε tuning per scenario             | OPEN — start with single global ε, tune empirically | M5'.6 |
| R6 | Gen5 GPU patterns (cp.async, warp shuffles, SoA tile kernels, L2 persistence, memory tiers) | OUT OF SCOPE — M9-CUDA only, Python ref does NOT need them | Permanent |

---

## 5. Working principles

These remain non-negotiable for gen5:

- **Physics completeness first.** Before adding a scenario, enumerate the physical inputs and check them against the gen5 cell struct. A scenario that runs but is silently missing an input is worse than no scenario.
- **Reference sim is the oracle.** Python correctness is the contract. CUDA perf is the eventual goal. Schema-v2 JSON bridges them. `verify_v2.py` checks both.
- **"Properties move, cells don't."** The Eulerian invariant holds in gen5 the same as Tier 0.
- **Conservation enforced in code, not hoped for.** `verify_v2.py` runs every tick.
- **No hand-tuned fudge constants.** All constants come from element_table, phase_diagrams, border_table.
- **Small, scoped commits.** Each milestone landing is one or more commits. Tests pass before the next milestone starts.
- **Doc and code stay synchronized.** `verdant_sim_design.md` is the gen5 authority.
- **Iterative, with consensus.** Decisions D1–D7 above are flagged precisely because they're architectural and need explicit human sign-off before implementation.

---

## 6. Branch + git discipline

- `gen5` is the working branch. All M5'.* work commits there.
- `sim-core` is **frozen** — no further commits. It is the schema-v1 oracle until M5'.7.
- `verdant_sim_design.md` and `Claude Code Handoff Brief.md` should be committed to `gen5` so they're versioned with the code that implements them.

---

## 7. Definition of "M5'.7 done"

When all of the following hold:

1. All `g5_*` scenarios run end-to-end on `gen5` branch at HEAD.
2. Each emits valid schema-v2 JSON every tick.
3. Each passes `python checker/verify_v2.py --baseline <tick_0> <tick_N>` for every tick.
4. Each has a recorded `golden_v2/<scenario>_tick_<N>.json`.
5. `checker/diff_ticks_v2.py` exists, has its own self-tests, and is wired into `regression_v2.py`.
6. `regression_v2.py` runs every g5_* scenario and reports pass/fail.
7. `PLAN.md` updated: M1–M4 historical record retained, M5'.0–.7 marked DONE with delivery notes.
8. Tier 0 reference `reference_sim/` either archived (renamed) or removed.
9. Mass conservation per element per phase: exact. Momentum conservation: exact modulo gravity. Energy conservation: relative tolerance 1e-6 across all scenarios.
10. Flux summation symmetry verified on every cycle of every g5_* scenario.

At that point: stop, summarize, and wait for direction on M6'.0 (Tier 1 gen5 — H₂O, phase transitions in compositional space).
