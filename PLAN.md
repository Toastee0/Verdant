# VerdantSim — Plan of Action

**As of:** 2026-05-02 — M1–M4 complete. Reference sim runs all five Tier 0 scenarios with full conservation and a regression runner.

This document is the forward-looking roadmap. For the *design* (what the sim is), see `wiki/README.md`. For the code layout, see `reference_sim/README.md`.

---

## Where we are

Completed:
- Staged Jacobi auction framework designed and agreed (see `wiki/pipeline.md`).
- Debug harness operational: schema-v1 JSON contract shared by the reference sim, `checker/verify.py`, and `viewer/viewer.html`.
- All major physics decisions resolved: cell struct, flags, flow primitives, overflow cascade, walls, gravity method, magnetism, precipitation, cohesion, elasticity, dt, convergence budgets.
- **M1 done** — Tier 0 element table (Si only), NIST-sourced, SI units, power-of-2 encoding scales, validated loader (`reference_sim/element_table.py`).
- **M2 done** — `checker/verify.py` mass-conservation tautology fixed. New `--baseline` flag loads expected mass from an external tick-0 JSON. Incompatibility checks (run_id, element_table_hash, scenario, tick ordering) return exit code 4.
- **M3 done** — full reference sim runs all five Tier 0 scenarios end-to-end with perfect conservation. Stage 1 (ratchet sentinel + phase resolve + Curie + latent heat + precipitation), Stage 2 (cohesion-network Jacobi for elastic strain + per-bond fracture detection on the loaded state), Stage 3 (μ-gradient mass auction with bidder-ignorant capacity check + cohesion barrier on the dominant element of intact solids), Stage 4 (Jacobi conduction + convection coupling + Stefan-Boltzmann radiation), Stage 5 (Tier 2 P↔U coupling + Tier 3 refund/EXCLUDED). Five Tier 0 scenarios: `t0_static`, `t0_compression` (strain dispersion), `t0_ratchet` (Stage 1 sentinel handshake → mohs++ + compression-work heat), `t0_fracture` (opposing-strain bond stress > tensile_limit), `t0_radiate` (Si-liquid disc, ring-5 RADIATES boundary, monotonic cooling).
- **M4 done** — `checker/diff_ticks.py` cell-by-cell comparator with per-field tolerances (pressure_raw/phase/mohs/strain/flags exact; pressure_decoded/energy float-relative 1e-6; composition exact pairs). 8 self-tests in `checker/test_diff_ticks.py`. Regression runner `checker/regression.py` runs every t0 scenario, verifies invariants against tick-0 baseline, and diffs the chosen golden tick. Five `golden/<scenario>_tick_<N>.json` files recorded. Current status: 5/5 scenarios green.

---

## Milestones

### M1. Element table for Tier 0 (Si only) — **DONE**

Delivered: `data/element_table.tsv` (one Si row, 40 columns, SI units, NIST-cited), `data/element_table_sources.md` (per-field citations), `data/compounds.tsv` (stub for Tier 1+), `reference_sim/element_table.py` (loader + validator + encode/decode helpers).

Power-of-2 encoding scales for shift-decode on GPU. Mohs-1 ceiling (268 MPa) sits at 2.24× Si's elastic_limit — elastic regime has real headroom before pressure saturates.

*(Original spec preserved below for reference.)*



Author `data/element_table.tsv` with every field the framework requires, in SI units, sourced from NIST / CRC / Wikipedia reference values. Si-only at this stage.

Required columns (see `wiki/element-table.md` for full spec):
- Symbol, Z, name, molar_mass
- melt_K, boil_K, critical_T, critical_P
- density (per phase)
- specific_heat (per phase)
- thermal_conductivity (per phase)
- elastic_modulus, elastic_limit, tensile_limit (solid)
- mohs_max, mohs_multiplier
- thermodynamic_coupling (per phase)
- emissivity, albedo
- is_ferromagnetic, curie_K, susceptibility, remanence_fraction
- precipitation_rate (scenario-tunable multiplier)

Deliverable: one TSV row (Si), cross-checked against references in a companion note.

**Blocks:** everything downstream. Required before any physics code can be dimensionally correct.

### M2. Fix verify.py mass conservation — **DONE**

Delivered: `checker/verify.py` now takes `--baseline <tick_0.json>`. Expected mass comes from `baseline.totals.mass_by_element` (authoritative) with a fallback to summing baseline cells. Without baseline the check is marked SKIPPED (not silently passing). Compatibility guards: run_id, element_table_hash, scenario, tick ordering → exit 4.

Validated against existing samples: previously-DIVERGENT tick 99 now cleanly FAIL (exit 1); checker catches the 55-unit Si loss independently rather than requiring the sim's self-report to flag it.

*(Original spec preserved below for reference.)*



Current `infer_expected_mass()` sums the current cells' compositions to derive the expected mass — tautological, can't detect loss.

