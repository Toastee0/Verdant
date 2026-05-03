# VerdantSim — Session Handoff

**As of:** 2026-05-02 deep into the gen5 rewrite session.
**Branch:** `gen5` (origin/gen5 up to date through commit `355fd5e`).
**Author target:** the Claude Code session that picks up after context cycling.

This file captures the full live state of the project so a new session
can resume without re-deriving the architecture decisions.

---

## TL;DR — what shipped, what's next

**Shipped (origin/gen5):**

- Tier 0 schema-v1 reference simulator (`reference_sim/`, on `sim-core` branch as
  frozen reference; also retained in tree for cross-validation per user direction).
- M1–M4: element table, verify.py, regression runner, golden emissions for 5
  scenarios (`golden/`).
- Gen5 rewrite (`reference_sim_v2/`, schema-v2):
  - **M5'.0** scaffolding (16-slot composition, 4-channel phase fractions, petals)
  - **M5'.1** derive (identity/cohesion/T/pressure)
  - **M5'.2** gravity vector field (Newton borders + Jacobi diffusion)
  - **M5'.3** region kernel + flux SoA + integration
  - **M5'.4** concurrent per-phase scheduler
  - **M5'.5** phase-diagram lookup + transitions + ratchet
  - **M5'.6** Tail-at-Scale culling + Stefan-Boltzmann radiation
  - **M5'.7** verify_v2 + diff_ticks_v2 + regression_v2 + golden_v2
  - **M5'.7b** energy region kernel (conduction + convection)
  - **M5'.5b** rate-limited partial phase transitions + latent heat
  - **M5'.6b** petal stress integrator (gravity-on-mass × cohesion)
  - **M5'.6c** mid-cycle culling wake-up
  - **M6'.0** Tier 1 data: H + O element rows, H2O.csv phase diagram,
    `compounds.py` with `set_compound()` / water macro 200, smoke test
    `g6_water_static`.
  - **M6'.1** Tier 1 transition + multi-element flux: `t1_ice_melt`,
    `t1_water_pressure_drop`.
  - **M6'.2** Sorting-ruleset extension for cross-phase mass transmutation
    (`flux.dst_phase_per_slot` + `flux.energy_self`; region kernel
    consults neighbour-side phase-diagram lookup at flux-compute time;
    asymmetry: dst > src cross-phase routes; dst ≤ src defers to in-place
    transitions). First scenario `t1_evaporation` ships with it.
  - **M5'.5c** Energy-balanced phase transitions: `apply_phase_transitions`
    now solves Δm = (T_boundary - T_now) × m × cp_blend / L_J/kg per cycle
    so the cell settles at the phase boundary instead of free-running past
    it. Reality prevents oscillation through latent heat absorption; gen5
    now models that. For our (H,114)+(O,141) water compound the cp ratio
    across the solid/liquid boundary is ~4.7× (vs real water's ~2×), so a
    1/16-per-cycle Δm cap keeps the constant-cp linearisation safe; full
    energy-balance lifts when M6'.x compound calibration lands.
  - **M5'.7c** Log-encoded `energy_raw`. Linear u16 gave ~175 K per quantum
    for tiny gas-cell mass — fine for solids, useless for under-saturated
    gas. New encoding `raw = round(log10(1+E_J)×10923)` covers 0..1e6 J in
    u16 with sub-µJ resolution at the low end. Per-element `energy_scale`
    column in element_table.tsv becomes vestigial. `cells.energy_raw`
    semantics change but type stays uint16; verifier walks decoded J via
    `encoding.decode_energy_J`.
  - **Schema versioning dropped.** No external consumers; verifier and
    producer ship in the same repo. The `schema_version` field is gone
    from emissions, baseline-compat checks, diff_ticks, and self-tests.

**Validation:** `python -m checker.regression_v2` → 13/13 PASS.
`python -m checker.test_diff_ticks_v2` → 11/11 PASS.

**Next up (M6'.x Tier 1 scenarios):**

- **M6'.3** t1_humidity — gas cell with water-vapor composition (largely
  a state-construction test; doesn't need cross-phase flux per se).
- **M6'.4** t1_condensation — humid gas cooled to dewpoint; vapor flips
  to liquid via in-place phase transition; cohesion-driven liquid flux
  pulls new liquid into nearby liquid cells. Per Q3 verdict this is the
  asymmetric path — does NOT use the sorting-ruleset extension.

Per user direction: keep doing physics scenarios; ping when eyeballs needed
(viewer port). M6'.1 is "interesting enough" for first eyeball gate but user
authorised continuing physics work.

---

## Live calibration caveats (M6'.x cleanups)

These are NON-BLOCKING but documented in code as TODOs. They limit the
realism of Tier 1 dynamics.

1. **Compound water properties drift from real H₂O.** Composition-fraction-
   weighted blends of H + O give different density / c_p / latent heat than
   real water. Real H₂O: ρ_l=1000, c_p_l=4186, L_f=333.6 kJ/kg. Our
   (H,114)+(O,141) blend: ρ_l=663, c_p_l=5410, L_f≈800 kJ/kg. The L_f/c_p
   ratio is ~2× too high; this means latent heat absorbed during full melt
   exceeds cell's sensible heat capacity in many scenarios → cell can
   oscillate near the phase boundary.
   - **Workaround for now:** scenarios author energy levels carefully or
     skip latent heat entirely (`element_table=None` to
     `apply_phase_transitions`).
   - **Real fix at M6'.x:** per-compound calibration table, OR composition-
     weighted L blends with empirical correction factors, OR upgrade to
     molecular-level model.

2. **Per-tick conduction signal floors at the log-encoded quantum.** ΔU
   per direction (κ × ΔT × area × dt) can be sub-1 J at default cell_size
   for typical Tier 0/1 gradients. Under M5'.7c log encoding the resolution
   at E≈1 J is ~0.5 mJ/quantum (sub-mK on gas cells, sub-µK on solids),
   so this caveat is largely retired — what's left is f32 accumulation
   noise on the J side, which is benign for current scenarios.

3. **Phase diagram is 1D (T-only).** P-axis ignored everywhere. Water's
   triple-point and pressure-melt-curve aren't represented. Scenarios at
   1 atm work fine; M6'.x bumps to 2D when needed.

4. **Latent-heat partial transition is energy-balanced** as of M5'.5c.
   Δm per cycle solves T_after = T_boundary; cell self-stabilises at the
   phase boundary. A 1/16 cap is in place because the (H,114)+(O,141)
   water compound's cp_liquid/cp_solid ratio is ~4.7× (real water ~2×),
   which means the constant-cp linearisation overshoots at full conversion.
   Cap lifts when M6'.x compound calibration lands.

5. **Compound macros register phase diagrams under both H and O element
   ids** so identity tie-break (by saturation) lands consistently. This is
   a Tier 1 simplification; M6'.x does composition-weighted phase
   resolution.

6. **No solar flux absorption.** `world.solar_flux > 0` doesn't yet
   inject incoming radiation on RADIATES cells. Symmetric with the
   emission code that exists; deferred per gen5 §"Radiation".

7. **Petal stress doesn't integrate mid-cycle flux.** M5'.6b populates
   petal_stress from gravity-on-mass at sub_pass=0. But gen5 also says
   stress flux RECORDS update petal stress on both endpoints. Region
   kernel doesn't yet write `flux.stress`; M5'.6b' work.

---

## Branch + git state

- `sim-core` — frozen Tier 0 reference. M1–M4 + checker/verify.py +
  golden/. Up-to-date on origin.
- `gen5` — active dev. M5'.0 → M6'.0 + all follow-ups. Up-to-date on
  origin (last push `355fd5e`).
- `main` — UNRELATED HISTORY (legacy raylib game code, the dropped
  Rust/wgpu effort per the user's `project_impl_path` memory). Not
  merging into main from sim-core or gen5; user said "no need to
  destroy anything, disc space is not at risk."

---

## Code map (gen5 schema-v2)

```
reference_sim_v2/
├── __init__.py
├── grid.py             — copied verbatim from Tier 0; hex axial coords
├── cell.py             — gen5 SoA: 16-slot composition, 4-channel phase
│                          fractions, phase_mass, petals (stress/velocity/
│                          topology), sustained_overpressure f32
├── element_table.py    — Tier 0 loader reused via "from reference_sim
│                          import element_table"; hash uses LF-normalised
│                          content (bug fixed in M2)
├── compounds.py        — COMPOUNDS dict + set_compound() helper.
│                          Compound 200 = water = [(H,114),(O,141)]
├── scenario.py         — Scenario / WorldConfig / EmissionConfig / GravitySource
├── derive.py           — identity, cohesion, temperature, pressure decode,
│                          compute_thermal_blends (κ, c_p, ρ blends)
├── gravity.py          — Newton-seeded borders + Jacobi diffusion vector field
├── flux.py             — FluxBuffer SoA + apply_veto + integrate
├── region.py           — region kernel: mass flux (Fick on pressure dev) +
│                          energy flux (conduction + convection)
├── transitions.py      — apply_phase_transitions (rate-limited partial,
│                          latent heat) + apply_ratchet (sustained-
│                          overpressure → mohs++)
├── radiation.py        — Stefan-Boltzmann emission once per cycle
├── mechanics.py        — petal stress from gravity × cohesion + decay
├── culling.py          — Tail-at-Scale ε culling + mid-cycle wake-up
├── phase_diagram.py    — PhaseDiagram1D (T-only) loader + lookup
├── emit.py             — schema-v2 JSON writer
├── sim.py              — top-level driver; PHASE_BUDGETS + sub-pass scheduler
└── scenarios/          — g5_static, g5_temp_gradient, g5_grav_uniform,
                          g5_grav_two_body, g5_pressure_drop, g5_mixed_phase,
                          g5_melt, g5_ratchet, g5_radiative_boundary,
                          g6_water_static
```

Checker:
```
checker/
├── verify.py           — Tier 0 (schema-v1)
├── diff_ticks.py       — Tier 0
├── regression.py       — Tier 0
├── test_diff_ticks.py  — Tier 0 self-tests
├── verify_v2.py        — schema-v2: 11 invariants; mass_per_element_total
│                          per gen5 (sum across phases); mohs based on
│                          solid_mass not identity
├── diff_ticks_v2.py    — schema-v2 per-field tolerance comparator
├── test_diff_ticks_v2.py — 11 self-tests
└── regression_v2.py    — drives 10 scenarios (Tier 0 + Tier 1)
```

Data:
```
data/
├── element_table.tsv   — Si, H, O rows (NIST-sourced)
├── element_table_sources.md
├── compounds.tsv       — stub (compound macros now in compounds.py code)
└── phase_diagrams/
    ├── Si.csv           — Tier 0
    └── H2O.csv          — Tier 1 (1D T-only)
```

---

## Emission contract (the JSON each emission produces)

No `schema_version` field — gen5 dropped it. The producer (gen5 sim) and
all current consumers (verifier_v2, diff_ticks_v2, golden snapshots,
viewer-when-it-lands) live in this repo and evolve together; cross-repo
versioning would be ceremony with no purpose. If a future external
consumer ever binds to this format, version negotiation can be added
at that point.

```json
{
  "scenario": "<name>",
  "tick": N,
  "cycle": N,
  "sub_pass": 6,
  "stage": "post_integration" | "initial",
  "grid": {"shape": "hex_disc", "rings": 5, "cell_count": 91, "coordinate_system": "axial_qr"},
  "element_table_hash": "sha256:...",
  "phase_diagram_hash": "sha256:none",
  "border_table_hash": "sha256:none",
  "allowed_elements": ["Si"] | ["H", "O"] | ...,
  "cells": [
    {
      "id": int,
      "coord": [q, r],
      "composition": [["Si", 255]] | [["H", 114], ["O", 141]] | ... up to 16 pairs,
      "phase_fraction": [solid, liquid, gas, plasma],   // sum ≤ 1.0
      "phase_mass":     [solid, liquid, gas, plasma],   // gen5 hex-arithmetic
      "pressure_raw": uint16,         // deviation from equilibrium centre
      "energy_raw":   uint16,         // log10(1+E_J)×10923, decoded via encoding.decode_energy_J
      "mohs_level":   uint8,           // [1..10] when solid_mass > 0; 0 otherwise
      "sustained_overpressure": float, // ratchet integrator
      "identity": {"phase": "solid|liquid|gas|plasma|void", "element": "Si|H|O|..."},
      "flags": {"no_flow", "radiates", "insulated", "fixed_state", "culled",
                "fractured", "ratcheted_this_tick", "excluded"},
      "petals": [
        {"direction": 0..5, "stress": float, "velocity": [vx, vy],
         "topology": {"is_border", "is_grid_edge", "is_inert", "border_type"}}
      ],
      "temperature_K": float?,    // when derive ran
      "gravity_vec": [gx, gy]?,   // when scenario emits
      "cohesion": [c0..c5]?       // debug only
    }
  ],
  "totals": {
    "mass_by_element_by_phase": {"Si": {"solid": ..., "liquid": ..., "gas": ..., "plasma": ...}, ...},
    "energy_total": float,
    "momentum_total": [px, py],
    "cells_by_dominant_phase": {"solid": N, "liquid": N, "gas": N, "plasma": N, "void": N},
    "cells_culled": N, "cells_fractured": N, "cells_ratcheted_this_tick": N, "cells_excluded": N
  },
  "invariants": [...],
  "cycle_timing_ms": {"sub_pass_0_ms": ..., ..., "cycle_total_ms": ...}
}
```

---

## Verifier v2 invariant suite

10 checks (skipped silently when not applicable):

1. `composition_sum_255` — non-void cells sum to 255
2. `phase_fraction_sum_le_1` — vacuum is the complement
3. `phase_mass_non_negative`
4. `petal_count_6` — when petals emitted
5. `temperature_positive` — when T emitted
6. `cohesion_in_unit_interval` — when cohesion emitted; 0 at grid edges
7. `gravity_field_finite_bounded` — when gravity_vec emitted
8. `mohs_in_valid_range` — solid_mass > 0 ⇒ mohs ∈ [1, 10]; else mohs = 0
9. `fixed_state_cells_unchanged` — when FIXED_STATE cells exist + baseline given
10. `mass_per_element_total` — sum across phases per element vs baseline

---

## Architectural decisions locked in

D1. **Directory layout:** new `reference_sim_v2/` alongside frozen `reference_sim/`.
D2. **Schema:** `"schema_version": 2`; compatibility-checked by both verify
    and diff_ticks; refuses to compare v1 against v2.
D3. **Flux storage:** cell-centric SoA `flux.mass[N, 6, 16, 4]`. Authorship:
    cell A owns its 6 outgoing; B's incoming = A's outgoing in OPP[d].
D4. **Identity:** unified function (`compute_identity`) used everywhere;
    revisit at M5'.5+ if humid-air rendering shows problems. NOT yet split.
D5. **Identity tiebreak:** by-fraction-of-equilibrium (saturation = mass /
    EQUILIBRIUM_CENTER). Per the user: "identity-flip = displacement /
    nucleation event itself."
D6. **Energy resolution:** f32 working state inside cycle, encoded to u16
    only at integration boundary.
D7. **Tier 0 retirement:** retained per user direction ("disc space is not
    at risk").

Plus the user's clarifying messages:
- "Use the periodic table" (compounds expand at init only; runtime is elements).
- "Identity-crisis = displacement/nucleation of droplet" — confirmed D5.
- "Boundary cooling so we don't cook our sim bottle, internal thermal
  transfer should be modelled" — drove M5'.7b.
- "Whatever best fits the hardware" — M5'.5b rate-limited (vs energy-
  balanced); M5'.6b gravity×cohesion (vs sustained_overpressure decomposition).
- "Keep it simple for now, but keep a note of the potential option" — drove
  this very HANDOFF.md document.

---

## How to run

```
# Run any single scenario
python -m reference_sim_v2.sim reference_sim_v2.scenarios.<name> \
       --ticks N --output runs/<name>

# Verify a single emission
python checker/verify_v2.py runs/<name>/tick_NNNNN_post_integration.json \
       --baseline runs/<name>/tick_00000_initial.json

# Diff against golden
python checker/diff_ticks_v2.py golden_v2/<name>_tick_NN.json \
       runs/<name>/tick_NN_post_integration.json

# Full regression (all 10 scenarios)
python -m checker.regression_v2

# Self-tests
python -m checker.test_diff_ticks_v2

# Tier 0 (still alive)
python -m checker.regression       # 5 scenarios on schema-v1
python -m checker.test_diff_ticks  # 8 self-tests
```

---

## Open M6'.x work after Tier 1 scenarios

- **M6'.x calibration:** compound-aware phase resolution (currently both
  H and O point to H2O.csv; cleaner approach is a per-compound table).
- **M6'.x petal stress flux integration** (M5'.6b') — region kernel
  populates flux.stress; integrate sums onto petals on both endpoints.
- **M6'.x viewer port** — schema-v2-aware SVG/canvas viewer for
  fractional phases, identity, petals, gravity vec.
- **M7' Tier 2** — C, Fe → cast iron with lower melt point than pure Fe
  (mixing math falls out of composition-weighted phase boundaries).

---

## Working principles (carried forward, non-negotiable)

These applied throughout M1–M6'.1 and remain in force:

- **Physics completeness first.** Before adding a scenario, enumerate the
  physical inputs and check them against the cell struct. A scenario that
  runs but silently misses an input is worse than no scenario.
- **Doc and code stay synchronized.** When either changes, update the other
  in the same commit. Drift is its own bug.
- **Iterative, with consensus.** No big unilateral restructuring. Schema
  changes, cell-struct changes, staging-order changes are *design
  decisions, not coding decisions* — stop and check in.
- **Reference sim is the oracle.** Python correctness is the contract.
  CUDA perf is the eventual goal. The schema-v2 JSON bridges them.
- **"Properties move, cells don't."** Eulerian invariant. Every behavior
  is a flow on a static grid. If you ever want to move a cell, that's
  a bug.
- **No hand-tuned fudge constants.** Material values come from
  `data/element_table.tsv` (NIST-sourced) and `data/phase_diagrams/*.csv`.
  If something doesn't behave right and the impulse is to tweak a number,
  find the bug instead. (Calibration of compound-blend properties is the
  exception — documented as M6'.x.)
- **Small, scoped commits.** Each milestone is one or more commits. Tests
  must pass before the next milestone starts.
- **Conservation enforced in code, not hoped for.** `verify_v2.py` runs
  every tick. A failing run halts work; diagnosis happens before the
  next physics line is written.

## When to stop and check with the user

- A statement in `verdant_sim_design.md` (gen5) conflicts with a
  scenario's observed behaviour, OR with locked architectural decisions.
- A TODO in the skeleton has unclear intent and isn't covered by the
  spec docs (`gen5_implementation_spec.md`, `gen5_roadmap.md`).
- A scenario passes invariants but produces visibly wrong behaviour
  (humid air rendering, condensation that doesn't form droplets, etc.) —
  user wants to see these explicitly.
- The schema would need to change (schema is a contract; bump version
  and discuss before implementing).
- An open question from §"Open M6'.x work" comes up in practice. Don't
  unilaterally resolve it; flag it.
- Any destructive git operation (force-push, branch delete, reset --hard,
  retiring tracked files) needs explicit authorisation.

## How a fresh session resumes

1. Read this `HANDOFF.md` end-to-end.
2. Read [verdant_sim_design.md](verdant_sim_design.md) — it is the canonical
   physics design (commit `4e8a6e3` enshrined it). When this file and the
   design doc disagree, the design doc wins.
3. Run `python -m checker.regression_v2` to confirm the 13-scenario
   baseline is green.
4. Run `python -m checker.test_diff_ticks_v2` to confirm 11/11 self-tests.
5. Check `git log --oneline | head -20` for the last work cadence.
6. Pick up from §"Open M6'.x work" or wait for user direction.
