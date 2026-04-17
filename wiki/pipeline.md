# Pipeline

The order of operations within one tick. Four phases: **Derive → Resolve → Propagate → Reconcile**. Each stage has clearly defined inputs, outputs, and side effects.

## Why this order

Each phase has a purpose:

- **Derive** — compute everything that depends only on current stored state. No state changes. Pure fan-out of derived fields into scratch buffers.
- **Resolve** — the "state change" stage. Phase transitions, ratcheting, demagnetization, precipitation fire here. These emit deltas that propagate passes will distribute.
- **Propagate** — the three Jacobi flow passes (elastic, mass, energy). Each iterates to convergence within a per-phase budget.
- **Reconcile** — apply accumulated deltas, run overflow cascade, produce the new stored state.

Stages within a phase can have data dependencies, but phases are cleanly separated: Derive finishes before Resolve starts, etc. This matters for GPU scheduling and for the cross-validation with the Python reference.

## Full stage list

```
─── DERIVE ────────────────────────────────────────────────────────
0a   Φ — gravitational potential
0b   cohesion — bond topology
0c   T — temperature
0d   B — magnetic field (if scenario has magnetism)
0e   μ — chemical potentials → dense scratch buffer

─── RESOLVE ───────────────────────────────────────────────────────
1    phase resolve, ratchet, Curie demag, latent-heat shedding, precipitation

─── PROPAGATE ─────────────────────────────────────────────────────
(per-phase convergence budgets — see convergence.md)
2    elastic strain    (solids)
3    mass (elements)   (all phases)
4    energy            (all phases, includes radiation)

─── RECONCILE ─────────────────────────────────────────────────────
5a   apply deltas with overflow cascade (Tier 1/2/3)
5b   apply refunds (rare)
5c   clear scratch
6    emit JSON / invariant check (debug only)
```

## Stage 0a — gravitational potential Φ

**Inputs:** cell composition (for mass density ρ), cell phase (for density by phase).
**Output:** scratch buffer `Φ[cell]`, scalar per cell.

Solve Poisson equation `∇²Φ = 4πG_sim · ρ` via Jacobi iteration on the hex grid. Iteration converges in O(√N) sweeps for pure Jacobi; bring-up scales use pure Jacobi with a convergence threshold. At large scales may upgrade to multigrid or FFT-Poisson.

See [`gravity.md`](gravity.md) for the full treatment.

## Stage 0b — cohesion topology

**Inputs:** composition, phase, `FRACTURED` flag of each cell.
**Output:** scratch `cohesion[cell][direction]` — 6 bits per cell (or one bool per bond).

Rule: two neighbors are cohesively bonded iff both are solid, same dominant element in composition (or same composition signature), neither is `FRACTURED`. Implicit — recomputed fresh each tick; no stored bond state.

See [`cohesion.md`](cohesion.md).

## Stage 0c — temperature T

**Inputs:** energy, composition, phase.
**Output:** scratch `T[cell]`.

Per-cell local computation (no neighbor reads). Formula:
```
c_p(cell) = Σ fraction_i × specific_heat(element_i, phase)
T(cell) = energy / (mass × c_p(cell)) + T_phase_ref(phase, composition)
```

See [`derived-fields.md`](derived-fields.md).

## Stage 0d — magnetic field B

**Inputs:** `magnetization[cell]`.
**Output:** scratch `B[cell]` (2D vector).
**Skipped entirely** if `scenario.magnetism_enabled = false`.

Poisson-like Jacobi over the grid. Scenario flag skips this pass when no magnetic materials are present — saves compute.

See [`magnetism.md`](magnetism.md).

## Stage 0e — chemical potential μ

**Inputs:** pressure, Φ, B, composition, phase, solubility table, cohesion.
**Output:** scratch `μ[cell][element_slot]` — one scalar per element slot per cell.

Formula per (cell, element_slot):
```
μ = P
  + ρ_element × Φ                       (gravity)
  + f(concentration, solubility)         (Fickian + solubility)
  + cohesion_barrier                     (∞ if element is in a bonded solid to this direction)
  − m_element · B · n̂                    (magnetic, for ferromagnetic elements)
```

Dense scratch buffer: `μ` is computed once per cell per sub-iteration, written to a scratch buffer, read by Stage 3 bonds. See [`mass-flow.md`](mass-flow.md).

Cost: 250k cells × 4 slots × ~6 FLOPs = 6 MFLOP per sub-iteration. Trivial.

## Stage 1 — phase resolve

**Inputs:** pressure, energy, composition, phase, mohs_level, elastic_strain, magnetization, T (from 0c).
**Side effects:** may flip phase, may ratchet (+mohs_level, +energy, strain reset), may fire precipitation (emit mass-flow sources), may latent-heat-shed (emit mass-flow sources), may Curie-demag (zero magnetization).

This is the "state-change" stage. Every other propagate pass is conservative flow; Stage 1 is the only place where a cell's phase, mohs_level, or magnetization change.

Outputs to subsequent flow stages: **deltas to apply** (not direct state changes). Phase resolution produces:
- **Latent-heat sheds:** mass moving to a fluid neighbor with its enthalpy (feeds Stage 3).
- **Precipitation deposits:** mass flows within composition slots (feeds Stage 3).
- **Ratchet heat dumps:** energy injected into self (feeds Stage 4).

These are queued as flow sources, not direct writes, so Stage 5 reconciliation sees them the same way as flow-pass outputs. This keeps overflow accounting unified.