Fix: `verify.py` takes an optional `--baseline <tick_0.json>` flag. When present, expected masses are read from the baseline's totals, not re-inferred. Without it, the check is marked as "no baseline — cannot verify conservation" and emitted as a warning rather than a pass.

Deliverable: updated `checker/verify.py`, re-run existing samples, confirm the tick_99 violation is caught without the tautology.

**Blocks:** conservation validation for the real sim. Without this, the real sim can silently lose mass and we wouldn't know.

### M3. Reference sim v1 — Si-only, 91-cell — **DONE**

Delivered (commits `7b9a1e9` Step 1, `8223bdc` Step 2, `14c8a3f` Step 3, `2e8fdaf` Step 4, `1a04c76` Step 5, `01be4d4` Step 6 on `sim-core`):

- **Stage 1** (`resolve.py`): ratchet check consumes `elastic_strain == +127` cross-tick sentinel from Stage 2 → mohs_level++, RATCHETED flag, compression work ½σy²/E·V queued via SELF_CHANNEL energy delta. Phase resolve T-thresholded against composition-weighted melt/boil. Curie demag for ferromagnetic dominant element above curie_K. Latent-heat shedding queues ±L_phase·mass on transition. Precipitation gated on multi-element compositions (Tier 0 no-op).
- **Stage 2** (`propagate.py`): Jacobi sweep over the cohesion network averages strain across cohesive neighbors with springback decay (50%/iter) for cells with no cohesive support. Per-bond tensile-failure detection on the LOADED state (before smoothing) — bond stress = elastic_limit × |Δε_i8|/127; >tensile_limit ⇒ FRACTURED on both endpoints + strain release.
- **Stage 3** (`propagate.py`): μ-gradient mass auction with working-fraction snapshot (cells.composition untouched mid-loop). Bidder-ignorant capacity check caps each individual bid at recipient's slot headroom; multi-bidder overshoot allowed (cavitation). Cohesion barrier blocks dominant element across cohesive bonds for intact solids. NO_FLOW gates bonds.
- **Stage 4** (`propagate.py`): Jacobi conduction on T gradient with min(κ_self, κ_nbr) bond conductivity, INSULATED-gated, FIXED_STATE recipients drop credits. Convection coupling reads Stage 3's mass deltas and queues mass × c_p × T_source energy moves. Radiation already in place.
- **Stage 5** (`reconcile.py`): Tier 2 P↔U coupling converts fraction-overshoot (>255) to energy via composition-weighted phase-dependent thermodynamic_coupling. Tier 3 refund scatters unplaceable mass back to bidders proportional to incoming contribution; sets EXCLUDED on the saturated cell. FIXED_STATE cells exempt from updates.
- **Scenarios**: `t0_compression` (Stage 2 strain dispersion), `t0_ratchet` (Stage 1 sentinel → mohs++ + heat), `t0_fracture` (opposing -127/+120 strains exceed tensile via Stage 2), `t0_radiate` (Si-liquid disc with ring-5 RADIATES, monotonic Stefan-Boltzmann cooling). Each passes all six independent checks against a tick-0 baseline.

Tier 0 caveats documented in code: per-tick conduction/radiation deltas at default cell_size + Si energy_scale=1.0 floor below u16 resolution at low T (t0_radiate uses liquid Si at 2500 K to clear the floor); strain Jacobi averaging on a finite hex disc has small boundary leak; compression-work raw value floored at 1 unit for observability.

*(Original spec preserved below for reference.)*

*(Original spec preserved below for reference.)*



First real implementation of the framework. Python, numpy-backed, same JSON output as the stub.

