# Tier 0 → gen5 Reusability Audit

**Audit scope:** `reference_sim/`, `checker/`, `data/`, `wiki/`
**Reference architecture:** `verdant_sim_design.md` (gen5)
**Date:** 2026-05-02

## Executive summary

The Tier 0 codebase was authored against the wiki framework: scalar pressure
(u16 log-encoded), 4-slot composition, integer mohs ladder, μ-gradient mass
auction with bidder-ignorant capacity check, ratchet via i8 strain saturation
sentinel, three-tier overflow cascade, schema-v1 JSON.

gen5 keeps about 30% of the surface but rewrites the inner physics:

- **State model changes:** 4 → 16 composition slots; phase becomes a
  fractional distribution (4 phases summing ≤1) not a single u2 enum;
  per-phase mass content replaces a single bulk mass concept; six **petals**
  (per-direction persistent stress/momentum/topology) replace the i8
  `elastic_strain` scalar; vacuum becomes "pressure at encoding floor" not a
  separate phase or flag; identity is computed not stored.

- **Pipeline changes:** Stages 0a–0e + 1 + 2/3/4 + 5 + emit collapses into
  Derive (Φ analog: gravity vector field) + concurrent per-phase sub-passes
  (gas 3 / liquid 5 / solid 7 running simultaneously, not serialized) + flux
  summation + integration + emit. Mass auction with bidders is replaced by
  blind summation of fluxes from overlapping 7-cell regions.

- **Things that survive verbatim:** axial-(q,r) hex grid + 6-neighbor table,
  ring/disc generators, dt convention (1/128 s), TSV-loader pattern, hashed
  element table, JSON I/O scaffolding, regression-driver pattern, baseline-
  flag/exit-code conventions, scenario-as-Python dataclass pattern.

- **Things that need to die:** μ scratch buffer; bidder-ignorant capacity
  check; cohesion-as-bool-graph; i8 strain saturation sentinel for ratchet;
  intact-solid ∞-cohesion-barrier μ term; Tier 3 refund + EXCLUDED; whole
  resolve.py (latent-heat shedding via single-target neighbor search,
  composition-weighted melt/boil thresholding, Curie demag in-place);
  schema-v1 cell shape (specifically `composition[4]`, `phase: enum`,
  `pressure_raw: u16`, `elastic_strain: i8` — all need replacement).

The good news: the **plumbing** (grid, scenarios, JSON IO, checker harness)
is largely framework-agnostic. The **physics inside the stages** is
framework-specific and overhauls.

---

## reference_sim/

### `reference_sim/__init__.py` — REUSE AS-IS
Trivial package docstring. No structural content. Keep.
- File: `C:\projects\VerdantSim\reference_sim\__init__.py`

### `reference_sim/grid.py` — REUSE AS-IS
Axial (q, r) hex coords, 6-neighbor table with `OPPOSITE_DIRECTION` lookup,
`ring_of`, `hex_disc_coords`, `build_hex_disc`. gen5 confirms pointy-top hex
in axial coords with N/NE/SE/S/SW/NW neighbors (verdant_sim_design.md §State
representation > Grid). The `NEIGHBOR_DELTAS` ordering convention this file
fixes (lines 27–34) is exactly what gen5's region kernels and flux records
will index into.
- Carries forward verbatim: `HexGrid`, `NEIGHBOR_DELTAS`, `OPPOSITE_DIRECTION`,
  `axial_distance`, `ring_of`, `hex_disc_coords`, `build_hex_disc`,
  `valid_neighbors`, `is_boundary`.
- One nit for gen5: gen5 requires the active region be **convex** (gravity
  diffusion §Architecture). Add a `is_convex(grid)` helper later; doesn't
  invalidate anything in this file today.
- File: `C:\projects\VerdantSim\reference_sim\grid.py`

### `reference_sim/cell.py` — RETIRE (full rewrite)
Every concrete field is wrong for gen5:

- `composition` is `int16[N, 4, 2]`. gen5 specifies `[(element_id u8,
  fraction u8) × 16]` summing to 255 (verdant_sim_design.md §Per-cell state
  bullet 1).
- `phase: u8` with `PHASE_SOLID/LIQUID/GAS/PLASMA = 0..3` is a single-phase
  enum. gen5 has fractional phase distribution: four floats summing to ≤1,
  with vacuum = complement (§Per-cell state bullet 2).
- No phase-fraction mass field. gen5 requires per-phase mass: "the quantity
  each phase fraction seeks to hold near its phase density equilibrium center"
  (§Per-cell state bullet 3).
