# Pressure — handoff plan

**Audience:** my future self, picking up after context cycling.
**Status:** The next foundational milestone. Everything before this was
incremental polish on a sim that is missing a load-bearing primitive.

---

## Why this file exists

Without pressure, the sim is conduction + radiation + phase transitions
only. That covers a fraction of the physics it was designed for and breaks
the scenarios it claims to demonstrate:

- **Buoyancy is absent.** `t1_ice_melt` puts ice in water under no gravity
  and the ice doesn't move. Ice (ρ = 917 kg/m³) in water (ρ = 1000) should
  rise — that's the canonical pressure-driven phenomenon.
- **Vacuum boundaries don't evaporate.** `g5_radiative_boundary` floats
  liquid Si at 2227 °C in "space" and watches it slowly radiate. In real
  physics that surface would be aggressively boiling off into vacuum
  (vapor pressure × area), losing mass orders of magnitude faster than it
  loses energy via radiation alone.
- **`g5_pressure_drop` tests nothing physical.** `decode_pressure_to_f32`
  is the M5'.1 stub — it casts `pressure_raw: uint16` to f32 as-is. The
  region kernel uses `dP > 0` as a flow gate, so the scenario is
  "high raw number flows toward low raw number" with no physical meaning.
- **Atmospheric stratification, hydrostatic columns, vapor flow into
  saturated air, dewpoint condensation rate** — all impossible.

The user's diagnosis: *"it's a waste of time to do any simulation without
pressure. that block of ice? it should have moved due to density."* They
are right. Most of the M6'.x and viewer work was confirming that the
non-pressure parts of the sim function — they do, but they only describe
a sliver of the design's intended behaviour.

---

## What pressure should be (sketch — needs user verdict)

A per-cell scalar P assembled from three independent contributions:

### 1. Phase-equilibrium pressure (P_eq)
*The gen5 design doc's primary concept.* Deviation from equilibrium mass density:

    P_eq ∝ mass_actual − ρ_blend × V_cell

Over-saturated cells (more mass than the phase wants at this density)
push outward; under-saturated cells pull inward. Drives the volumetric
contraction we already added for ice → water (ice has 8 % less liquid
volume per kg, so a fully-melted ice cell creates a vacuum bubble that
the surrounding water flows into — *under pressure*, not "for free").

### 2. Hydrostatic pressure (P_hydro)
Gravity acting on accumulated cell-column mass:

    P_hydro = ρ × g · depth_vector

In our 2D hex grid, `depth_vector` falls out of `derived.gravity_vec`
(which already exists, computed by `gravity.py`) projected onto the
cell-to-neighbour direction. Each cell's hydrostatic contribution at its
faces is `gravity_vec · face_normal × cell_mass / cell_face_area`.

This is where **buoyancy emerges**: ice cell in water column has lower
ρ → less weight pressing down on the cell below it → that cell's
upward face has lower pressure than its downward face → net force
pushes water up, ice flows down/water flows up → ice rises.

### 3. Vapor / thermal pressure (P_thermal)
Equation of state per phase:

- **Gas / plasma:** P = nRT/V (ideal gas). For gas water at 100 °C in
  1 L: n = (1 kg) / (0.018 kg/mol) = 55.6 mol, P = 55.6 × 8.314 × 373.15
  / 0.001 ≈ 173 MPa. Big number — that's why steam under pressure is
  scary. For under-saturated gas (humid air): P_thermal scales with mass.
- **Liquid:** Tait equation OR constant (~atmospheric). Liquids are
  near-incompressible; P_thermal contribution is dominated by P_hydro.
- **Solid:** track stress separately via `petal_stress` (already exists).
  Bulk pressure is dominated by P_hydro.
- **Vapor pressure (Clausius-Clapeyron)** at phase boundaries:

      P_vap(T) = P_ref × exp((-L_vap / R) × (1/T − 1/T_boil))

  Each element / compound has a vapor pressure curve. Used at *surface*
  boundaries (cells next to vacuum, atmosphere, or dramatically lower-
  pressure neighbours). This is what makes hot Si in vacuum evaporate.

### Boundary conditions
RADIATES + grid-edge cells need a phantom-neighbour pressure:
- **Vacuum boundary:** P_phantom = 0
- **Atmosphere boundary:** P_phantom = 101,325 Pa (1 atm)
- **FIXED_STATE wall:** P_phantom = wall's internal pressure (held constant)

These let surface evaporation, condensation onto cold walls, etc. fall
out of the same Δ P-driven flux mechanism that handles internal cell flow.