Scope:
- 91-cell hex disc (same as existing sample scenarios)
- Si only (Tier 0)
- Full pipeline: Stages 0a–0e, 1, 2, 3, 4, 5a, 5b, 6
- All solids (no liquid/gas flows exercised yet, but code paths present and no-op'd)
- No magnetism (scenario flag disabled)

Test scenarios (each is a reproducible physics fixture — see `wiki/scenarios.md` when written):
- `t0_static` — 91 cells at equilibrium. Expected: zero deltas every tick. Conservation exact.
- `t0_compression` — one cell with elevated pressure. Expected: excess redistributes over multiple ticks, plateaus at new equilibrium.
- `t0_ratchet` — aggressive compression to trigger Mohs ratcheting. Expected: mohs_level increments, energy field rises by the compression work.
- `t0_fracture` — tensile load exceeds limit in a 1D chain. Expected: bond breaks at the weakest point, fragment below marked FRACTURED.
- `t0_radiate` — uniformly hot disc with radiative boundary. Expected: boundary cells cool via blackbody emission; interior cools via conduction toward boundary.

Deliverable: `reference_sim/sim.py` (the real one; `sim_stub.py` retires to `reference_sim/archive/`). Each scenario runs to completion and passes `verify.py` with a proper baseline.

**Blocks:** Tier 1+ work, CUDA port, anything that needs "the sim actually runs."

### M4. diff_ticks.py — **DONE**

Delivered (commit `135a4e2` on `sim-core`):

- `checker/diff_ticks.py` — cell-by-cell schema-v1 emission comparator. Per-field tolerances per the brief: pressure_raw / phase / mohs_level / elastic_strain / magnetization / flags exact match; pressure_decoded and energy float-relative 1e-6; composition exact (element, fraction) pairs. Compatibility checks (schema_version, element_table_hash, scenario, cell_count) return exit code 2. Diff exit code 1, identical 0.
- `checker/test_diff_ticks.py` — 8 self-tests covering identical, single-field diffs, float-tolerance gating, composition/flags exactness, and incompatibility guards. No pytest dependency; assertion-based.
- `checker/regression.py` — runs every t0_* scenario, verifies tick-N invariants against tick-0 baseline, then diffs the chosen golden tick. Failing diff or verify ⇒ non-zero exit. Currently 5/5 scenarios green.
- `golden/<scenario>_tick_<N>.json` — recorded emissions for regression diffing (t0_static@5, t0_compression@5, t0_ratchet@1, t0_fracture@1, t0_radiate@5).

*(Original spec preserved below for reference.)*

### M5. Tier 1 — + H₂O (H, O) compound

Add H, O rows to the element table. Add compound alias 200 → water = [(H, 114), (O, 141)]. Extend scenarios:
- `t1_ice_melt` — ice cube heated, liquid water sheds to gas-space above via latent-heat shedding. Expected: phase transition fires, mass flows to fluid neighbors, energy field records latent heat absorbed.
- `t1_boil` — liquid water at thermal boundary above boiling point. Expected: water sheds as vapor, pool recedes, vapor rises (via μ gravity term).
- `t1_precipitate` — water with dissolved Si exceeds solubility on a surface. Expected: first cell of a stalactite forms.

Deliverable: extended element table, extended reference sim, three new scenarios with golden emissions.

### M6. Tier 2 — + C, Fe

Mixing fixtures. Cast iron (Fe + C) with lower melt point than pure Fe — falls out automatically if the framework is right. First mixed-composition scenarios.

### M7. Tier 3 — + N (atmosphere)

Gas-phase stratification by molar mass. First "lots of gas cells" scenarios. First stress-test of the mass flow code paths at scale.

### M8. Tier 4 — + Al, K, Ca, Mg, Na (realistic silicate rock)

Real geology scenarios. Granite, basalt, weathering, metamorphism.

### M9. CUDA port

Only after reference sim is stable through Tier 3. Port emits identical JSON. `diff_ticks.py` cross-validates. Any divergence is a porting bug, localized by the diff to specific cells.

---

## Principles for the work ahead

Restated from session discussion for durable reference:

**Physics completeness first.** A scenario that runs but has a silent missing input (e.g. air pressure on water) is worse than no scenario. Before writing new scenario code, enumerate the physical inputs and cross-check against the cell struct.

**Doc and code stay synchronized.** The wiki describes what the sim is. The code implements it. When either changes, update the other. Drift between them is a bug in its own right.

**Iterative, with consensus.** No big unilateral restructuring. Propose → discuss → test → update. The design has been worked out carefully; changes should be as careful.

**Reference sim is the oracle.** Python correctness is the contract. CUDA perf is the goal. The JSON schema bridges them. `verify.py` checks both.

**"Properties move, cells don't."** The Eulerian invariant. Every physical behavior emerges from flows on a static grid. No cell ever has a position change.

---

## Open questions worth revisiting later

Not blocking, but worth thinking about:

1. **Non-radial gravity performance.** Poisson Jacobi converges in O(√N) iterations for pure Jacobi. At large scales (>100k cells) we may need multigrid or FFT-based Poisson. Defer until we see the pain.

2. **Magnetism anisotropy.** Starting with scalar magnetization. If scenarios demand it (north-pointing needles, magnetic shape anisotropy), upgrade to 2D vector.

3. **Composition vector capacity.** 4 slots covers ~95% of real materials. Seawater is 8+. When a scenario exceeds 4, do we drop the smallest, merge trace elements, or widen the struct?

4. **Grid shape for production.** Currently 91-cell hex disc for bring-up. Production grid size and shape intentionally not fixed. Revisit once Tier 3 is running and we know the physics cost per cell.

5. **Scenario DSL.** Currently scenarios are Python code. If the list grows past a dozen, a declarative scenario format (YAML/TOML) may be worth it.

6. **Determinism on GPU.** Jacobi sweeps on a GPU can produce slightly different floating-point results depending on reduction order. For cross-validation with the Python reference, we may need either integer-only arithmetic or deterministic reductions. Worth confirming before the CUDA port lands.
