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

### Working

- **Scaffolding** — grid, cells, flags, scenario framework all complete.
- **Stage 0a** (gravity Φ) — full Poisson Jacobi solve. Skipped when
  `world.g_sim == 0`.
- **Stage 0b** (cohesion) — full implementation: same-dominant-element,
  both-solid, neither-fractured-nor-excluded.
- **Stage 0c** (temperature) — composition-weighted specific heat; correct
  for Tier 0 single-element scenarios.
- **Stage 0d** (magnetism) — scenario-gated no-op. Real Jacobi solve
  deferred until a scenario needs it.
- **Stage 0e** (μ) — pressure + gravity terms. Solubility and magnetic
  contributions are stubs (zero). Cohesion barrier is applied inline at
  Stage 3 bond-evaluation, not baked into μ itself.
- **Stage 5** (reconcile) — sums per-direction deltas, clamps into u8/u16/i8
  bounds. Tier 1 P↔U coupling and Tier 3 refund are TODO.
- **Stage 6** (emit) — full schema-v1 JSON output. Cross-checked against
  `checker/verify.py`.
- **t0_static scenario** — passes all invariant checks across 5+ ticks.
  Mass conserved exactly (Si=23205). Energy conserved exactly (27300 J).
  Zero deltas every tick as expected for uniform equilibrium.

### Not yet implemented

Each of these is marked with TODO comments where the skeleton lives:

- **Stage 1** phase resolve / ratchet / Curie / precipitation — clears
  per-tick transient flags but does no resolution work. Needed for any
  non-static scenario.
- **Stage 2** elastic strain propagation — returns 0 iterations always.
  Needed for `t0_ratchet`, `t0_fracture`.
- **Stage 3** mass flow — returns 0 iterations always. Needed for
  `t0_compression` and any flow-dependent scenario.
- **Stage 4** energy conduction / convection — radiation is in place but
  conduction is a placeholder. Needed for `t0_radiate` (interior needs
  to conduct toward the radiative boundary).

### Additional scenarios to add after the flow passes land

Per `../PLAN.md` M3:

- `t0_compression` — elevated-pressure cell redistributes (needs Stage 3)
- `t0_ratchet` — compression triggers Mohs ratchet (needs Stage 2 + 1)
- `t0_fracture` — tensile failure breaks a chain (needs Stage 2)
- `t0_radiate` — hot disc cools to boundary (needs Stage 4 conduction)

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