See [`phase-transitions.md`](phase-transitions.md), [`precipitation.md`](precipitation.md).

## Stage 2 — elastic strain propagation

**Inputs:** elastic_strain, cohesion (from 0b), applied forces (gravity · mass, contact stresses).
**Output:** updated `elastic_strain[cell]`, plastic-overflow events (ratchets), tensile-failure events (`FRACTURED` sets).
**Sub-iteration budget:** solid phase cap (default 7).

Jacobi sweep through the cohesion network. Each iteration:
```
for each solid cell:
    applied_force = gravity_weight + Σ stress from cohesive neighbors
    new_strain = clamp(applied_force / elastic_modulus, -limit, +limit)
    if |new_strain| at compression clamp AND still loading:
        plastic overflow → ratchet event (Stage 1 handles next tick? or inline? see below)
    if tensile strain > tensile_limit:
        bond break → FRACTURED
    else:
        write new strain
```

Iterations propagate strain at ~1 cell per iteration. Speed of sound in solid ≈ cells_per_iteration / dt × cell_size. The 7-iteration cap for solids is literally the sound-propagation budget per tick.

See [`elastic-flow.md`](elastic-flow.md).

## Stage 3 — mass flow

**Inputs:** composition, phase, μ (from 0e), per-phase convergence cap.
**Output:** per-direction composition deltas in scratch buffer.
**Sub-iteration budget:** phase-dependent (gas ≤3, liquid ≤5, solid ≤7).

The main auction. Each cell reads its 7-stencil (self + 6 neighbors), computes per-element excess above dead-band, distributes proportionally across downhill neighbors. See [`auction.md`](auction.md) for mechanics, [`mass-flow.md`](mass-flow.md) for the μ terms.

Sub-iteration structure: re-compute μ (Stage 0e re-run, localized) → sweep → accumulate deltas → check convergence → repeat.

## Stage 4 — energy flow

**Inputs:** energy, composition (for conductivity), T (from 0c), `INSULATED` and `RADIATES` flags.
**Output:** per-direction energy deltas.
**Sub-iteration budget:** phase-dependent (shares caps with Stage 3).

Three mechanisms in one pass:
- **Conduction:** between non-insulated neighbors, proportional to ΔT and thermal conductivity.
- **Convection:** when Stage 3 moves mass, that mass carries proportional energy (coupled via cell's current T).
- **Radiation:** `RADIATES` cells emit `ε σ T⁴ × dt × face_area` to scenario `T_space`; absorb incoming solar flux if configured.

See [`energy-flow.md`](energy-flow.md).

## Stage 5a — reconcile with overflow cascade

**Inputs:** stored state + all queued deltas from Stages 1, 2, 3, 4.
**Output:** new stored state; possibly refund deltas; possibly `EXCLUDED` sets.

For each cell:
```
proposed_P = current_P + Σ incoming P delta
proposed_U = current_U + Σ incoming U delta

# Tier 2: P→U coupling
if proposed_P > p_max:
    overshoot = proposed_P - p_max
    proposed_U += overshoot × thermodynamic_coupling
    proposed_P = p_max

if proposed_P < p_min AND proposed_U > 0:
    deficit = p_min - proposed_P
    proposed_U -= deficit × thermodynamic_coupling
    proposed_P = p_min

# Tier 3: refund on double saturation
if proposed_U > u_max:
    # Route excess mass/energy back to bidders proportionally
    scatter refund
    set flags.EXCLUDED
    hold at (p_max, u_max)
else:
    commit (proposed_P, proposed_U, new_composition, new_strain, ...)
```

See [`overflow.md`](overflow.md).

## Stage 5b — apply refunds

Rare path. For each cell with pending refund deltas, apply them. These cannot overflow (refunds restore mass that originally came from the source).

## Stage 5c — clear scratch

Zero out all per-direction delta buffers, refund buffers, μ scratch. Prepare for next tick.

## Stage 6 — emit / verify

Debug build only. Serialize full cell state + totals + invariant self-report to JSON. See [`debug-harness.md`](debug-harness.md).

In release/production builds, Stage 6 is a no-op or runs at a lower frequency (one tick in N).

## Cost per tick (rough)

At 250k cells on RTX 3090:

| Stage | Cost per tick | Notes |
|---|---|---|
| 0a (Φ Poisson) | ~10 iterations × O(N) = 2.5M cell-ops | ~0.1 ms |
| 0b (cohesion) | O(N) | trivial |
| 0c (T) | O(N) | trivial |
| 0d (B) | same as 0a, optional | ~0.1 ms when active |
| 0e (μ) | O(N × 4 slots), per sub-iteration | ~0.05 ms × sub-iterations |
| 1 (phase resolve) | O(N) | ~0.1 ms |
| 2 (elastic, ≤7 iter) | 7 × O(N_solid) | ~0.3 ms |
| 3 (mass, ≤7 iter) | 7 × O(N) | ~0.5 ms |
| 4 (energy, ≤7 iter) | 7 × O(N) | ~0.5 ms |
| 5a/5b/5c (reconcile) | O(N) | ~0.1 ms |
| 6 (emit) | O(N) + JSON serialize | slow but debug-only |

Total ~2 ms per tick at 250k cells. Target is 128 Hz = 7.8 ms budget. ~4× headroom.

At 60 Hz display (~16 ms/frame), we have ~2 sim ticks per displayed frame. Emission every tick = 128 JSON files/sec at ~50 KB = 6 MB/sec. Plenty.