### Total
**P_total = P_eq + P_hydro + P_thermal**

### Flow law
Replace the current `dP > 0` flow gate with proper Navier-Stokes-light:

    flux_amplitude ∝ −∇P / ρ × dt × phase_mass × cohesion × K_phase

For each (cell A, direction d, phase p), the flux toward neighbour B is
proportional to (P_A − P_B), divided by ρ_A_phase, times the mass
available in that phase channel, times per-phase mobility, times the
existing cohesion damping. **Phase-mass-scaled** (not phase-fraction-
scaled) — that fix we already made for the cohesion attractor.

---

## Open design questions (resolve via user / chat-session review)

1. **Derived vs canonical state.** Is pressure a *derived* field (recomputed
   each cycle from phase_mass + T + composition + gravity_vec, like
   `phase_fraction` now is) or *integrated* state (evolves via continuity
   + momentum equations)? My lean: derived. Simpler, single source of
   truth, no integration drift. The cost is no inertia/wave-propagation
   effects.

2. **Hydrostatic in 2D hex.** How does "column mass above this cell"
   work when gravity is a vector field on a 2D plane? Possibilities:
   - **Local gradient only:** for each face, hydrostatic contribution =
     gravity_vec · face_normal × cell_mass. Adjacent cells exchange via
     ΔP. Buoyancy emerges from chain of pairwise differences.
   - **Integrated column:** scan up from each cell along gravity_vec
     direction, sum mass × g of cells overhead. More expensive but more
     accurate over multiple cell heights.
   - Recommendation: start with local-gradient (simpler), upgrade if
     scenarios need it.

3. **Reconciliation with the cross-phase sorting ruleset.** The Q3
   verdict said evaporation uses sorting-ruleset routing. With proper
   vapor pressure driving surface evaporation, the sorting ruleset
   becomes redundant for water — pressure-driven flow handles bulk
   evaporation directly. Two options:
   - **Pressure wins; sorting ruleset deprecated.** Cleaner long-term.
     `t1_evaporation` rewires to rely on pressure.
   - **Both fire, asymmetric.** Sorting ruleset handles small-area-fast-
     transit edges (a few cells deep); pressure handles bulk volumes.
     More complex but matches some design-doc language about cohesion-
     driven droplet migration.
   - Recommendation: ask user. Pressure-only is cleaner.

4. **Equation of state per phase.** Detailed EoS or simplified?
   - Gas: ideal gas is the right starting point.
   - Liquid: constant atmospheric ≈ 101 kPa for most demos. Tait
     equation if we want compressibility under high pressure later.
   - Solid: use `petal_stress` for stress, P_hydro for bulk pressure.

5. **Per-cell P scalar vs per-direction P vector.** Anisotropic stress
   in solids (e.g., overburden vs sideways earth pressure) needs per-
   direction. Liquids and gases are isotropic. For Tier 1 / 2 demos,
   per-cell scalar is fine. Solids can use `petal_stress` for
   directionality.

6. **Numerical stability.** Pressure differences can be enormous (vacuum
   to atmosphere = 101 kPa) compared to other terms. CFL condition
   becomes restrictive at small dt. May need adaptive dt or implicit
   integration for stiff cases. Start explicit, fall back if needed.

7. **Schema implication.** `pressure_raw: uint16` was canonical state.
   If pressure becomes derived: keep `pressure_raw` in emission for
   inspection but it's now overwritten each derive. No schema bump
   required (emission format unchanged).

---

## Implementation plan (5 phases)

Each phase is a coherent, testable increment. Commit after each. Goldens
regen each time.

### Phase 1 — P_eq + plumbing (M7'.1)
**Smallest viable change. Just wire phase-equilibrium pressure.**

1. New helper `compute_pressure_eq(cells, element_table, world)` in
   `derive.py`. For each cell:
   - density_blend = composition × per-phase-density-blend × phase_fraction
   - mass_actual = Σ phase_mass × Q_KG = cell mass in kg
   - mass_eq = density_blend × cell_volume
   - P_eq = K_eq × (mass_actual − mass_eq)
2. Replace `decode_pressure_to_f32` to call `compute_pressure_eq` and
   ignore `cells.pressure_raw`.
3. `derived.pressure` populated from `compute_pressure_eq`.
4. Region kernel already reads `derived.pressure` for flux — no change.
5. Pick K_eq calibration so flow magnitudes match current scenarios
   (e.g., for `g5_pressure_drop` to produce similar liquid flow).
