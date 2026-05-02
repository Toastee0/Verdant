# reference_sim/

Python reference implementation of the VerdantSim physics engine. See
[`../wiki/framework.md`](../wiki/framework.md) for the model and
[`../wiki/pipeline.md`](../wiki/pipeline.md) for the stage-by-stage breakdown.

This is the **correctness oracle**. The CUDA port will emit the same
schema-v1 JSON; any divergence is a porting bug caught by `verify.py`.

## Module layout

```
reference_sim/
├── __init__.py
├── grid.py            # HexGrid, axial coords, 6-neighbor lookup
├── cell.py            # CellArrays (SoA storage, numpy-backed)
├── flags.py           # u8 flag bits + preset wall combos
├── element_table.py   # TSV loader, Element dataclass, encode/decode helpers
├── scenario.py        # Scenario + WorldConfig + EmissionConfig
├── derive.py          # Stage 0: Φ, cohesion, T, B, μ
├── resolve.py         # Stage 1: phase resolve, ratchet, Curie, precipitation
├── propagate.py       # Stages 2/3/4: elastic, mass, energy flows + radiation
├── reconcile.py       # Stage 5: apply deltas, overflow cascade
├── emit.py            # Stage 6: schema-v1 JSON writer
├── sim.py             # tick-loop orchestrator + CLI
├── scenarios/
│   └── t0_static.py   # minimal Tier 0 hello-world
└── archive/
    └── sim_stub.py    # original stub (superseded, historical)
```

## Running

```bash
python -m reference_sim.sim <scenario_module> [--ticks N] [--output DIR]

# Example:
python -m reference_sim.sim reference_sim.scenarios.t0_static \
    --ticks 5 --output runs/smoke
```

Verify with the external checker:

```bash
python checker/verify.py runs/smoke/tick_00005_post_stage_5.json \
    --baseline runs/smoke/tick_00000_initial.json
```

## Current implementation state

All Tier 0 stages implemented; five scenarios green against `checker/verify.py` and `checker/regression.py`.

### Working

- **Scaffolding** — grid, cells, flags, scenario framework all complete.
- **Stage 0a** (gravity Φ) — full Poisson Jacobi solve. Skipped when `world.g_sim == 0`.
- **Stage 0b** (cohesion) — same-dominant-element, both-solid, neither-fractured-nor-excluded.
- **Stage 0c** (temperature) — composition-weighted specific heat; correct for Tier 0 single-element scenarios.
- **Stage 0d** (magnetism) — scenario-gated no-op. Real Jacobi solve deferred until a scenario needs it.
- **Stage 0e** (μ) — pressure + gravity terms. Solubility and magnetic contributions are stubs (zero). Cohesion barrier applied inline at Stage 3 bond-evaluation.
- **Stage 1** (resolve) — ratchet check consumes the `elastic_strain == +127` cross-tick sentinel (mohs_level++, RATCHETED, compression work to energy, strain reset); phase resolve from composition-weighted melt/boil; Curie demag; latent-heat shedding queue; precipitation gate.
- **Stage 2** (propagate, elastic) — Jacobi sweep over cohesion graph; springback decay for cells with no cohesive support; per-bond tensile-failure detection on the loaded (pre-iteration) strain.
- **Stage 3** (propagate, mass auction) — μ-gradient bidding with bidder-ignorant capacity check on recipient slot headroom; cohesion barrier blocks the dominant element across cohesive bonds for intact solids; CULLED on no-eligible-path.
- **Stage 4** (propagate, energy) — Jacobi conduction over T gradient with min(κ) bond conductivity; INSULATED gating; convection coupling from Stage 3's mass deltas; radiation as before.
- **Stage 5** (reconcile) — Tier 2 P↔U coupling on fraction overshoot; Tier 3 refund routing + EXCLUDED on energy saturation.
- **Stage 6** (emit) — full schema-v1 JSON output. Cross-checked against `checker/verify.py`.

### Tier 0 scenarios (in `scenarios/`)

All pass `verify.py` invariants against tick-0 baseline AND `diff_ticks.py` against recorded golden:

- `t0_static` — uniform Mohs-5 Si solid disc; zero deltas every tick.
- `t0_compression` — center cell `elastic_strain=+60` disperses through cohesion graph (Stage 2).
- `t0_ratchet` — center cell `elastic_strain=+127` saturation sentinel; tick 1 ratchet (mohs 5→6, RATCHETED, compression work to energy).
- `t0_fracture` — opposing strains -127 and +120 produce bond stress > tensile_limit; both cells FRACTURED at tick 1.
- `t0_radiate` — Si-liquid disc at 2500 K, ring-5 cells RADIATES; total energy decreases monotonically by Stefan-Boltzmann.

### Tier 0 caveats (documented inline)

- Per-tick conduction/radiation deltas at default cell_size_m=0.01 and Si energy_scale=1.0 floor below u16 resolution for cool/moderate temperatures. `t0_radiate` works in Si liquid at 2500 K to clear the floor.
- Strain Jacobi averaging on a finite hex disc has a small boundary leak (i8 rounding + missing exterior neighbors). Mass / energy conservation holds; strain conservation is approximate.
- Compression-work raw value floored at 1 unit so the ratchet event is observable in u16. Tier 1+ scenarios will use realistic energy_scale.

## Design notes

- **SoA memory layout** matches what the CUDA port will use. Each cell
  field is its own numpy array of length N.
- **numpy int sizes** are the same as the C struct dtypes. Composition
  is int16 (not u8) to allow signed delta accumulation without overflow;
  re-clamped to [0, 255] at reconcile.
- **Scratch buffers** (derived fields, delta buffers) are allocated once
  per scenario and cleared at tick boundaries. No per-tick allocations.
- **Scenarios are Python**, not a DSL. Each scenario module exports
  `build()` returning a ready-to-run `Scenario`.
- **Emission granularity** is configurable per scenario. Default is
  per-tick; can go to per-stage or per-sub-iteration for deep debug.