- `mohs_level: u8` is a scalar. gen5 needs per-cell, per-solid-component
  Mohs (§Per-cell state bullet 6 — "Mohs level: per-cell, per-solid-
  component").
- `elastic_strain: i8` doesn't exist in gen5; gen5 replaces it with **petals**
  (six per cell, one per neighbor direction, each carrying directional
  stress + cohesion working value + accumulated velocity/momentum + topology
  flags — §Petal data, lines 338–348).
- `magnetization: i8` is parked indefinitely (gen5 lists "ferromagnetic
  composition scenarios" as a future extension, §Sorting ruleset paragraph
  9).
- Missing in gen5: a `sustained_overpressure_magnitude` f32 integrator
  (replaces ratchet i8 sentinel — §Per-cell state bullet 7).
- Missing in gen5: per-cell gravity vector slot (one f32×2 per cell, the
  diffused vector field — §Gravity as a first-class diffused vector field).

What gen5 replaces with: a new `cell.py` defining a `CellArrays` SoA with:
- `composition[N, 16, 2]` (element_id u8, fraction u8)
- `phase_fraction[N, 4]` f32 (solid, liquid, gas, plasma; sum ≤ 1.0)
- `phase_mass[N, 4]` f32 (mass per phase; targets equilibrium centers
  42 / 1764 / 74088)
- `pressure_raw[N]` u16 (log-encoded, but encoding is gen5-shaped: deviation
  from phase density equilibrium center, not from arbitrary zero — §Per-cell
  state bullet 4 "expressed as deviation from the phase density equilibrium
  center")
- `temperature[N]` u16 encoded / f32 working
- `energy[N]` f32 / u16 encoded
- `mohs[N, ?]` per solid component (probably u8 dominant + sparse table)
- `sustained_overpressure[N]` f32
- `petals[N, 6]` struct with directional stress f32, accumulated velocity f32,
  topology flags u8 (cached border type, is_inert, etc.)
- `gravity_vector[N, 2]` f32

`COMPOSITION_SLOTS = 4` becomes `COMPOSITION_SLOTS = 16`. `PHASE_*`
constants stay as indices into the 4-element phase distribution arrays.
`PHASE_NAMES` stays. `composition_sum`, `composition_as_list`,
`set_single_element` all need adjustments for slot count and the new
phase model but the *pattern* (helper functions to AoS↔SoA convert) carries
forward.

- File: `C:\projects\VerdantSim\reference_sim\cell.py`

### `reference_sim/flags.py` — PARTIAL
Carries forward:
- The named-bit / `flags_to_dict` / `flags_from_dict` / `describe` pattern
  is fine. Just a u8 with bit constants.
- `RADIATES`, `INSULATED` map directly to gen5's per-channel border behavior
  (§Borders and boundary conditions > Per-channel configurable behavior:
  "Thermally insulating but mass-permeable", "Radiatively coupled to a fixed
  ambient temperature").
- `FIXED_STATE` maps to "Held at fixed temperature regardless of incoming
  flux" / "Held at fixed flux" border types.
- `NO_FLOW` maps to "Mass-sealed but thermally conductive" or "Fully sealed".
- `FRACTURED` is implicit in gen5 (a solid that has yielded enough to crack);
  status TBD whether explicit flag still needed.

Retire (gen5 replaces):
- `CULLED` — gen5 has Tail-at-Scale culling but it's a tier-promotion
  decision (hot → warm → cold), not a per-cell flag. Replaced by
  hot/warm/cold tier membership (§Memory tiers).
- `RATCHETED` — the i8-saturation sentinel approach is dead; ratcheting
  in gen5 fires when the f32 sustained-overpressure integrator crosses a
  trigger value, not via a flag (§Mohs ratcheting). Could keep as a
  per-tick debug flag but not load-bearing.
- `EXCLUDED` — gen5 has no "Tier 3 refund + EXCLUDED" cascade. Pressure
  clamps at the encoding floor/ceiling and the cell becomes vacuum or hits
  the encoding wall (§Vacuum, §Phases and density equilibrium centers
  paragraph "however, the canonical packed encoding..."). No EXCLUDED flag
  in gen5's model.

Migration: `PRESET_*` recipes are gen5-shaped — they map to the gen5 border
properties table approach (§Border properties table). Keep the recipe idea,
move the data into a gen5 border-types TSV at the system boundary.

- File: `C:\projects\VerdantSim\reference_sim\flags.py`

### `reference_sim/element_table.py` — REUSE WITH ADAPTATION
**Loader pattern stays.** Hash-based reproducibility (lines 181–188 with
CRLF normalization) is exactly what gen5 wants ("hash of the element table
is recorded in save files for reproducibility" — verdant_sim_design.md
§Material identity). TSV format, dataclass-with-validation, line-by-line
error messages, CLI smoke test — all good.

**Element dataclass needs gen5 columns:**

Existing columns gen5 keeps as-is or with minor scaling:
- `symbol`, `Z`, `name`, `element_id` — unchanged
- `molar_mass` — still drives gas density scaling (§Per-element density
  scaling: "Gas density scales by molar mass")
- `density_solid`, `density_liquid`, `density_gas_stp` — drives the
  per-element scaling of phase equilibrium centers (§Per-element density
  scaling)
- `specific_heat_*`, `thermal_conductivity_*` — energy/temp computation,
  unchanged
- `melt_K`, `boil_K`, `L_fusion`, `L_vaporization`, `critical_T`,
  `critical_P` — gen5 keeps phase-diagram lookups (§Cross-phase dynamics
  passim, §Region kernels > Phase-dependent transport rules paragraph
  "Phase transitions ... 2D phase diagram lookup")
- `mohs_max` — caps ratcheting at element-specific limit (gen5 §Mohs
  ratcheting "ceiling is diamond at Mohs 10")
- `elastic_modulus`, `elastic_limit`, `tensile_limit` — drives stress flux
  records (§Flux records bullet 4)
- `emissivity_*`, `albedo_*` — radiation/absorption at borders (§Borders
  bullet 5)

Existing columns gen5 retires:
- `mohs_multiplier` — the geometric Mohs-ladder scaling lives implicitly
  in gen5 via "ratchet step raises the cell's yield threshold geometrically"
  (§Mohs ratcheting), but the per-element multiplier becomes implicit; can
  drop the explicit column.
- `pressure_mantissa_scale_gas/liquid/solid` — gen5 changes pressure
  encoding (deviation from phase center, not absolute Pa via shift-decode);
  these specific scale columns retire. Replaced by gen5's
  phase-density-equilibrium-center per element + a single log-encoding
  range param.
- `P_U_coupling_solid/liquid/gas` — Tier 2 P↔U cascade is gone (no overflow
  cascade in gen5; cells just go to vacuum or hit the encoding wall).
- `precipitation_rate_default`, `dissolution_rate_default` — gen5 doesn't
  carry these as element-table constants; precipitation falls out of
  cohesion + phase-diagram + diffusion (§Cross-phase dynamics > Precipitation,
  §Cohesion paragraph "Precipitation and crystal growth").

Existing columns that stay but with adapted semantics:
- `is_ferromagnetic`, `curie_K`, `susceptibility`, `remanence_fraction`
  — magnetism is Future Work in gen5 (§Sorting ruleset last paragraph
  "Magnetic sorting for ferromagnetic-composition scenarios is a future
  extension"); keep the columns, leave the code paths empty for now.

New columns gen5 needs added:
- `phase_density_center_solid` — per-element scaling factor on the
  74,088 default (§Per-element density scaling)
- `phase_density_center_liquid` — per-element scaling on 1,764
- `phase_density_center_gas` — per-element scaling on 42 (mostly molar-mass
  driven; could be derived rather than tabulated)
- `mohs_initial_at_phase_diagram` — gen5's "phase-diagram initial assignment"
  (§Per-cell state bullet 6) wants a per-element entry mapping (P, T) →
  initial Mohs

Validation logic carries forward (lines 255–358) with adjustments to drop
the dead columns. Encode/decode helpers (`decode_pressure`, `encode_pressure`,
`decode_energy`, `encode_energy`) are framework-specific to the pressure-as-
absolute-Pa-via-shift model; rewrite for gen5's deviation-from-center
encoding. The `MANTISSA_MASK = 0x0FFF` 12-bit mantissa is gen5-compatible
in shape, just with different decoding semantics.

- File: `C:\projects\VerdantSim\reference_sim\element_table.py`
- Lines that survive: 1–248 (loader infrastructure, with column-list
  adjustments), 460–536 (CLI smoke test pattern)
- Lines that need rewrite: 28–91 (Element dataclass columns), 380–457
  (encode/decode under gen5 semantics)

### `reference_sim/scenario.py` — REUSE WITH ADAPTATION
The dataclass scaffolding (Scenario, WorldConfig, EmissionConfig) is sound
and gen5-friendly. The **shapes** of WorldConfig need gen5 fields:

WorldConfig field-by-field:
- `dt = 1/128` — REUSE AS-IS. gen5's "the cycle window" maps onto this
  (verdant_sim_design.md doesn't pin dt explicitly but the per-phase pass
  budgets 3/5/7 are sub-cycle).
- `g_sim` — RETIRE. gen5 replaces the scalar G_sim with point sources +
  border-seeded gravity diffusion (§Gravity as a first-class diffused
  vector field). New fields: `gravity_sources: tuple[GravitySource, ...]`
  where each source has position + mass.
- `t_space`, `solar_flux` — REUSE AS-IS. Map to "Radiatively coupled to
  a fixed ambient temperature (space-facing exterior)" border config
  (§Borders bullet 4) and "fixed flux (solar input, geothermal input)"
  (§Borders bullet 6).
- `precipitation_rate_multiplier`, `dissolution_rate_multiplier` — RETIRE.
  Precipitation emerges from primitives in gen5; no rate knob.
- `conv_cap_gas/liquid/solid` — REUSE WITH ADAPTATION. gen5 fixes these
  at 3/5/7 globally (verdant_sim_design.md §Phases and density equilibrium
  centers table column "Pass count"). Move from per-scenario to
  module-level constants in the new physics code; remove from WorldConfig.
- `convergence_threshold` — REUSE AS-IS, but reinterpret as the noise
  floor ε for tail-at-scale culling (§Tail at Scale > "noise floor ε is a
  tunable parameter").
- `magnetism_enabled` — REUSE AS-IS as a feature flag.
- `cell_size_m` — REUSE AS-IS.

Add to WorldConfig for gen5:
- `gravity_sources: tuple[(position, mass), ...]` (§Setup phase)
- `border_config_table: dict[border_type, BorderProperties]` (§Border
  properties table)
- `noise_floor_epsilon: float` (the ε for culling and motion-threshold)
- `phase_pass_budgets: tuple[int, int, int, int]` — gas/liquid/solid/plasma
  (3/5/7/3 default per gen5 table)

EmissionConfig — REUSE AS-IS. The `mode/output_dir/include_*` pattern is
framework-agnostic. gen5 will want different `include_*` knobs (gravity
field debug overlay, flux records, petals dump) but the shape is right.

Scenario dataclass — REUSE AS-IS. `name/grid/cells/world/emission/
element_table/allowed_elements/description` is the right bundle.

- File: `C:\projects\VerdantSim\reference_sim\scenario.py`
- Carries forward: lines 50–58 (EmissionConfig), 60–75 (Scenario)
- Needs gen5 changes: lines 29–48 (WorldConfig fields)

### `reference_sim/derive.py` — RETIRE (mostly)
Inversion of the gen5 model. Specific failures:

- `DerivedFields.phi` — RETIRE. Φ scalar potential is replaced by the
  diffused gravity vector field (§Gravity as a first-class diffused vector
  field). Different solver (vector Jacobi diffusion seeded from border, not
  Poisson on density), different storage (per-cell vector, not scalar), and
  it's a *first-class field maintained alongside pressure/energy*, not a
  pre-stage scratch buffer.
- `DerivedFields.cohesion: bool[N, 6]` — RETIRE. gen5 cohesion is a
  per-cell per-direction *scalar damping coefficient* recomputed inside the
  region kernel during flux computation, not a stored bool graph (§Cohesion
  > "Cohesion is a per-cell per-direction scalar... transient working value
  inside the region kernel — never stored in persistent cell state").
- `DerivedFields.mu` — RETIRE WHOLESALE. There is no μ in gen5. Mass flow
  is driven by gradient + cohesion damping + sorting-ruleset exposure
  weights computed inside region kernels, not a global μ scratch buffer.
  This is the single biggest framework difference.
- `DerivedFields.b_field` — RETIRE for now (magnetism is future work in
  gen5).
- `DerivedFields.temperature` — REUSE WITH ADAPTATION. gen5 keeps "Temperature
  is derived from energy + heat capacity + composition" (§Per-cell state
  bullet 5). The composition-weighted c_p computation in `stage_0c_temperature`
  (lines 167–200) is gen5-shaped. Just relocate from "Stage 0c derive" to
  "computed inline in the region kernel when needed."

Code paths that retire:
- `stage_0a_gravity` (lines 53–119): wrong solver (Poisson on density via
  Jacobi); wrong target (scalar Φ); wrong boundary condition (Dirichlet 0;
  gen5 uses border-seeded values from point-source contributions).
- `_compute_density`, `_phase_density` (lines 95–119): the *concept* of
  composition-weighted phase-density carries forward to gen5's μ-less flux
  computation, but extracted helpers are entwined with the `phase: u8` field
  so they need restructuring for fractional phases.
- `stage_0b_cohesion` (lines 126–160): bool graph approach is dead. Replaced
  by the per-cycle scalar `cohesion(self, dir) = f(shared_majority_match) ×
  g(self.purity)` computed inside region kernels (§Cohesion).
- `stage_0c_temperature` (lines 167–200): math survives; relocate.
- `stage_0d_magnetism` (lines 227–240): no-op stub anyway, leave as future-
  work scaffold.
- `stage_0e_chemical_potential` (lines 247–326): dead. μ doesn't exist.
- `_decode_pressure_all` (lines 293–326): the encode/decode round-trip
  pattern is fine but pressure encoding semantics change (deviation from
  phase center, not absolute Pa).
- `run_derive_stage` (lines 333–344): orchestrator dies along with the
  five-stage decomposition.

What gen5 replaces it with: a single Stage 0 that solves the gravity
diffusion (vector Jacobi from frozen border + active mass perturbation)
and updates the canonical state encodings; everything else moves into the
concurrent region kernels.

- File: `C:\projects\VerdantSim\reference_sim\derive.py`
- Carries forward in spirit (relocated to region kernels): lines 95–119
  (`_compute_density`/`_phase_density`), 167–200 (temperature derivation
  math), 203–209 (`_phase_specific_heat`)

### `reference_sim/resolve.py` — RETIRE
Almost entirely framework-specific. Specific issues:

- `ELASTIC_STRAIN_SATURATED = 127` cross-tick sentinel via i8 saturation
  (lines 53–56) — RETIRE. gen5 uses an f32 sustained-overpressure
  integrator (§Per-cell state bullet 7).
- `_resolve_ratchet` (lines 91–153): the *concept* of ratcheting carries
  forward (mohs_level++, exothermic compression work to energy, strain
  reset) but the *trigger* (i8 sentinel) and the *energy formula* (½σ²/E×V
  at saturation) need restructuring to fire on integrator threshold.
- `_resolve_phase` (lines 160–206): T-thresholding from composition-weighted
  melt/boil is gen5-incompatible. gen5 phase resolution is per-cell per-cycle
  via 2D phase diagram lookup `(pressure, temperature) → (phase, initial_mohs)`
  with **fractional** phase distribution outputs (§Region kernels > Phase-
  dependent transport rules last paragraph; §Per-cell state bullet 2). Single-
  phase enum flip is wrong shape.
- `_resolve_curie` (lines 213–236): magnetism deferred.
- `_resolve_latent_heat` (lines 243–306): "find a fluid neighbor and shed
  to it" approach is wiki-specific. gen5 handles latent heat as part of
  flux-record energy at phase-transition-firing cells; mass redistribution
  goes through normal flux, not a separate shed-to-one-target step.
  (§Region kernels > Phase-dependent transport rules > "When a phase
  transition occurs, the cell's phase distribution updates; the kernel writes
  appropriate flux records for any mass/energy redistribution".)
- `_resolve_precipitation` (lines 313–342): unimplemented stub anyway, and
  gen5 has precipitation falling out of phase diagrams + cohesion + diffusion
  with no special sub-stage (§Cross-phase dynamics > Precipitation).
- `SELF_CHANNEL = 0` direction-0-as-self-delta hack (lines 61–62) — RETIRE.
  The whole PropagateBuffers structure is wrong-shape for gen5.

What gen5 replaces with: phase transitions and ratcheting fire **inside
the region kernels** as part of the per-cell cycle decision; there's no
separate "Stage 1 resolve" pass. The pipeline is Setup → Cycle (concurrent
phase sub-passes with phase transitions baked in) → Integration → Emit.

- File: `C:\projects\VerdantSim\reference_sim\resolve.py`
- Nothing carries forward intact; the ratchet/Curie/precipitation
  *triggers* migrate into per-cell-per-cycle decisions inside region kernels.

### `reference_sim/propagate.py` — RETIRE (mostly)
The entire file is wiki-framework-shaped:

- `PropagateBuffers.mass_deltas: int32[N, 6, 4]` (lines 38–69) — RETIRE.
  Replaced by gen5's flux records (§Flux records, §Flux summation). Flux
  records carry mass-per-species-per-phase, momentum, energy, stress,
  phase-identity metadata in one structure; not separate per-stage delta
  buffers.
- `STRAIN_SATURATION = 127` (line 34) — RETIRE.
- `stage_2_elastic` (lines 76–178): Jacobi-on-cohesion-graph for strain
  diffusion is gen5-incompatible. gen5 transmits stress via **petal stress
  flux records** crossing edges (§Flux records bullet 4 "Stress flux —
  directional stress being transmitted across this edge this cycle. Updates
  the petal stress values on both sides during integration"). Per-bond
  fracture detection (lines 181–239) carries forward in concept (excessive
  bond stress → yield/fragmentation event) but the implementation relocates
  to inside the region kernel's solid transport rule.
- `stage_3_mass` (lines 268–471): the **mass auction** is gen5's biggest
  retirement. Specifics:
  - "downhill-μ neighbors" — μ doesn't exist in gen5.
  - "bidder-ignorant capacity check" (lines 415–424) — gen5 explicitly
    rejects this: "Atomic contention is the enemy of GPU throughput.
    Blind summation eliminates atomics; each contribution adds independently"
    (§Core principle paragraph "Blind flux summation"). Replaced by blind
    sum of all contributing region's flux records, with veto stage for
    hard constraints only (§Flux summation > Sum, don't arbitrate; Veto
    stage for hard constraints).
  - "cohesion barrier intact solids cannot bid for their dominant element
    across cohesive bonds" (lines 393–396) — replaced by cohesion damping
    on outgoing flux (§Cohesion > "Cohesion is consumed by the cell's own
    flux computation as a damping coefficient"). High cohesion suppresses
    flux; ∞-barrier disappears.
  - CULLED on no-eligible-path — replaced by tail-at-scale region culling
    based on noise floor ε (§Tail at Scale).
- `stage_4_energy` (lines 512–532, 535–668): conduction Jacobi on T gradient
  is gen5-shape (§Region kernels > Phase-dependent transport rules >
  "thermal coupling to neighbors"); but it's per-phase-per-region, not a
  separate Stage 4. Convection coupling from mass deltas (`_apply_convection`,
  lines 671–747) is gen5-shape (§Flux records bullet 3 "Energy flux —
  kinetic + thermal + pressure work" + bullet 1 "Mass flux"); same
  relocation. Radiation (`_apply_radiation`, lines 799–844) is gen5-shape
  for RADIATES border cells; relocates to integration step.
- `_composition_weighted_*` helpers (lines 242–261, 750–796) — REUSE WITH
  ADAPTATION as utility math. Switch from looping over 4 slots to 16, drop
  `phase: u8` lookup in favor of fractional phase weights.
- `OPPOSITE_DIRECTION` import — REUSE AS-IS.
- The orchestrator `run_propagate_stages` (lines 857–869) — RETIRE. gen5
  runs phases concurrently within one cycle, not sequentially across three
  stages.

What gen5 replaces with: a single `cycle.py` that runs concurrent phase
sub-passes (gas 3, liquid 5, solid 7) over hot-tier regions, with each
region kernel computing flux records (mass + momentum + energy + stress
+ identity metadata) for its 6 edges, blind-summed at the end and
integrated.

- File: `C:\projects\VerdantSim\reference_sim\propagate.py`
- Carries forward in spirit: composition-weighting helpers (lines 242–261,
  750–796), radiation Stefan-Boltzmann math (lines 821–844)

### `reference_sim/reconcile.py` — RETIRE
Pure overflow-cascade implementation:

- `U16_MAX = 0xFFFF`, `FRAC_MAX = 255` (lines 35–36) — RETIRE the cascade
  semantics. The encoding limits remain as overflow-protection clamps
  (§Phases and density equilibrium centers > "values that would exceed the
  encoded ceiling or fall below the encoded floor are clamped at the
  encoding boundary. This is overflow protection, not physics").
- `run_reconcile_stage` Tier 2 P↔U coupling (lines 95–104) — RETIRE.
  Pressure conversion to energy via thermodynamic_coupling factor is wiki-
  framework-specific. gen5 has no P↔U coupling; pressure clamps and motion
  stops, no pressure-to-energy transfer.
- Tier 3 refund + EXCLUDED scatter (lines 106–183) — RETIRE entirely. gen5
  rejects refunds: cells just clamp at the encoding boundary, become vacuum
  if pressure floors out, or hit the encoding wall as bounded-scenario
  protection (§Phases and density equilibrium centers, §Vacuum, §Scenario
  bounds and validation). No EXCLUDED state.
- `_composition_weighted_coupling`, `_solid_pressure_scale` — RETIRE
  (specific to the dead cascade).

What gen5 replaces with: a simple **integration kernel** (§Integration step):

```
new_center_state = current_center_state
                 + (sum of incoming fluxes across 6 edges)
                 − (sum of outgoing fluxes across 6 edges)
                 + (intra-cell transitions authored by region kernels)
```

with re-encoding of f32 working state to canonical packed representations
(log-scale u16 pressure, etc.) at the end. "Cavitation is permissive: a
cell that ends the cycle with less mass than it started with is allowed
to do so" (§Integration step) — directly contradicts the wiki's overflow
cascade.

- File: `C:\projects\VerdantSim\reference_sim\reconcile.py`
- Nothing carries forward; replaced by `integration.py`.

### `reference_sim/emit.py` — REUSE WITH ADAPTATION
The serialization scaffolding (write JSON, run-id timestamping, output_dir
handling, totals computation, self-report invariants section) is solid and
framework-agnostic. The cell **schema** needs gen5 changes:

What carries forward verbatim:
- `SCHEMA_VERSION` mechanism — bump to 2 for gen5.
- `emit_tick` orchestrator shape (lines 31–91): inputs (scenario, derived,
  buffers, tick, stage, cycle, run_id, stage_timing_ms), outputs the dict.
  Stage names change to gen5 ("post_setup", "post_cycle_<n>",
  "post_integration", etc.), but the structure stays.
- `_compute_totals` shape (lines 130–159) — needs gen5 fields
  (mass_by_element_by_phase, energy_total per phase, cells_in_warm_tier,
  cells_in_cold_tier, regions_culled etc.) but the pattern is right.
- `_self_report_invariants` (lines 162–207) — pattern carries forward;
  invariant set changes (composition_sum_255 still relevant for the 16-slot
  vector; mass_conservation_per_element still relevant; new invariants
  for phase_fraction_sum, gravity_field_consistency, etc.).
- `write_emission`, `new_run_id` — REUSE AS-IS.

What `_build_cell_object` (lines 94–127) needs changed for gen5:
- Drop `pressure_raw`, `pressure_decoded` as currently shaped — gen5
  pressure is "deviation from phase density equilibrium center", needs
  per-phase decode.
- Drop `phase: enum` — emit `phase_distribution: [solid, liquid, gas, plasma]`
  fractions.
- Drop `elastic_strain` — emit `petals: [{stress, velocity, topology}, ×6]`.
- Drop `magnetization` (or keep at zero pending future work).
- Add `phase_mass: [solid, liquid, gas, plasma]`.
- Add `gravity_vector: [x, y]`.
- Add `sustained_overpressure: float`.
- Composition shifts from 4 slots to 16; `composition_as_list` already
  filters trailing zeros so that adapts cleanly.

- File: `C:\projects\VerdantSim\reference_sim\emit.py`
- Carries forward: lines 130–230 (totals/invariants/write/run_id)
- Needs gen5 rewrite: lines 94–127 (`_build_cell_object` field set)

### `reference_sim/sim.py` — REUSE WITH ADAPTATION
Top-level driver. Pattern is right, stage list changes:

- argparse + module-import + `mod.build()` + scenario run loop — REUSE AS-IS.
- The five-stage timing dict (lines 73–78) — RETIRE in favor of gen5's
  promotion / sub-pass / re-encoding / emit timing.
- The new tick loop runs gen5's cycle structure (§Cycle structure):
  1. Promotion pass (tier management)
  2. Sub-pass loop per phase (gas 3 / liquid 5 / solid 7) — concurrent
  3. Re-encoding
  4. Render sync (optional)

- File: `C:\projects\VerdantSim\reference_sim\sim.py`
- Carries forward: lines 14–28 (imports/setup), 30–56 (run_scenario shell),
  92–95 (timing format), 97–132 (CLI main)
- Needs gen5 rewrite: lines 60–88 (the per-tick stage sequence)

### `reference_sim/scenarios/t0_static.py` — PARTIAL
The pattern (`build()` returns ready Scenario; load element table; populate
CellArrays; instantiate WorldConfig + EmissionConfig) is gen5-friendly.

But the *content* is wrong-shape:
- `set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)`
  uses the 4-slot composition; needs 16-slot.
- `cells.phase[cell_id] = PHASE_SOLID` uses single-phase enum; needs
  fractional phase distribution `[1.0, 0, 0, 0]` for full-solid + phase_mass
  set to 74,088 (gen5 solid equilibrium center).
- `cells.pressure_raw[cell_id] = 0` is interpretable as "at phase density
  center" in both frameworks but encoded differently.
- `cells.energy[cell_id] = DEFAULT_ENERGY_J` energy field carries forward.
- `cells.elastic_strain[cell_id] = 0` doesn't exist in gen5; replace with
  zero-init petals.
- `WorldConfig(g_sim=0.0)` — gen5 replaces with empty `gravity_sources=()`.

The conceptual scenario ("uniform Mohs-5 Si solid disc, expect zero deltas
every tick") is **re-authorable in gen5**. It's exactly the same physics
test (uniform-rest scenario, conservation invariants every cycle), just
expressed in gen5's state shape.

- File: `C:\projects\VerdantSim\reference_sim\scenarios\t0_static.py`
- Re-authorable verbatim once new `cell.py` lands.

### `reference_sim/scenarios/t0_compression.py` — PARTIAL
Same as `t0_static`: shell pattern reusable, internals need gen5 state.
The single-cell strain spike at center → propagate-via-cohesion test maps
to gen5 as a single-cell **petal stress spike** at center → propagate via
solid sub-pass flux records. Same physics test, gen5-shaped state.

- File: `C:\projects\VerdantSim\reference_sim\scenarios\t0_compression.py`
- Re-authorable in gen5; petal init replaces strain init.

### `reference_sim/scenarios/t0_ratchet.py` — PARTIAL
The "single cell at strain saturation sentinel +127, expect ratchet at
tick 1" is *specifically* testing the i8-saturation cross-tick mechanism
that gen5 retires. Gen5 ratchet test instead: place a cell with
sustained_overpressure already at threshold (or arrange persistent
overpressure for one cycle), expect mohs_level++, exothermic energy dump,
overpressure reset. The *intent* (verify ratchet fires when threshold hit)
carries forward; the *trigger mechanism* changes.

- File: `C:\projects\VerdantSim\reference_sim\scenarios\t0_ratchet.py`
- Re-author trigger in gen5; intent preserved.

### `reference_sim/scenarios/t0_fracture.py` — PARTIAL
"Opposing strains -127 and +120 produce bond stress > tensile_limit, both
cells FRACTURED at tick 1" — same as t0_ratchet, the *specific mechanism*
(i8 strain pair → bond stress check) retires; gen5 expresses this via
opposing petal stresses producing inter-cell flux exceeding tensile
limit → fragmentation event. Test intent preserves; setup changes.

- File: `C:\projects\VerdantSim\reference_sim\scenarios\t0_fracture.py`
- Re-author trigger in gen5; intent preserved.

### `reference_sim/scenarios/t0_radiate.py` — REUSE WITH ADAPTATION
The radiative-boundary cooling test is **gen5-clean**. Gen5 explicitly
keeps "Radiatively coupled to a fixed ambient temperature (space-facing
exterior)" as a first-class border behavior (§Borders bullet 4). Stefan-
Boltzmann emission to T_space is preserved. The only adaptation: the
ring-5 cells set RADIATES via flag → gen5 setup sets ring-5 cells'
border_type to a radiative entry in the border properties table, and
the radiation math runs at integration step.

The clever T-energy_scale calibration (lines 54–60) to keep ε σ T⁴ × A × dt
above u16 floor is gen5-relevant — gen5 still uses encoded energy with
finite resolution.

- File: `C:\projects\VerdantSim\reference_sim\scenarios\t0_radiate.py`
- Re-authorable; flag → border_type table swap.

### `reference_sim/scenarios/__init__.py` — REUSE AS-IS
Empty package marker. Trivial.

### `reference_sim/archive/sim_stub.py` — RETIRE
Already archived per `archive/README.md`. Predates the wiki framework
even, was a schema-stub for the harness. Not gen5-relevant. Keep archived
or delete; doesn't matter.

### `reference_sim/archive/README.md` — RETIRE (or update)
Documents an already-superseded artifact. Not load-bearing.

### `reference_sim/README.md` — RETIRE (rewrite)
Currently documents the wiki framework's stage decomposition. Needs full
rewrite when gen5 code lands. The "SoA memory layout matches what the CUDA
port will use" + "scenarios are Python, not a DSL" + "scratch buffers
allocated once per scenario" design notes (lines 84–97) carry forward in
spirit.

- File: `C:\projects\VerdantSim\reference_sim\README.md`

---

## checker/

### `checker/verify.py` — REUSE WITH ADAPTATION
**Excellent scaffolding, almost all of it survives.** The harness pattern is
framework-agnostic; only the specific invariant checks need updating.

What carries forward verbatim:
- argparse setup, exit-code conventions (lines 18–24, 357–414): 0/1/2/3/4
  for pass/fail/divergent/schema-error/baseline-incompat. **gen5 keeps these**;
  invariant violations should still produce non-zero exit codes the same way.
- `--baseline` flag pattern (lines 360–362, 382–393): load tick-0 ground
  truth for mass conservation. gen5 still wants this.
- `load_baseline_expected_mass` (lines 176–195): TSV-aware, fallback to
  cells if totals missing. Gen5-friendly.
- `check_baseline_compatible` (lines 198–225): run_id, element_table_hash,
  scenario, tick-ordering checks. All still apply.
- `verify` orchestrator (lines 230–294): runs every check, cross-references
  sim self-report against independent verdict, outputs divergences. **The
  divergence concept is gen5's bread and butter** — it's exactly the
  cross-validation invariant for Python↔CUDA correctness.
- `format_report` (lines 297–354) and `--json-report` mode (lines 364–366,
  404–407): all reusable.
- `check_composition_sums` (lines 36–45): sum-to-255 invariant survives;
  loop bound updates from 4 slots to 16, no other change.

What needs gen5 adaptation:
- `check_pressure_decoding` (lines 67–97): the hard-coded shift-decode
  formulas (gas: ×1, liquid: ×8, solid: ×8 × mult^(mohs-1), plasma: ×64)
  are wiki-specific. gen5 pressure encoding differs (deviation from phase
  density center). Replace with gen5's encoding.
- `check_mohs_range` (lines 100–114): "1–10 for solids, 0 for non-solids"
  was tied to single-phase enum. gen5 has fractional phases; the check
  becomes "for any cell with non-zero solid phase fraction, mohs is in 1–10".
- `check_bid_conservation` (lines 117–147): RETIRE. gen5 has no bids;
  fluxes are blind-summed. Replaced with `check_flux_conservation`: every
  flux A→B has matching B→A entry with negated magnitude (for non-veto
  edges).
- `check_flags_consistency` (lines 150–165): flag set changes; checks like
  "RATCHETED implies solid" become "ratchet event implies cell had non-zero
  solid fraction at trigger time". `culled_cell_sent_bids` retires (no bids).
- `check_mass_conservation` (lines 48–64): pattern fine; needs adaptation
  for the new emission shape (mass_by_element_by_phase or similar).

New gen5 invariants to add:
- `check_phase_fraction_sum`: phase fractions sum to ≤ 1.0 per cell;
  remainder is vacuum (verdant_sim_design.md §Per-cell state bullet 2).
- `check_gravity_vector_finite`: gravity_vector magnitudes within scenario
  bounds (§Scenario bounds and validation bullet 1).
- `check_petal_stress_bounded`: petal stress doesn't exceed yield-limit
  + tensile-limit windows.
- `check_no_pressure_floor_underflow_with_mass`: cells at vacuum pressure
  floor have near-zero phase masses (§Vacuum).

Bottom line for verify.py: 70% of the file is reusable.

- File: `C:\projects\VerdantSim\checker\verify.py`
- Carries forward: lines 18–35 (header/exit codes), 168–225 (baseline +
  compat), 230–414 (orchestrator/format/main)
- Needs adaptation: lines 36–165 (specific invariant check bodies)

### `checker/diff_ticks.py` — REUSE WITH ADAPTATION
Per-field tolerance pattern is exactly what gen5 wants for the
Python↔CUDA cross-validation (verdant_sim_design.md mentions emission as
the contract; cross-validation isn't called out explicitly but the
harness intent is clear from §Implementation patterns).

What carries forward verbatim:
- `_load`, `_compatible`, `_rel_close`, `diff_emissions`, `format_report`,
  `main` — all framework-agnostic.
- `REL_TOL = 1e-6` — keeps.
- Schema/scenario/cell-count compatibility checks — keep.
- Exit codes 0/1/2 — keep.

What needs gen5 adaptation: `_diff_cell` (lines 74–110) — the field list
grows:
- Drop `phase` enum exact-check (line 80); add `phase_distribution`
  per-element float-tolerance check (4 floats summing ≤1).
- Drop `pressure_raw` exact-check; add `pressure_raw_per_phase` exact-check
  (gen5 has phase-fraction-specific pressure if encoded that way).
- Drop `elastic_strain` exact (i8); add `petals[6]` per-direction structure
  with stress/velocity/topology float-tolerance.
- Drop `magnetization` (or keep at exact 0).
- Add `phase_mass` array float-tolerance.
- Add `gravity_vector` 2-vector float-tolerance.
- Add `sustained_overpressure` float-tolerance.
- Composition slots 4 → 16, otherwise unchanged.

Tolerances stay: integer-exact for u16/u8 fields, REL_TOL for f32 fields.
The pattern is gen5-correct.

- File: `C:\projects\VerdantSim\checker\diff_ticks.py`
- Carries forward: lines 1–73, 113–179
- Needs gen5 adaptation: lines 74–110 (field list)

### `checker/regression.py` — REUSE AS-IS
Driver pattern is purely about subprocess-calling the sim, verifier, and
diff-ticks. Framework-agnostic.

- The `ScenarioCheck` dataclass (lines 32–37) — keep.
- The `SCENARIOS` tuple (lines 40–46) — gen5 will fill with new scenarios
  (probably `g5_static`, `g5_uniform_compression`, etc.); the format
  stays.
- `_run_scenario`, `_verify`, `_diff_golden`, `regression_run` — all
  reusable.
- `GOLDEN_DIR`, `RUNS_DIR` paths and the cleanup-then-run pattern — keep.

- File: `C:\projects\VerdantSim\checker\regression.py`
- Reuses entirely; just update SCENARIOS list when gen5 scenarios land.

### `checker/test_diff_ticks.py` — REUSE WITH ADAPTATION
Pattern (lightweight assertions, no pytest dep, exit non-zero on failure)
is reusable. Test fixture `_cell` factory (lines 30–46) needs gen5 fields,
and the schema_version override in incompatibility tests (lines 100–104)
becomes "schema 1 vs 2" instead of "1 vs 2". The actual test cases
(identical, differs on field, float tolerance, composition exact, flags
exact, schema mismatch, cell-count mismatch) are gen5-relevant.

- File: `C:\projects\VerdantSim\checker\test_diff_ticks.py`
- Reuses pattern; update fixture + test data shape.

---

## data/

### `data/element_table.tsv` — REUSE WITH ADAPTATION
The single Si row (header + Si values) is gen5-relevant data — gen5 still
needs Z, name, density per phase, specific heat per phase, thermal
conductivity per phase, melt/boil/critical, mohs_max, elastic constants,
emissivity, albedo, magnetism columns. **Keep the row data**.

Column changes per gen5:
- Drop: `mohs_multiplier` (gen5 ladder is geometric implicit),
  `pressure_mantissa_scale_gas/liquid/solid` (encoding changes),
  `P_U_coupling_solid/liquid/gas` (no cascade), `precipitation_rate_default`,
  `dissolution_rate_default` (precipitation emerges).
- Add: `phase_density_center_solid` (multiplier on 74,088 default),
  `phase_density_center_liquid` (on 1,764), `phase_density_center_gas`
  (on 42 — could derive from molar_mass).

The actual physical values (densities, specific heats, melt/boil, elastic
modulus, etc.) are NIST-sourced and gen5-correct. The companion
`element_table_sources.md` documents these and stays valid.

For Tier 0 single-Si the file is one row (header + Si). Easy to migrate
column-wise.

- File: `C:\projects\VerdantSim\data\element_table.tsv`
- Reuses physical values; migrates column set for gen5.

### `data/element_table_sources.md` — REUSE AS-IS
Citations are physical-truth. Source file references survive any framework
change.

- File: `C:\projects\VerdantSim\data\element_table_sources.md`

### `data/compounds.tsv` — RETIRE OR DEMOTE
gen5 says: "Compound materials resolve to their element composition vector
at cell initialization (water = `[(H, 114), (O, 141)]`). The 118 real
elements fit in u8 with 137 slots for compound aliases if aliasing is
useful." (verdant_sim_design.md §Material identity)

So gen5 keeps the **concept** of compound aliases at init time, but doesn't
require a TSV — Python helpers are sufficient: e.g.,
`compounds.water = [("H", 28), ("O", 227)]` as a module constant. The current
TSV is mostly comments documenting future entries (no actual rows yet); 18
lines, 0 data rows in the data section.

Recommendation: convert to a Python `data/compounds.py` module with dict
constants. If a TSV is preferred for diff-friendliness, keep but gen5
doesn't need it as a separate file with a separate parser.

- File: `C:\projects\VerdantSim\data\compounds.tsv`

---

## wiki/

The wiki documents the auction/μ-gradient framework that gen5 supersedes.
Most pages are framework-specific and either retire wholesale or need full
rewrites. A few cross-cutting topics (dt/units, hex grid coords) carry
forward.

### `wiki/README.md` — RETIRE
Index page for the superseded wiki. Retire when gen5 docs replace it.
The "how to use this wiki" navigation pattern (by-topic) is reusable for
gen5 docs.
- File: `C:\projects\VerdantSim\wiki\README.md`

### `wiki/framework.md` — RETIRE
The whole page is "the model in one line: cells don't move — properties
move", followed by the stored/derived/flow categorization, the four-stage
pipeline, and the auction unification. gen5 keeps "cells don't move,
properties move" (verdant_sim_design.md §Cells are indivisible) but every
specific mechanism listed (μ-gradient mass flow, Stage 0a Φ Poisson, Stage
1 phase resolve, Stage 5 overflow cascade) is replaced.

The "what emerges for free" table (lines 90–110) carries forward in
**spirit**: gen5 has its own version of the same table (verdant_sim_design.md
§Cross-phase dynamics, §Cohesion bullets) where humid air, condensation,
evaporation, precipitation, ionization, ablation, aquifers, springs, oil
shale all "fall out from phase density centers, cohesion, computed identity,
diffusion during phase sub-passes, and phase-diagram transitions." Different
primitives, same emergent set.

- File: `C:\projects\VerdantSim\wiki\framework.md`

### `wiki/cell-struct.md` — RETIRE
Documents the wiki cell struct: 4 composition slots, single phase enum,
i8 elastic_strain, i8 magnetization. gen5 has 16 slots, fractional phase
distribution, six petals, no scalar strain (verdant_sim_design.md §Per-cell
state). Page is fully superseded.
- File: `C:\projects\VerdantSim\wiki\cell-struct.md`

### `wiki/flags.md` — RETIRE
Documents NO_FLOW/RADIATES/INSULATED/FIXED_STATE/CULLED/FRACTURED/
RATCHETED/EXCLUDED. gen5 retains the persistent four (NO_FLOW etc.) as
border-config table entries (§Borders), retires the transient four
(CULLED/RATCHETED/EXCLUDED replaced by tier promotion, integrator
threshold, and clamp-at-encoding; FRACTURED arguably preserved as fragmentation
event marker but framing changes).
- File: `C:\projects\VerdantSim\wiki\flags.md`

### `wiki/cohesion.md` — RETIRE
Wiki cohesion is a bool graph (recomputed each tick, used as ∞-barrier in
μ and as bond-existence in Stage 2). gen5 cohesion is a per-cell per-direction
**scalar damping coefficient** computed inside the region kernel, never
stored, applied as flux damping (§Cohesion). The composition-similarity
formula `f(shared_majority_match) × g(self.purity)` is gen5-specific. The
emergent behaviors table (surface tension, immiscibility, cleavage planes,
crystal growth, blob maintenance) is gen5-correct in concept.
- File: `C:\projects\VerdantSim\wiki\cohesion.md`

### `wiki/auction.md` — RETIRE
Documents the staged Jacobi auction with bidder-ignorant capacity check.
gen5 explicitly rejects this: "Atomic contention is the enemy of GPU
throughput. Blind summation eliminates atomics" + "There is no conflict
resolution, no voting, no winner selection among region contributions"
(verdant_sim_design.md §Core principle, §Flux summation). The whole concept
of bidders sending bids retires; replaced by overlapping regions blind-
summing flux records.
- File: `C:\projects\VerdantSim\wiki\auction.md`

### `wiki/overflow.md` — RETIRE
Three-tier overflow cascade (cavitation → P↔U coupling → refund + EXCLUDED)
is wiki-specific. gen5 has no cascade: cells clamp at the encoding boundary
as overflow protection only, vacuum is "pressure at the encoding floor"
not a special case, and refunds + EXCLUDED don't exist (verdant_sim_design.md
§Phases and density equilibrium centers, §Vacuum, §Integration step "Cavitation
is permissive").
- File: `C:\projects\VerdantSim\wiki\overflow.md`

### `wiki/derived-fields.md` — RETIRE
Documents Φ + cohesion + T + B + μ. gen5 keeps T as derived (computed inline),
keeps cohesion as in-kernel transient (not "derived field" — never stored),
replaces Φ scalar with diffused gravity vector field (a *first-class field
maintained alongside pressure/energy*, not a per-tick scratch — §Gravity as
a first-class diffused vector field), retires μ entirely, defers B.
- File: `C:\projects\VerdantSim\wiki\derived-fields.md`

### `wiki/pipeline.md` — RETIRE
Documents Stage 0a/0b/0c/0d/0e + 1 + 2/3/4 + 5a/5b/5c + 6 sequential pipeline.
gen5 has Setup → Cycle (concurrent phase sub-passes, not sequential stages)
→ Integration → Emit (verdant_sim_design.md §Cycle structure). Stage names
and ordering all change.
- File: `C:\projects\VerdantSim\wiki\pipeline.md`

### `wiki/convergence.md` — PARTIAL
The 3/5/7 per-phase budget table (lines 7–14) is **gen5-correct**
(verdant_sim_design.md §Phases and density equilibrium centers table).
The CULLED-vs-EXCLUDED distinction (lines 50–61) retires (gen5 has tier
promotion, not CULLED, and no EXCLUDED at all). The convergence-criterion
math (max |Δstate| / max |state| < threshold) maps to gen5's noise floor ε
threshold (§Tail at Scale).

What carries forward: the 3/5/7 budgets, the noise-floor-ε convergence-
exit pattern, the per-phase-per-stage independent convergence concept.

What retires: the serial/interleaved-vs-default discussion (gen5 mandates
concurrent), CULLED, EXCLUDED, the inter-stage sync within tick (gen5
runs phases concurrently within one cycle).

- File: `C:\projects\VerdantSim\wiki\convergence.md`

### `wiki/dt-and-units.md` — REUSE AS-IS (mostly)
Most of this is gen5-correct:
- 1 tick = 1/128 s — keeps as default cycle rate.
- SI throughout (kg/m³, Pa, J, K, etc.) — keeps.
- u16 encoding for pressure/energy — gen5 keeps log-scale u16 pressure
  (verdant_sim_design.md §Per-cell state bullet 4) and u16 encoded
  temperature/energy (bullets 5–6).
- CFL stability discussion (lines 51–68) — gen5-correct.
- NIST-sourced constants — unchanged.
- Cross-validation units section (lines 102–107) — gen5-correct.

Retires:
- Specific scale-decode formulas (gas: mantissa × 1, liquid: mantissa × 8,
  solid: mantissa × 8 × mult^(mohs-1)) — gen5 changes these.
- Real `G_sim` discussion — replaced by point-source + diffusion in gen5.
- Rate multipliers (precipitation_rate_multiplier etc.) — gen5 doesn't
  have these.

- File: `C:\projects\VerdantSim\wiki\dt-and-units.md`
- 70% gen5-applicable; just strip the wiki-specific encoding formulas and
  rate-multiplier section.

### `wiki/mass-flow.md` — RETIRE
The whole page is the μ formula + auction mechanics + bidder-ignorant
capacity check + cohesion ∞-barrier as μ term + magnetic μ term. All wiki-
specific. gen5 mass flow is per-phase sub-passes computing flux records
inside region kernels with cohesion damping and sorting-ruleset exposure
weights (verdant_sim_design.md §Region kernels, §Cohesion, §Cells are
indivisible > sorting ruleset). Different math, different memory model.
- File: `C:\projects\VerdantSim\wiki\mass-flow.md`

### `wiki/energy-flow.md` — PARTIAL
- Conduction-via-T-gradient with κ_bond = min/harmonic-mean — gen5-correct
  in concept (§Region kernels phase rules carry conduction).
- Convection coupled to mass flow — gen5-correct (§Flux records bullet 1+3:
  mass and energy flux carried together).
- Radiation Stefan-Boltzmann from RADIATES cells — gen5-correct (§Borders
  bullet 5 "Radiatively coupled to a fixed ambient temperature").
- The specific "Stage 4 reads Stage 3's delta buffer" plumbing — retires
  (no separate stages).
- Per-phase budget shared with Stage 3 — retires.
- Energy underflow clamp at zero — gen5-correct (§Integration step "Cavitation
  is permissive").
- File: `C:\projects\VerdantSim\wiki\energy-flow.md`
- Conceptual content carries; mechanism section retires.

### `wiki/elastic-flow.md` — RETIRE
The wiki's elastic flow is a Jacobi sweep on a bool cohesion graph for the
i8 strain field, with i8-saturation triggering ratchet and tensile limit
triggering FRACTURED. gen5 retires all of this:
- Strain is petal-resident directional stress, not a scalar (§Petal data).
- Stress propagates via flux records crossing edges (§Flux records bullet 4),
  not Jacobi on a cohesion graph.
- Ratchet trigger is the f32 sustained-overpressure integrator threshold,
  not i8 saturation.
- Fragmentation/yield events fire from inside the region kernel for solid-
  phase transport rules (§Region kernels paragraph "Solid: non-opportunistic
  for mass transport... Move-before-compress priority: when yield is
  exceeded, first check for fluid/gas neighbor to displace into (brittle/
  spalling); if none, compress and harden (ductile)").
The example narratives (stalactite holding itself up, rock bouncing) are
gen5-relevant in *outcome* but the path-through-physics differs.
- File: `C:\projects\VerdantSim\wiki\elastic-flow.md`

### `wiki/phase-transitions.md` — RETIRE
Stage 1 sub-phases (ratchet-check / phase-resolve / Curie / latent-heat /
precipitation) are wiki-specific. gen5 puts phase transitions and ratcheting
inside the per-cycle region kernels with 2D phase-diagram lookups; latent
heat is part of flux records at transition events. The "deltas emitted, not
state written" discipline (lines 131–139) is gen5-aligned, but the rest
retires.
- File: `C:\projects\VerdantSim\wiki\phase-transitions.md`

### `wiki/precipitation.md` — RETIRE
Documents the wiki precipitation algorithm: per-cell solubility table
lookup + deposit/dissolve via composition deltas. gen5 has precipitation
**emerge** from cohesion + phase-diagram + diffusion with no special sub-
stage (verdant_sim_design.md §Cross-phase dynamics > Precipitation, §Cohesion
> "Precipitation and crystal growth"). No solubility table needed. The
emergent-behaviors table (lines 105–117) is gen5-relevant; the algorithm
isn't.
- File: `C:\projects\VerdantSim\wiki\precipitation.md`

### `wiki/walls.md` — REUSE WITH ADAPTATION
Wall-as-real-cell concept survives gen5 (everything has a per-channel
border behavior). The flag-combo recipes (sealed-insulated, sealed-radiative,
fixed-T, fixed-flux, reflective, absorbing) all exist in gen5's border
properties table (verdant_sim_design.md §Borders > Per-channel configurable
behavior bullets — gen5 lists the same eight behaviors plus a few). The
mechanism changes from "set flags directly on cell" to "set border_type
index on cell, lookup in border properties table at first contact and
cache in petal topology metadata" (§Border properties table, §Topology
caching in petal metadata).

What carries forward: the conceptual recipes, the example scenarios
(crucible with insulated sides + hot bottom + radiative top), the rendering
suggestions, the invariants section.

What changes: wall = flag-combo replaced by wall = border-type-table-entry.

- File: `C:\projects\VerdantSim\wiki\walls.md`

### `wiki/gravity.md` — RETIRE
Wiki Φ via Poisson Jacobi from density distribution is replaced by gen5's
border-seeded gravity vector field (verdant_sim_design.md §Gravity as a
first-class diffused vector field). Different solver (vector Jacobi diffusion
from frozen border + active mass perturbation, not Poisson on density), different
representation (per-cell vector, not per-cell scalar), different setup
(point sources + border seeding, not just G_sim multiplier), different
runtime cost (concurrent sub-pass alongside other fields, not pre-stage
scratch).
- File: `C:\projects\VerdantSim\wiki\gravity.md`

### `wiki/magnetism.md` — RETIRE (or shelve)
Magnetism is future work in gen5 (§Sorting ruleset last paragraph "Magnetic
sorting for ferromagnetic-composition scenarios is a future extension"). The
wiki page is detailed but academic until gen5 reaches that extension. Either
retire or move to a "future work" sub-folder of gen5 docs.
- File: `C:\projects\VerdantSim\wiki\magnetism.md`

### `wiki/element-table.md` — REUSE WITH ADAPTATION
Required-columns documentation. The TSV philosophy ("Why TSV"), the tier
ladder concept (Si → +H,O → +C,Fe → +N → +Al,K,Ca,Mg,Na), the sourcing
convention, the validation list, and the hash integrity discipline all
survive verbatim. Specific column lists need gen5 updates per the
`element_table.py` adaptation above. Compound aliases section (lines 116–129)
survives in concept; gen5 keeps compound-init expansion.
- File: `C:\projects\VerdantSim\wiki\element-table.md`
- 80% reusable; column list section needs adaptation.

### `wiki/debug-harness.md` — REUSE WITH ADAPTATION
Documents the schema-v1 + viewer + verifier + diff_ticks harness. gen5
keeps the harness shape (emit JSON at each cycle, view + verify + diff)
with schema bumped to v2 and field set updated.

What carries forward: the three-artifact pattern, exit-code conventions,
emission-granularity options (off/frame/stage/cycle/violation), the
debug workflow example (run → verify → view → diff), the
cross-validation Python↔CUDA discussion.

What needs gen5 update: schema field list (sections "JSON schema summary"
lines 16–34, "What emission carries" lines 121–143), invariant list
("Current invariant checks" lines 53–61), the wiki-specific failure-mode
discussion (mass-conservation-tautology bug already fixed).

- File: `C:\projects\VerdantSim\wiki\debug-harness.md`
- 70% reusable; field/invariant sections update.

### `wiki/glossary.md` — PARTIAL
Term-by-term:
- **Survives:** Bottle, Cavitation (gen5 keeps cavitation as permissive),
  Cell struct (concept), Cohesion (concept; specific formula changes),
  Compound alias, Convection, Conduction, Convergence budget (3/5/7
  carry), Curie temperature, Delta buffer (concept; gen5 calls it flux
  scratch), Dissolution (emerges in gen5), dt, Elastic strain (replaced
  by petal stress; concept survives), Element table, Eulerian, Fickian
  diffusion (gen5 has it via gradient-driven diffusion in region kernels),
  Hex grid, Insulated, Invariant, Jacobi iteration (gen5 keeps Jacobi
  for gravity diffusion and per-phase passes — §Core principle "Jacobi
  diffusion over Gauss-Seidel"), Latent heat (concept survives,
  shedding-mechanism changes), Mohs (gen5 keeps), Plasma (gen5 keeps as
  fourth phase), Pressure dead-band (gen5 has phase density equilibrium
  center as the analog), Tail-at-scale culling (gen5 keeps), Verifier.
- **Retires:** Auction, Bidder, Bid-ignorant capacity check, CULLED flag,
  EXCLUDED flag, Chemical potential μ, Derived field (gen5 model differs),
  Derive stage, Mass flow Stage 3, Overflow cascade, P↔U coupling,
  Refund, Resolve stage.
- File: `C:\projects\VerdantSim\wiki\glossary.md`
- About 50/50; rebuilds as gen5-glossary.

---

## Cross-cutting observations

### What gen5 inherits cleanly
1. **Hex grid + axial coords + 6-neighbor lookup** — `reference_sim/grid.py`
   carries forward verbatim. The `NEIGHBOR_DELTAS` ordering is what gen5
   region kernels and flux records will index into.
2. **TSV-based element table with hashed reproducibility** — `element_table.py`
   loader infrastructure carries forward; column set adapts.
3. **Scenario as Python module with `build()` returning Scenario dataclass**
   — pattern carries forward; cell/world internals adapt.
4. **JSON schema-versioned emissions with totals + per-cell-state +
   self-reported-invariants + stage-timing** — emit.py orchestrator carries
   forward; cell shape adapts.
5. **Verifier with --baseline, exit codes, divergence detection,
   --json-report, filter flag** — verify.py harness carries forward;
   invariant set adapts.
6. **Per-field diff with rel-tol for floats, exact for ints, schema/scenario/
   cell-count compat checks** — diff_ticks.py carries forward; field list
   adapts.
7. **Regression driver subprocess pattern** — regression.py carries forward
   verbatim.
8. **Convergence budgets 3/5/7 per phase** — conceptually gen5-native.
9. **dt = 1/128 s** — gen5-native default.
10. **NIST-sourced SI units throughout** — gen5-native.

### What gen5 retires structurally
1. **μ-gradient mass flow + auction with bidders**. There is no μ in
   gen5. Mass flow is region-kernel computed flux records, blind-summed.
2. **Bidder-ignorant capacity check**. Gen5 explicitly forbids this
   pattern (§Core principle).
3. **Cohesion as bool graph stored as derived field**. Gen5 cohesion is
   per-cycle scalar damping computed inside the region kernel.
4. **Three-tier overflow cascade (cavitation → P↔U → refund + EXCLUDED)**.
   Gen5 has cavitation-permissive integration + encoding-boundary clamps
   only. No P↔U conversion. No refund. No EXCLUDED.
5. **i8 elastic_strain saturation as cross-tick ratchet sentinel**. Gen5
   uses f32 sustained-overpressure integrator.
6. **4-slot composition vector**. Gen5 uses 16 slots.
7. **Single-phase enum (PHASE_SOLID/LIQUID/GAS/PLASMA)**. Gen5 uses fractional
   phase distribution + per-phase mass content.
8. **Φ scalar gravitational potential via Poisson Jacobi from density**.
   Gen5 uses border-seeded gravity *vector* field via Jacobi diffusion.
9. **Stage 0/1/2/3/4/5 sequential decomposition**. Gen5 runs phase sub-
   passes concurrently within one cycle.
10. **Latent-heat shedding via single-target neighbor search**. Gen5 has
    latent heat as flux-record energy at phase-transition events.
11. **CULLED / RATCHETED / EXCLUDED transient flags**. Gen5 replaces with
    tier promotion (hot/warm/cold), integrator threshold, encoding clamp.

### Pre-rewrite work checklist
Before writing gen5 code:

1. Define gen5 cell SoA layout (`cell.py` rewrite). Pin: 16 composition
   slots, fractional phase distribution + per-phase mass, six petals each
   with directional stress + accumulated velocity + topology flags, gravity
   vector, sustained-overpressure integrator, log-encoded pressure
   relative to phase center, u16/f32 boundary encoding choices.

2. Define gen5 element table columns (extend `element_table.py` Element
   dataclass; update TSV header; document new columns in
   `data/element_table_sources.md`).

3. Define gen5 border properties table format (new `data/border_types.tsv`
   or Python module). Map current flag preset recipes
   (PRESET_SEALED_INSULATED etc.) to entries in the new table.

4. Define gen5 gravity-source format (Python dataclass, lives in
   WorldConfig).

5. Define gen5 schema-v2 JSON shape. Emit version 2 from new emit.py.
   Update verify.py invariant set, diff_ticks.py field list, and
   test_diff_ticks.py fixtures concurrently.

6. Re-author Tier 0 scenarios in gen5 state shape: t0_static (uniform Si
   solid), t0_compression (single-cell petal stress spike), t0_ratchet
   (sustained-overpressure threshold), t0_fracture (opposing petal stress
   beyond tensile), t0_radiate (radiative ring border).

7. Implement gen5 Stage 0 (gravity vector field setup + diffusion).

8. Implement gen5 cycle (concurrent per-phase sub-passes in region kernels).

9. Implement gen5 integration (flux summation + intra-cell transition
   application + canonical re-encoding).

10. Verify each gen5 scenario passes the gen5 verifier; record golden
    emissions; wire regression.py to drive gen5 scenarios.

### Estimated reuse percentage by file count

| Bucket | Count | Files |
|---|---|---|
| REUSE AS-IS | 4 | grid.py, regression.py, element_table_sources.md, scenarios/__init__.py |
| REUSE WITH ADAPTATION | 9 | element_table.py, scenario.py, emit.py, sim.py, t0_radiate.py, verify.py, diff_ticks.py, test_diff_ticks.py, element_table.tsv, dt-and-units.md, walls.md, debug-harness.md, element-table.md |
| PARTIAL | 7 | flags.py, t0_static.py, t0_compression.py, t0_ratchet.py, t0_fracture.py, energy-flow.md, glossary.md, convergence.md |
| RETIRE | most of the wiki + most of derive/resolve/propagate/reconcile + cell.py, archive/ |

Roughly: ~35% of LOC carries forward (grid + scaffolding + harness),
~40% needs adaptation (loaders, scenarios, schema, verifier checks),
~25% retires (the framework-internal physics).