6. Scenarios: `pressure_raw` becomes vestigial (still in schema, scenario
   init can leave at 0). Update docstrings.
7. **Validate:** existing 16/16 regression still passes (with regen
   goldens — flow magnitudes shift). Inspect `g5_pressure_drop` viewer —
   should look qualitatively similar.

**Open question for Phase 1:** does keeping `pressure_raw` as
canonical-state buy us anything? Recommendation: deprecate but don't
remove (let scenarios that want to seed pressure use it as an additive
override — `derived.pressure = pressure_raw + P_eq` or similar).

### Phase 2 — P_hydro + buoyancy (M7'.2)
**Add gravity coupling. Ice floats.**

1. Add P_hydro term to `compute_pressure_eq` → rename to
   `compute_pressure(cells, derived, ...)`. Reads `derived.gravity_vec`.
2. Per-cell hydrostatic contribution: each face gets a P offset based on
   `gravity_vec · face_normal × cell_mass / face_area`.
3. Effective P per face for flux: `P_eq + P_hydro_per_face`.
4. Region kernel flux uses per-face pressure: a bit of refactor; flux per
   (A, d, p) now reads (P_A_at_face_d − P_B_at_face_opp_d) instead of
   (P_A − P_B).
5. New scenario `t1_buoyancy`: ice cube in water under gravity, ice rises
   to the top, water sinks to the bottom. 30+ ticks of viewable migration.
6. Optionally: add gravity to `t1_ice_melt` so ice floats while melting.
7. **Validate:** `t1_buoyancy` shows ice migration; existing scenarios
   without gravity unchanged.

### Phase 3 — Vapor pressure + vacuum boundaries (M7'.3)
**Surface evaporation into vacuum/atmosphere works.**

1. Add Clausius-Clapeyron data per element / compound (element_table has
   `L_vaporization` and `boil_K` — sufficient inputs).
2. `compute_pressure` adds P_thermal contribution:
   - For phase_mass in gas channel: P_thermal_gas = nRT/V (or saturation-
     capped at vapor-pressure curve if phase diagram says we're at the
     boundary).
   - For liquid/solid cells near their respective surfaces: contribute
     P_vapor(T) to the gas side of the boundary.
3. Phantom-neighbour pressure for grid-edge + RADIATES cells:
   `derived.pressure` lookup returns 0 (vacuum) when neighbour is invalid
   and a RADIATES flag is set on this cell.
4. Region kernel: flux into phantom neighbour fires when P_self > 0.
5. Update `g5_radiative_boundary` description: "liquid Si in vacuum, loses
   mass via evaporation and energy via radiation."
6. **Validate:** `g5_radiative_boundary` Si loses mass over ticks;
   `t1_evaporation` continues to work (now via pressure rather than
   sorting ruleset — confirm or deprecate sorting ruleset for water).

### Phase 4 — Per-phase EoS refinement (M7'.4)
**Tighten the gas/liquid/solid pressure models.**

1. Gas: ideal gas explicit. Molar mass from element_table (already
   present).
