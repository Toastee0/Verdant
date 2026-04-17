# VerdantSim — Plan of Action

**As of:** 2026-04-16 (framework design session)
**Status:** Physics framework settled. Debug harness runs. Ready to build reference sim.

This document is the forward-looking roadmap. For the *design* (what the sim is), see `wiki/README.md`.

---

## Where we are

Completed:
- Staged Jacobi auction framework designed and agreed (see `wiki/pipeline.md`).
- Debug harness operational: `reference_sim/sim_stub.py` emits schema-v1 JSON, `checker/verify.py` verifies independently, `viewer/viewer.html` renders. Sample data validates (`sample_data/` — 4 PASS + 1 DIVERGENT).
- All major physics decisions resolved: cell struct, flags, flow primitives, overflow cascade, walls, gravity method, magnetism, precipitation, cohesion, elasticity, dt, convergence budgets.

Open from prior architecture (not yet closed):
- `reference_sim/sim_stub.py` is the *schema-reference*, not the real sim. It's three ticks of canned evolution. Real sim not yet written.
- `checker/verify.py` has a mass-conservation tautology (re-sums current state as the baseline). Needs baseline-from-tick-0 comparison.
- No `element_table.tsv` yet. Everything in the sim is implicit/hardcoded.
- `ARCHITECTURE.md` documents the *debug harness*, accurate for that. Physics framework now lives in `wiki/`.

---

## Milestones

### M1. Element table for Tier 0 (Si only)

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

### M2. Fix verify.py mass conservation

Current `infer_expected_mass()` sums the current cells' compositions to derive the expected mass — tautological, can't detect loss.

Fix: `verify.py` takes an optional `--baseline <tick_0.json>` flag. When present, expected masses are read from the baseline's totals, not re-inferred. Without it, the check is marked as "no baseline — cannot verify conservation" and emitted as a warning rather than a pass.

Deliverable: updated `checker/verify.py`, re-run existing samples, confirm the tick_99 violation is caught without the tautology.

**Blocks:** conservation validation for the real sim. Without this, the real sim can silently lose mass and we wouldn't know.

### M3. Reference sim v1 — Si-only, 91-cell

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

### M4. diff_ticks.py

Small utility: load two JSON emissions, cell-by-cell diff, report any field differences above tolerance. Used for (eventually) Python vs CUDA cross-validation, and right now for regression testing (run scenario → diff against golden emission → fail if anything changed).

Deliverable: `checker/diff_ticks.py`. ~50 lines.

**Blocks:** CUDA port (for cross-validation). Low priority until then, but cheap enough to write early.

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
