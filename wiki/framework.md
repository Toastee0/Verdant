# Framework Overview

The model in one line: **cells don't move — properties move.**

## The Eulerian invariant

Cell positions are fixed forever. Nothing in the stored state has a position; a cell's index *is* its position. Every physical behavior the sim eventually exhibits — a falling rock, a growing stalactite, a pressure wave, convection, stratification — is the emergent pattern of one-cell-wide property flows on a static grid.

No cell ever changes place. A "falling iron block" is iron composition flowing downward while lighter composition flows upward through the same bonds; the cells stay fixed.

This invariant is the foundation. Everything else is a consequence.

## The three kinds of state

### Stored (persistent across ticks)

Per cell, ~16 bytes. See [`cell-struct.md`](cell-struct.md).

- `composition` — up to 4 `(element, fraction)` pairs summing to 255
- `phase` — solid / liquid / gas / plasma
- `pressure_raw` — u16, log-scale encoded
- `energy` — u16
- `mohs_level` — u4, solids only
- `elastic_strain` — i8, solids only
- `magnetization` — i8
- `flags` — u8 (see [`flags.md`](flags.md))

These are the conserved quantities plus a few state bits. Everything else is derived.

### Derived (recomputed each tick, never stored persistently)

See [`derived-fields.md`](derived-fields.md).

- `Φ(cell)` — gravitational potential, Poisson-solved from mass distribution. See [`gravity.md`](gravity.md).
- `T(cell)` — temperature, computed from (energy, composition, phase).
- `B(cell)` — magnetic field vector, Poisson-solved from magnetization distribution (if scenario enables magnetism). See [`magnetism.md`](magnetism.md).
- `μ(cell, element)` — chemical potential per element, composes pressure + Φ + concentration + solubility + cohesion-barrier + magnetic term. See [`mass-flow.md`](mass-flow.md).
- `cohesion[cell, direction]` — bool per bond, from composition match + phase. See [`cohesion.md`](cohesion.md).

These live in per-tick scratch buffers. Never emitted in normal JSON (debug overlay only). Cannot drift from truth because they're recomputed.

### Flows (transfers during a tick)

Three flow primitives, all with the same mathematical shape: Jacobi sweeps where each cell acts as a bidder based only on a local stencil.

- **Mass flow** (Stage 3) — elements move down μ gradient. See [`mass-flow.md`](mass-flow.md).
- **Energy flow** (Stage 4) — heat moves down T gradient. See [`energy-flow.md`](energy-flow.md).
- **Elastic flow** (Stage 2) — strain propagates through cohesion network. See [`elastic-flow.md`](elastic-flow.md).

## The tick pipeline

One tick = 1/128 s of sim time. See [`dt-and-units.md`](dt-and-units.md).

```
─── DERIVE (no state change) ────────────────────────
0a   Φ         gravitational potential     (Poisson Jacobi)
0b   cohesion  bond topology               (local pass)
0c   T         temperature from U          (local pass)
0d   B         magnetic field              (Poisson Jacobi, optional)
0e   μ         chemical potential → scratch buffer   (local pass)

─── RESOLVE (triggers, emits flow sources) ──────────
1    phase resolve, ratchet, Curie demag,
     latent-heat shedding, precipitation

─── PROPAGATE (three flow passes, Jacobi with per-phase convergence caps)
2    elastic strain  (solids, up to 7 sub-iterations)
3    mass (elements) (up to phase's cap; gas ≤3, liquid ≤5, solid ≤7)
4    energy          (up to phase's cap)

─── RECONCILE ────────────────────────────────────────
5a   apply deltas with overflow cascade (cavitation → P↔U → refund + EXCLUDED)
5b   apply any Tier 3 refunds
5c   clear scratch for next tick
6    emit JSON / invariant check (debug only)
```

See [`pipeline.md`](pipeline.md) for stage-by-stage detail.

## Why this shape

**Unification.** Every physical mechanism that looked distinct (buoyancy, diffusion, avalanche, precipitation, cohesion-supported stalactites) collapses into "Flow 1 with different terms in μ." One auction, one rule set.

**Conservation by construction.** Stored quantities conserve because the only way they change is through symmetric flows (`out_A = in_B`). Derived quantities can't drift from the stored truth because they're recomputed.

**No special cases.** Walls are cells with flag combos ([`walls.md`](walls.md)). Heat sinks are cells with `RADIATES`. "Wind" is gas cells with a pressure gradient from adjacent cells. Each of these is the same primitive.

**GPU-natural.** Every stage is a parallel sweep over cells (or cell pairs, for flows). No atomics beyond per-direction scatter-gather. Memory is read-mostly from stored state, write-into per-direction scratch buffers. Fits the RTX 3090 cleanly.

## What emerges for free

When the framework is right, each physical behavior falls out of one or a small combination of primitives with no special-case code:

| Behavior | Falls out of |
|---|---|
| Diffusion (ink in water) | μ concentration term |
| Stratification (dense sinks) | μ gravity term |
| Buoyancy (iron in water) | same μ gravity term, acting on different materials |
| Convection | coupled mass + energy flow |
| Sound through rock | elastic Jacobi sweep, N iterations = speed |
| Earthquake / shock | elastic bond break + energy release |
| Stalactite growth | precipitation (solubility) + cohesion |
| Cave formation | dissolution (same solubility reversed) |
| Atmospheric stratification | gas molar mass in μ gravity term |
| Heating-up metamorphic rock | ratchet + P↔U coupling |
| Adiabatic compression | P↔U coupling (same rule, different trigger) |
| Magnetite attraction | μ magnetic term |
| Curie point demagnetization | phase-resolve Stage 1 |

No new code per behavior. Just the right material data in the element table and the behavior appears.

## What doesn't fit this framework (future work)

- **Moving charges and electrodynamics.** Would need charge as another stored field and a Maxwell solver. Deferred.
- **Chemical reactions.** Currently composition changes only via mass flow and precipitation. True combustion, oxidation, etc. would need reaction rules in Stage 1. Deferred until a scenario demands it.
- **Fracture mechanics beyond simple tensile limits.** Real fracture is anisotropic and direction-dependent. Current design is scalar. Upgradable.
- **Anisotropic magnetization.** Scalar moment for now. Upgrade to 2D vector if scenarios require it.

Each of these is an additive extension, not a rework — the framework shape stays the same.
