# Convergence

How the Jacobi flow passes (Stages 2, 3, 4) iterate within a tick, and what happens when they don't fully settle.

## Per-phase iteration budgets

Each phase has its own maximum number of sub-iterations per tick:

| Phase | Max sub-iterations per tick |
|---|---|
| gas | 3 |
| liquid | 5 |
| solid | 7 |
| plasma | 5 (tentative; deferred) |

These are **upper bounds**. A pass that converges before the budget exits early.

## Why different phases, different budgets

Physical rationale:
- **Gas**: lightweight, diffuses quickly. Pressure equilibrates fast. Few iterations needed.
- **Liquid**: heavier, bulk flow. Medium iteration count.
- **Solid**: strain propagates through cohesion network; sound in rock is fast in reality, should propagate fast in sim too. More iterations = faster propagation.

The Dean & Barroso tail-latency discipline — don't wait on pressure-locked stragglers within a frame — is encoded here. Each phase gets a bounded time to settle; unsettled cells carry over.

## Convergence criterion

For each pass (mass, energy, elastic), compute max delta across the cells of that phase:

```
residual = max |Δstate| / max |state|
```

If `residual < convergence_threshold` → phase converged; exit early.

Default threshold: `1e-3` (relative). Tunable per scenario.

## What happens at budget exhaustion

When a pass exits without converging:

1. Cells whose delta magnitude exceeded threshold at the final sub-iteration get `flags.CULLED` set.
2. Their current state is committed (reconciled at Stage 5a).
3. No retry within this tick.
4. Next tick's Stage 1/2/3/4 will try again with the new state.

`CULLED` persists only through this tick's sub-iterations — it's cleared at the start of the next tick.

## CULLED vs. EXCLUDED

Both flags mean "this cell is not fully participating," but the regimes are different:

| Flag | Trigger | Persistence | Meaning |
|---|---|---|---|
| `CULLED` | Unconverged within sub-iteration budget | Cleared next tick | "Ran out of time this tick; try again next tick" |
| `EXCLUDED` | Numerical saturation (Tier 3 overflow) | Cleared when rejoin condition met | "Numerically broken; wait for neighborhood to calm" |

A cell can be `CULLED` and `EXCLUDED` simultaneously? No — `EXCLUDED` supersedes. If `EXCLUDED`, the cell doesn't participate at all and wouldn't cull.

See [`flags.md`](flags.md), [`overflow.md`](overflow.md).

## Inter-stage synchronization within a tick

The three propagate passes (Stage 2 elastic, Stage 3 mass, Stage 4 energy) can run in series or interleaved:

### Serial within tick (default)
```
for sub_iter in range(7):
    if solid_phase_has_strain_work:
        run Stage 2 sub-iteration
    if mass_work:
        run Stage 3 sub-iteration for all phases
    if energy_work:
        run Stage 4 sub-iteration for all phases
```

Each sub-iteration of each stage happens before the next. Convergence is checked per stage per sub-iteration.

### Interleaved (alternative)
Alternate Stage 3 and Stage 4 sub-iterations, because they're tightly coupled (convection couples mass moving to energy moving).

Default is serial. If profiling shows interleaved converges meaningfully faster, switch. The Python reference can switch between them by flag for cross-validation experiments.

## Per-stage convergence, not global

Each stage tracks its own convergence state:

- Stage 2 (elastic): converges for solid cells only.
- Stage 3 (mass): per-phase. Gas cells may converge at sub-iter 2 (within budget 3) while solid is still iterating.
- Stage 4 (energy): per-phase.

When a phase converges in its stage, that stage stops processing that phase's cells (for this sub-iteration). Other phases continue until their budget runs out.

## Budget exhaustion telemetry

Emitted in JSON:

```json
"totals": {
    "cells_culled": 12,           // cumulative this tick
    "phases_converged": {
        "gas": true,               // gas converged at sub-iter 2 out of 3
        "liquid": false,           // exhausted budget
        "solid": true              // converged at sub-iter 5 out of 7
    },
    "sub_iterations_used": {
        "stage_2_elastic": 5,
        "stage_3_mass": 5,
        "stage_4_energy": 5
    }
}
```

Persistent liquid budget exhaustion in a scenario → scenario is liquid-chaotic and may need more iterations, or physics is wrong.

## Future: adaptive budget

Scenarios with known low turbulence could run with tighter budgets (gas ≤1, liquid ≤2) for perf. Scenarios with extreme events might want temporary budget escalation (gas ≤8, solid ≤15 during the event window).

Could be a scenario-config knob:
```yaml
convergence_budgets:
  gas: 3
  liquid: 5
  solid: 7
```

Defaults live in the engine; scenarios override.

## Invariants

- A cell cannot be `CULLED` in a sub-iteration that it didn't participate in (e.g., a gas-phase-only sub-iteration shouldn't cull a solid cell).
- Convergence is monotonic within a pass — residual only ever decreases between sub-iterations (or the pass bails).
- Total sub-iterations per stage per tick ≤ phase's budget.
- Every `CULLED` flag is cleared before the next tick's Stage 1.
