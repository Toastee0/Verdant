# Gravity

Gravity is derived from the cell mass distribution each tick. Not stored. Computed in Stage 0a as a scalar field `Φ(cell)` — the gravitational potential — via **Poisson solve by Jacobi iteration**.

## Why Poisson, not a simpler model

Earlier in the design, two approaches were considered:

1. **Radial approximation.** Assume mass is (approximately) radially symmetric. Compute enclosed mass per ring (shell theorem), derive `Φ` as a 1D function of radius. O(N) cost, very cheap. But **fails on asymmetric mass distributions** — a dense meteorite off-center won't create a real gravity well, just a symmetric one centered wherever the sim's center is.

2. **Poisson solve.** `∇²Φ = 4πG_sim × ρ`. General — works for any mass layout. Another Jacobi sweep. Same architectural shape as every other flow pass; fits cleanly.

Decision: **Poisson**, so non-uniform scenarios (impactors, multi-body, arbitrary containers) work correctly.

## The equation

```
∇²Φ = 4πG_sim × ρ(cell)
```

Where:
- `∇²` is the discrete Laplacian on the hex grid (6-neighbor average minus self).
- `ρ(cell) = Σ fraction × density(element, phase) / 255 × volume_per_cell`.
- `G_sim` is a sim-scaled gravitational constant. **Not real G.** Tuned per scenario to produce visible stratification at the scenario's time scale.

## Discrete form on a hex grid

For a regular hex lattice with neighbor spacing `h`:

```
∇²Φ(c) ≈ (2/3h²) × [Σ Φ(neighbor_i) − 6 × Φ(c)]
```

The `2/3` factor is specific to hex geometry (vs. `1/h²` for square). Folded into the solver coefficients.

## Jacobi iteration

```
Φ_new[c] = (Σ Φ_old[neighbors] − 4πG_sim × ρ(c) × h² × 3/2) / 6
```

Iterate until `max |Φ_new − Φ_old| < tolerance`. For pure Jacobi:
- 91-cell disc: converges in ~10 iterations, ~1 ms.
- 250k cells: ~300-500 iterations at fixed tolerance, ~10 ms.

At large grids (100k+) this starts to dominate. Upgrade paths:

- **Red-black Gauss-Seidel**: checkerboard iteration, converges ~2× faster than Jacobi.
- **Multigrid V-cycle**: O(N) iterations for fixed residual. Standard technique for large Poisson solves.
- **FFT Poisson**: O(N log N), exact solution, but requires rectangular grid and periodic/known BCs. Probably overkill.

We start with pure Jacobi and upgrade only when profiling demands.

## Boundary conditions

At walls (`FIXED_STATE` cells) the Poisson solve needs a boundary condition. Typical choice: `Φ = 0` at the outer edge of the sim (equivalent to assuming no mass outside the bottle).

Alternative: `∇Φ · n̂ = 0` (Neumann BC, no gravity component perpendicular to wall) — reflects gravity inside the bottle. Useful for sealed containers where you don't want the wall to "leak" gravitational potential outward.

Scenario-configurable. Default: Dirichlet `Φ = 0` at bottle boundary.

## `G_sim` tuning

Real G ≈ 6.67 × 10⁻¹¹ N m²/kg². Real gravity on a 1 m cell of water is ~10⁻¹² N. Imperceptible at sim scales.

For interesting sim behavior (stratification, visible sinking) we need `G_sim` to be much larger than real. Tuning guide:

- Stratification visible in 100 ticks (~0.8 seconds sim time): `G_sim` on the order of 10² or 10³ real units.
- Strong "gravity well" behavior (core formation on a planet-scale sim in minutes): 10⁶ real units.
- Specific scenarios tune to taste.

Keep `G_sim` in the scenario config, not in the element table.

## How Φ plugs into physics

Φ contributes to chemical potential as `ρ_element × Φ(cell)` in [`mass-flow.md`](mass-flow.md). Higher Φ → element wants to leave (move downhill, toward lower Φ).

Also used in Stage 1 for the gravitational part of ratchet-induced stress (a column of rock has weight proportional to Φ differences up and down).

Also used in Stage 2 (elastic) as the gravitational body force per cell: `F_grav(cell) = ρ(cell) × (−∇Φ)`.

## Radially symmetric check (for debug/tests)

In scenarios with deliberately radially symmetric mass distributions, Φ should come out close to what the radial approximation would produce. Verifier can run a sanity check: compute `M_enclosed(r)` per ring from the mass distribution, compute radial-approximation Φ(r), compare to the Poisson-solved Φ averaged per ring. They should match to within a few percent for radial scenarios.

This is a useful smoke test for the Poisson implementation.

## Cost summary

At 250k cells, pure Jacobi, convergence tolerance `1e-4`:

- ~500 iterations × 250k cells × 5 FLOPs/cell = 625 MFLOP per tick
- Memory: 2× 250k × 4 B = 2 MB (read from Φ_old, write to Φ_new, swap)

On RTX 3090 (35 TFLOP, 936 GB/s): ~20 µs compute, ~2 µs bandwidth-bound. Stage 0a dominates compute within Stage 0 but is still fast.

If this becomes a bottleneck (very large grids), upgrade to red-black Gauss-Seidel first (~2× speedup, trivial change), then multigrid (~10× speedup, moderate implementation effort).

## What Φ doesn't do

- Not stored. Recomputed every tick.
- Not read by Stage 6 emission unless debug overlay is requested.
- Not a per-element thing — Φ is a single scalar field; `ρ_element` scaling happens at μ computation time.

## Future extensions

- **Non-hex grid support**: if the grid shape becomes non-regular (e.g., irregular meshing around interesting features), the Laplacian stencil changes. Outside current scope.
- **Time-varying G_sim**: a scenario might ramp G up or down over time (e.g., accretion simulation with increasing mass → effectively stronger gravity). Trivial scenario-config change.
- **Multiple G fields**: if the sim ever models separate gravitational contributions (e.g., simulating Earth's gravity and a moon's separately for a specific scenario), add multiple Φ scratch buffers. Not needed for anything planned.
