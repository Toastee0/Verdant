# Derived Fields

Per-cell fields that are **recomputed every tick from stored state**, never persisted across ticks. Live in scratch buffers during Stage 0 (Derive phase of the pipeline). Consumed by Stage 1 and the propagate stages.

Derived fields exist because storing them would let them drift from the conserved quantities they're derived from. Computing them fresh each tick guarantees consistency.

## The four derived fields

| Field | Symbol | Computed at | Depends on |
|---|---|---|---|
| Gravitational potential | `Φ(cell)` | Stage 0a | composition (→ ρ) |
| Cohesion bond topology | `cohesion[cell][dir]` | Stage 0b | composition, phase, flags |
| Temperature | `T(cell)` | Stage 0c | energy, composition, phase |
| Magnetic field | `B(cell)` | Stage 0d | magnetization |
| Chemical potential | `μ(cell, element)` | Stage 0e | P, Φ, B, composition, solubility, cohesion |

`μ` depends on the other four, so it runs last. Others have no mutual dependencies and could in principle run in parallel; we serialize them into 0a-0d for implementation clarity.

## `Φ(cell)` — gravitational potential

Scalar per cell, units ~ m²/s² (or sim-scaled equivalent).

Derived by **Poisson solve** via Jacobi iteration: `∇²Φ = 4πG_sim × ρ`, where `ρ` is the local mass density (computed from composition × per-element per-phase density).

Works for arbitrary mass distributions — radial symmetry not assumed. An off-center dense mass creates a real gravity anomaly; multiple centers of mass create multi-well geometry. See [`gravity.md`](gravity.md).

## Cohesion map

One bit per bond (6 bonds per cell). Bond exists iff both cells are solid, same composition signature, neither fractured. See [`cohesion.md`](cohesion.md).

Cost ~1 bit per bond × 6 bonds × N cells ≈ 250 KB at 250k cells. Trivial.

## `T(cell)` — temperature

Scalar per cell, units K. Single cell read, no neighbor access:

```
# Mass density and specific heat from composition
ρ = Σ fraction_i × density(element_i, phase) / 255
c_p = Σ fraction_i × specific_heat(element_i, phase) / 255

# Temperature from thermal energy
T(cell) = (energy - reference_energy(composition, phase)) / (mass × c_p)
       + T_phase_ref
```

`reference_energy` is the zero-point for energy accounting. Different phases have different zeros (latent heat offsets). The convention is: at standard temperature and standard phase, energy = 0. Energy added/removed from this zero is what we track.

Computed once per tick in Stage 0c. Read by Stage 1 (phase resolve, Curie check), Stage 4 (conduction).

## `B(cell)` — magnetic field

2D vector per cell, units T (or sim-scaled). Zero in scenarios without magnetism (flag-gated; Stage 0d skipped entirely).

Poisson-like Jacobi solve from magnetization distribution. See [`magnetism.md`](magnetism.md).

## `μ(cell, element)` — chemical potential

Scalar per (cell, element slot). 4 slots per cell.

Composed of contributions from every relevant term:

```
μ(cell, E) = P(cell)                              # pressure
          + ρ_E × Φ(cell)                          # gravity
          + concentration_term(cell, E)            # Fickian + solubility
          + cohesion_barrier(cell, E, direction)   # ∞ if bonded
          − m_E × B(cell) · n̂                      # magnetic (ferromag only)
```

Dense scratch buffer, ~4 MB at 250k cells. See [`mass-flow.md`](mass-flow.md).

## Why derived, not stored

### Temperature specifically

If both T and U were stored:
- Adding energy (via Stage 4) would have to update both.
- One forgotten update → T and U disagree → bugs.
- Composition changes (Stage 3) change effective specific heat → T would need re-deriving anyway.

Much simpler: store U, derive T as needed.

### Gravitational potential specifically

Φ is a *global* function of the mass distribution. Any mass movement changes Φ everywhere. Storing Φ and hoping to update it incrementally is error-prone; recomputing fresh is cleaner.

### Chemical potential specifically

μ depends on P + Φ + B + composition + cohesion + solubility, all of which change frequently. Between every sub-iteration of Stage 3, μ becomes stale. Recomputation is mandatory for convergence.

## Lifetime and storage

Scratch buffers live per-tick (or per-sub-iteration for μ). Cleared in Stage 5c:

```
Stage 5c:
    clear Φ, cohesion, T, B, μ scratch buffers
    clear per-direction delta buffers
    clear refund buffers
```

Next tick starts with fresh computations in Stage 0.

VRAM budget at 250k cells:
- Φ: 4 B × 250k = 1 MB
- cohesion: 1 B × 250k = 250 KB
- T: 4 B × 250k = 1 MB
- B: 8 B × 250k = 2 MB
- μ: 4 B × 4 slots × 250k = 4 MB

Total ~8 MB derived scratch. On 24 GB of VRAM, this is 0.03%.

## What's not a derived field

Things that might look derived but are actually stored:

- **Phase** — stored because Stage 1 resolves it based on (P, U); storing avoids recomputing the phase diagram lookup every neighbor read.
- **Mohs level** — stored because ratcheting is monotonic within a tick; a stored counter tracks history.
- **Elastic strain** — stored because it's the cell's *current state* under dynamic load; derived only in the sense that it equilibrates within Stage 2.
- **Magnetization** — stored because hysteresis matters (below-Curie, B=0 doesn't zero M; some remanence persists).

The distinction: stored things have memory between ticks (history matters), derived things are memoryless functions of current state.

## Invariants

- **Derived fields never appear in emitted JSON** except as debug overlay (optional).
- **Conserved quantities are stored**; derived quantities are computed. No exceptions.
- **Stage 0 has no side effects on stored state** — it only writes to scratch buffers.

This discipline is what makes the Python↔CUDA cross-validation work: both implementations compute derived fields the same way from the same stored state. Any difference is a bug in the port.