2. Liquid: choose Tait or constant. Document.
3. Solid: P_hydro dominates. Stress (anisotropic) handled separately via
   petal_stress (existing M5'.6b/c work).
4. **Validate:** atmospheric scenario (gas column under gravity)
   stratifies correctly (heavy gases at bottom, light at top).

### Phase 5 — Scenario suite rework (M7'.5)
**Demonstrate pressure across the board.**

New scenarios:
- `t1_buoyancy` (already in Phase 2)
- `t1_atmospheric` — gas column stratification
- `t1_vapor_pressure` — pure liquid in vacuum, evaporates at vapor-
  pressure rate
- `t1_pressure_column` — gradient-driven flow

Rework existing:
- `g5_pressure_drop` — currently fake; redo with proper pressure
- `g5_radiative_boundary` — adds evaporation
- `t1_ice_melt` — add gravity; demonstrate concurrent melt + buoyancy
- `t1_evaporation` — confirm or replace sorting-ruleset path

---

## Recommended approach for resume

**Apply the cross-phase-flux pattern:**

1. **Write `pressure_model_design.md`** (NEW doc, separate from this
   handoff plan). Should be self-contained for a fresh Claude chat
   session — same format as `cross_phase_flux_design_question.md` worked
   well for the cross-phase question. Include:
   - Problem framing (link to this file for fuller context)
   - Current code state (decode_pressure stub, scenario init expectations)
   - Proposed components (1–3 above) with concrete formulas
   - Open questions (1–7 above)
   - Default position for each open question (mine — for user to push
     back on)

2. **Surface to user** for review with a fresh chat session. Likely they
   come back with verdicts on the 7 open questions.

3. **Implement Phase 1 first.** Smallest scoped change; validate against
   existing 16/16 regression. Pressure becomes physically meaningful but
   nothing new is demonstrated yet.

4. **Iterate phases 2–5** with the user. Each phase is one or more
   commits, goldens regenerate, behavioural validation in the viewer.

**Don't go beyond Phase 1 without surfacing the open questions.** The
choices in #1–7 are foundational and cost a lot to undo. Cross-phase
flux taught us this pattern: ask first, build second.

---

## Where the sim is right now (resume context)

**gen5 branch, last push `5bfc6d7`:**
- 16/16 regression PASS (`python -m checker.regression_v2`)
- 11/11 self-tests PASS (`python -m checker.test_diff_ticks_v2`)
- Viewer functional (`viewer/viewer.html`, kg-native, Celsius display,
  history sparklines, four view modes)

**Recent commits (chronological, last → first):**
- `5bfc6d7` radiation + transitions sub-quantum residual
- `201302a` kg-native physics + volumetric phase_fraction + viewer Celsius/sparklines + ice-bath scenario
- `7ff532b` cohesion-as-attractor + compound calibration
- `aa3486b` phase_mass↔kg semantics — universal kg quantum + per-cell EQ
- `ab1fb9e` M6'.3 + M6'.4: t1_humidity + t1_condensation; asymmetry validated
- `a9cf3d9` M6'.2: sorting-ruleset extension + t1_evaporation
- `0fb7296` M5'.5c + M5'.7c: energy-balanced transitions, log-encoded energy_raw, drop schema_version
- `4e8a6e3` enshrine verdant_sim_design.md as canonical; archive schema-v1 wiki

**Current kg-native physics state (M6'.x):**
- `Q_KG = 1.0` — 1 hex unit of phase_mass = 1 kg of matter
- `LOG_E_MULTIPLIER = 8000` — covers 0..~150 MJ per cell in u16
- `FLOW_SCALE_KG ≈ 3.143e-8` — bridge for legacy `PHASE_CONDUCTANCE`
- Cell volumes scenario-dependent (water 0.1 m → 1 kg/cell; Si 0.01 m)
- Phase_fraction derived volumetrically from phase_mass + per-phase
  densities each cycle in `derive.run_derive`
- Sub-quantum energy residual in `cells.energy_residual: f32[N]` used
  by flux.integrate, apply_radiation, apply_phase_transitions, apply_ratchet

**Known fragility / pending design issues** (separate from pressure work):
- Constant-cp linearisation in energy-balanced phase transitions still
  caps at 1/8 per cycle (M7'+ wants enthalpy-aware energy field)
- `FLOW_SCALE_KG` is a legacy-bridge magic number; M7'+ wants Darcy-style
  per-phase permeability with proper units
- t1_ice_melt uses dt=60 s/tick to make conduction observable in 30 ticks
  (slow real water conductivity makes default dt impractical for demos)
- Latent heat at phase boundaries uses compound props when tagged, atomic
  blend otherwise — compound calibration only exists for H₂O so far

**Authoritative reference docs (in repo):**
- `verdant_sim_design.md` — canonical physics design (commit `4e8a6e3` enshrined)
- `gen5_implementation_spec.md` — architectural commitments
- `HANDOFF.md` — session-resume primer (general)
- `PRESSURE_HANDOFF.md` — this file (pressure-specific plan)
- `cross_phase_flux_design_question.md` — historical (verdict implemented in M6'.2)

**To resume:**
1. Read `verdant_sim_design.md` (canonical)
2. Read this file
3. Run `python -m checker.regression_v2` (should be 16/16 green)
4. `git log --oneline | head -10` for cadence
5. **Default action:** write `pressure_model_design.md` per the
   "Recommended approach" section above; surface for user review
   before implementing.
6. Don't dive into code until the open questions in #1–7 have user
   verdicts.

**Working principle to keep:** the user has corrected me twice already
in this thread for overclaiming success. Be honest about what's working
and what's broken. The current sim demonstrates conduction, radiation
(slowly), phase transitions, cohesion attraction, and cross-phase
sorting — but it's not a sim of physics, it's a sim of those specific
mechanisms. Pressure is the bridge to actual physics.
