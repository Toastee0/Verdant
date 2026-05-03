# Elastic Flow (Stage 2)

Stress propagation through the cohesion network. Acts on the solid phase. Produces **springback** (elastic deformation), **pressure waves / sound**, and detects both **plastic overflow** (→ ratchet) and **tensile failure** (→ fracture).

## The rule

Each solid cell has an `elastic_strain: i8` ([`cell-struct.md`](cell-struct.md)). Strain is signed: positive = compressed, negative = stretched. At rest = 0.

Within a tick, Stage 2 iterates (up to 7 sub-iterations for solids) a Jacobi sweep where strain propagates across cohesion bonds:

```
for each solid cell, in parallel:
    applied_force = gravity_weight(cell) + Σ stress_from_cohesive_neighbors
    new_strain    = clamp(applied_force / elastic_modulus, ±elastic_limit)

    if applied_force would exceed elastic_limit in compression AND loading:
        plastic overflow → ratchet event (excess compression → heat via P↔U coupling)
    if applied_force would exceed tensile_limit:
        bond break → flags.FRACTURED on both sides of the failing bond
    else:
        write new strain to scratch buffer
```

At the end of sub-iterations, strain state is committed to cell storage. Strain of a cell when there's no applied force decays toward zero — this is springback.

## Cohesion network

Stage 2 only flows across **cohesive bonds** (determined at Stage 0b — see [`cohesion.md`](cohesion.md)). Bonds between:
- two solids of the same dominant composition, both intact (not `FRACTURED`)

Non-cohesive bonds (fluid-solid, different-material solid-solid, fractured-anything) transmit zero stress.

This means a stalactite tip hanging in air: the tip's solid cell has cohesion upward (to the next stone cell), no cohesion sideways (air), no cohesion downward (air). Its gravity weight propagates as tension through the upward bond. The rest of the chain supports it.

## The three outcomes per cell per sub-iteration

### 1. Elastic (normal case)

`|applied_force / modulus| < elastic_limit`. Cell stores the strain; no plastic effects. When load lifts, strain decays back to zero. This is springback.

### 2. Plastic overflow (compression)

Compressive force would produce strain beyond `elastic_limit`. Cell clamps strain at `+elastic_limit`. Excess force converts to heat via the P↔U coupling rule ([`overflow.md`](overflow.md) Tier 2). If the accumulated plastic deformation crosses a ratchet threshold, `mohs_level++` and the `RATCHETED` flag fires ([`phase-transitions.md`](phase-transitions.md)).

### 3. Tensile failure

Tensile force would produce strain beyond `tensile_limit` (possibly same as elastic_limit, possibly a separate per-material constant — tensile strength is often less than compressive yield). `FRACTURED` flag on both cells of the failing bond. Cohesion across that bond is lost for all subsequent stages this tick and next.

## Pressure-wave propagation — sound in solids

Strain disturbances propagate through cohesion networks at approximately one cell per sub-iteration. Speed of propagation:

```
v_sound_sim = cell_size / (dt / sub_iterations_per_tick)
            = cell_size × sub_iterations_per_tick / dt
```

At `dt = 1/128 s`, 7 sub-iterations for solid, cell size 1 cm:
```
v_sim = 0.01 × 7 / (1/128) = 8.96 m/s
```

That's far below real sound speed in granite (~4000 m/s) by three orders of magnitude. The sim propagates "sound" much slower than reality — this is acceptable because most player-visible phenomena don't require accurate acoustic speeds.

Scenarios that care about real sound speed need either:
- Smaller cells (impractical — grid explodes)
- Much smaller dt (slows the whole sim)
- A dedicated fast-path for acoustic waves (future work)

For everything the sim is currently trying to model — stalactite support chains, avalanche triggers, fracture cascades, compression heating — the slower propagation is fine.

## Interaction with other stages

### With Stage 1 (phase resolve)
- If Stage 1 changes a cell's phase (e.g., solid melts), strain is zeroed (no strain in fluid).
- Ratchet events initiated here are implemented inline (mohs_level++ during Stage 2), but the compression work → heat is queued as a Stage 4 energy delta.

### With Stage 3 (mass flow)
- `FRACTURED` cells set by Stage 2 are immediately eligible as downward bidders in Stage 3 — avalanche begins the same tick.
- Intact solids have ∞ cohesion_barrier in their μ — they don't participate in Stage 3's composition flow for their own material. Other materials (e.g., fluid seeping in via fracture) do flow.

### With Stage 4 (energy flow)
- Ratchet compression work is dumped as a local energy increase. Stage 4 distributes it via normal conduction.
- No other direct coupling.

## Scratch buffers

```
strain_deltas[cell]              // i16, per sub-iteration, signed
stress_in_direction[cell][dir]   // i16, per-bond stress; used for tensile-failure detection
```

~250k × 2 B + 250k × 6 × 2 B = 3.5 MB. Cheap.

## Convergence

Max |Δstrain| / max |strain| across solid cells. Below threshold → converged early.

Solid phase cap = 7 sub-iterations (see [`convergence.md`](convergence.md)). If unconverged after 7, `CULLED` set on those cells.

## Example — stalactite holding itself up

Ceiling: column of 20 cohesively bonded solid-Si cells hanging downward.

Stage 0b: all 20 cells are bonded to their vertical neighbors (same material, all solid, none fractured).

Stage 2, sub-iteration 1:
- Tip cell (bottom): gravity_weight = ρ_Si × g × volume. Downward force, no cohesive support below (air). Applied force transmits as tension to the upward bond. strain = −(ρ_Si × g × V) / modulus. Small negative (stretched).
- Second-from-tip: its own weight + tension received from tip = 2× weight. strain ≈ −2×(weight/modulus).
- ... up the chain
- Ceiling anchor cell: weight of entire column. strain ≈ −20×(weight/modulus).

Sub-iteration 2: strain has propagated down the chain (cells further from the anchor "learn" about the tension).

After convergence: all cells have their equilibrium tensile strain. If anywhere along the chain `|strain| > tensile_limit`, that bond breaks and everything below drops (via Stage 3 next tick).

## Example — rock bouncing

A fractured rock hits the floor. Stage 3 moves its composition downward (avalanche). It collides with the floor — mass accumulates in one cell. Pressure rises. Via P↔U coupling, compression converts to heat; via Stage 2, the ceiling-ward bond carries strain outward to neighbors; those neighbors elastically compress.

After the collision, the strain pattern oscillates (sound wave) through the surrounding rock. As strain decays back to zero, the "stored bounce energy" has converted to either heat (via saturated ratchet) or kinetic strain-wave propagation that gradually attenuates.

Not a perfect bouncing ball, but a plausible pressure-wave shockwave through stone.

## Future extensions

- **Anisotropic strain.** Currently a scalar — treats strain as isotropic. For directional fracture or shear, upgrade to a 2D tensor (`i8 × 3`: εxx, εyy, εxy). Deferred until scenarios demand it.
- **Viscoelastic damping.** Strain currently decays instantly when load is removed. Real materials have finite relaxation times. Could add a damping coefficient per material (part of element table).
- **Fast acoustic path.** A separate high-speed propagation pass for pressure disturbances, running at higher effective propagation rate than 1 cell / sub-iteration. Complex; defer.
