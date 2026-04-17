# Magnetism

Magnetic interactions between ferromagnetic cells. Modeled via one stored field (per-cell `magnetization`) and one derived field (`B`, the magnetic flux density, computed per tick from magnetization distribution).

Scenario-gated: Stage 0d is skipped entirely if no cell has nonzero magnetization or ferromagnetic elements. Keeps cost at zero for non-magnetic scenarios.

## Stored state

Per cell:
```
magnetization: i8    // signed scalar, −128..+127
                     // 0 = non-magnetized
                     // positive / negative sign = orientation
                     // magnitude scaled to saturation magnetization of the material
```

**Scalar, not vector.** Represents the signed amplitude of magnetization along an implicit axis (defined per scenario — typically the "vertical" axis if one exists). This is a simplification; real magnetization is vector-valued. Upgrade path: promote to `i8 × 2` for 2D vector if scenarios demand directional magnetism.

## Derived field — `B(cell)`

2D vector, per cell. Computed in Stage 0d by Poisson-like Jacobi solve from the magnetization distribution. Same computational shape as gravitational Φ — iterate until converged.

Simplification for scalar magnetization: `B` at each cell is computed by summing contributions from nearby magnetized cells, weighted by distance. For a rigorous treatment the B field derives from the vector potential `A` via `∇²A = −μ₀ J_M` and `B = ∇ × A`, but for scalar-sim-use the direct contribution-sum is close enough.

In practice at small grids, even a brute-force `O(N²)` per-cell sum is cheap:

```
for each cell c:
    B_x[c], B_y[c] = 0, 0
    for each source cell s with magnetization ≠ 0:
        dx, dy = s.position - c.position
        r = sqrt(dx² + dy²)
        if r < 1e-6: continue
        # 2D magnetic dipole field (scalar moment along implicit axis)
        B_x[c] += coeff * s.magnetization * (3 * (dx/r) * (axis · r_hat) - axis_x) / r³
        B_y[c] += coeff * s.magnetization * (3 * (dy/r) * (axis · r_hat) - axis_y) / r³
```

O(N²) is tolerable up to a few thousand magnetized cells. Beyond that, Poisson-Jacobi on `A` is the right approach.

## Element-table fields for magnetism

```
is_ferromagnetic: bool        // if false, cell's magnetization stays 0
curie_K: f32                  // temperature above which magnetization is erased
susceptibility: f32           // how strongly material magnetizes in applied field
remanence_fraction: f32       // fraction of induced M retained when field drops
```

Ferromagnetic elements in our tier ladder: Fe (Curie ~1043 K), Co (~1400 K), Ni (~630 K). Others are paramagnetic or diamagnetic (effects negligible for sim purposes).

## Effect on physics

### μ term (Stage 0e)

For ferromagnetic elements in a cell's composition, `μ` gains a magnetic contribution:

```
μ(cell, ferromag_element) += −m_element × (B(cell) · n̂_direction)
```

Lower μ in the direction of aligned B. Iron filings migrate up B-field gradients automatically. See [`mass-flow.md`](mass-flow.md).

Non-ferromagnetic elements have zero magnetic contribution to their μ.

### Curie demagnetization (Stage 1)

```
for each cell:
    if has_ferromagnetic_element AND T(cell) > curie_K[dominant_ferromag]:
        cell.magnetization = 0
```

Above Curie point, thermal agitation destroys alignment. Instant in the model (real Curie transitions have finite widths, but close enough).

### Re-magnetization with hysteresis (Stage 1)

When a cell cools back below Curie and there's an applied B, it reacquires magnetization:

```
if T(cell) < curie_K AND cell.magnetization == 0 AND |B(cell)| > threshold:
    cell.magnetization = clamp(
        remanence_fraction × susceptibility × |B| × sign(B),
        -127, 127
    )
```

Once magnetized, the field persists even if external B drops — that's the remanence / hysteresis behavior.

A full hysteresis curve (BH loop) is more nuanced than this — real ferromagnets have coercive forces, saturation, etc. Starting simple; upgrade when a scenario demands it.

## Example — magnetite deposit attracting iron

Scenario: a cluster of magnetite cells (Fe-rich solid, already magnetized), with loose Fe filings in surrounding water.

Stage 0d: Poisson-like solve produces B field extending from magnetite, falling off with distance.

Stage 0e: Fe filings in water cells see a μ gradient — Fe μ is lower near the magnetite (aligned B). 

Stage 3: Fe composition flows from water cells toward magnetite. Over ticks, Fe accumulates on magnetite surface, mass flowing through water.

At some point, the accreted Fe itself becomes magnetized (it's now in strong B, below Curie) and contributes to the field.

## What doesn't fit the current model

- **Moving charges producing B** (Ampère's law). The sim doesn't model electric charge as a stored field. Would need a new primitive: `charge: i8` + electric potential derived field + current flow. Deferred.
- **Induced eddy currents.** Changing B in a conductor induces current, which resists the change (Lenz's law). Requires the above. Deferred.
- **Ferrite / antiferromagnet ordering.** Multi-sublattice magnetic ordering needs vector magnetization. Deferred.
- **Superconductivity.** Needs electrodynamics + chemistry. Out of scope.

These can all be added as additive extensions without changing the framework shape. The scalar magnetization field and the `B` Poisson solve are the foundation.

## Invariants

- No cell above Curie has nonzero magnetization.
- Only cells with ferromagnetic elements can have nonzero magnetization.
- Total magnetic moment of the grid is conserved unless there's a boundary leak (no boundary magnetic-flux effects modeled currently — field lines simply end at the grid boundary, which is a known limitation).

## Cost

At 250k cells, with magnetism enabled and ~1000 magnetized cells:

- O(N × K) sum approach: 250k × 1000 = 2.5 × 10⁸ ops per tick, ~10 ms on 3090. Borderline.
- Poisson-Jacobi approach: like gravity, ~500 iterations at 250k = 10 ms. Similar.

If magnetism becomes a hot path, dedicated multipole or multigrid methods apply.

Most scenarios will have few or no magnetized cells. Scenario flag skips Stage 0d entirely. Default cost: zero.
