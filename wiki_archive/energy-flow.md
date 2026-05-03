# Energy Flow (Stage 4)

Heat transport. Three mechanisms in one Jacobi pass: **conduction**, **convection**, **radiation**. All run the same auction-style parallel sweep; the differences are in the cost term and which cells participate.

## The rule

Energy flows down the gradient of **temperature T**, not energy. Two cells with equal energy but different composition can be at different temperatures — heat moves between them despite equal energy values.

```
if T(A) > T(B) and bond is open (not INSULATED):
    ΔU(A → B) ∝ thermal_conductivity × (T(A) − T(B)) × face_area × dt
```

Because T is derived (Stage 0c), not stored, this always reflects the current composition/phase of each cell.

## Sub-mechanisms

### Conduction

The default for adjacent cells with a T gradient. Per-bond cost:

```
κ_bond = min(κ(A, phase, composition), κ(B, phase, composition))   // series-like
ΔU(A → B) = κ_bond × (T(A) − T(B)) × face_area × dt
```

Using min() for the joined conductivity is a series-resistance approximation. The physically correct form is `2·κ_A·κ_B/(κ_A + κ_B)` (harmonic mean), which is also fine — pick whichever is simpler in your implementation.

Each bond with both endpoints not `INSULATED` contributes.

### Convection (coupled to Stage 3)

When Stage 3 moves mass across a bond, that mass carries its own thermal energy. Rather than a separate pass, Stage 4 reads Stage 3's delta buffer and computes the convective contribution:

```
for each (A → B, element, amount) in Stage 3's deltas:
    specific_heat = c_p(element, A.phase)
    energy_carried = amount × specific_heat × T(A)
    Stage 4 delta: -energy_carried from A, +energy_carried to B
```

This adds a convective ΔU to Stage 4's delta buffer. Stage 4 then runs its conduction sweep, both contributions accumulate.

**Why this order:** Stage 3 runs first (mass flow converges), then Stage 4 picks up the convective shadow plus independent conduction. Running them simultaneously would create cyclic dependencies (heat flow changes T changes μ changes mass flow …).

Between sub-iterations, the effective coupling is:
- Stage 3 sub-iter N: mass deltas — updates composition.
- Stage 4 sub-iter N: convection from that mass move + conduction → updates energy.
- Stage 0e + 0c before next sub-iter: μ and T both refresh.
- Stage 3 sub-iter N+1: sees updated T (via μ's implicit P dependency and via the mass it just moved), re-bids.

This sub-iteration ping-pong is exactly how you get **convection cells** to emerge — warm fluid rises, cools, sinks, warms again.

### Radiation

`RADIATES`-flagged cells emit blackbody thermal radiation. Once per tick (not per sub-iteration — radiation is a slow boundary loss, not a fast-transport mechanism):

```
for each cell with flags.RADIATES:
    ε = emissivity(composition)           // from element table
    σ = Stefan-Boltzmann constant
    P_rad = ε × σ × T⁴ × face_area × dt    // per radiating face
    Stage 4 delta: cell.energy -= P_rad × N_radiating_faces
    
    if scenario.solar_flux > 0:
        # Absorb incoming radiation on sun-facing faces
        albedo = albedo(composition)
        P_absorbed = solar_flux × (1 − albedo) × face_area × dt
        cell.energy += P_absorbed
```

`T_space` from scenario config is the "cold sink" at which the radiation is headed. Strictly, the net radiation is:
```
P_net = ε × σ × (T_cell⁴ − T_space⁴) × area × dt
```
Usually `T_cell ≫ T_space` so the `T_space⁴` term is negligible. But it matters for cold scenarios (lunar night, ice pack on deep time, etc.).

Radiative face: a face of a `RADIATES` cell that faces the grid exterior OR faces a `void` / `gas` cell with no RADIATES flag of its own (preventing double-counting at interfaces).

## Compute structure

```
Stage 4 per sub-iteration:
  1. Read Stage 3's delta buffer (mass deltas just produced)
  2. For each cell in parallel:
       for each direction:
         if bond not INSULATED and not walled:
             convective ΔU from Stage 3 delta at this bond
             conductive ΔU from current T gradient
         else:
             zero this direction
  3. Once per tick (not per sub-iter): for each RADIATES cell, apply P_rad.
  4. Scatter deltas to energy delta buffer.
```

Per-direction energy delta buffer: `energy_deltas[cell][direction]`, size 250k × 6 × 4 B (i32 for range) = 6 MB.

## Convergence

Same per-phase budgets as Stage 3 (gas ≤3, liquid ≤5, solid ≤7). Shares sub-iterations with Stage 3 — both converge together.

Threshold: `max |ΔU| / max |U|` across the phase's cells.

## Interactions

### With phase transitions (Stage 1)

Phase transitions consume or release energy (latent heat of fusion, vaporization, etc.). Stage 1 queues these as energy deltas:

- Ice → water: cell loses `L_fusion × mass` from its energy field.
- Water → vapor: cell loses `L_vaporization × mass`.
- Reverse directions: gains energy.

Stage 4 sees these as part of its delta pile and distributes normally.

### With ratcheting

Mohs ratchet dumps compression work to energy (Stage 1 / 5a handles this — see [`overflow.md`](overflow.md) Tier 2). Stage 4 distributes the resulting local temperature spike normally.

### With walls

- `INSULATED` flag: zero conduction across that boundary. Convective transport also zero (since mass can't cross either if paired with `NO_FLOW`).
- `FIXED_STATE` cells: their energy doesn't update. Act as heat reservoirs at held T.
- `RADIATES` cells: lose to `T_space` each tick.

## Edge cases

### Thermal underflow

If energy would go negative (cell has been over-drained by conduction + radiation), clamp at zero. This is equivalent to a cell reaching absolute zero — physically impossible to drain further. Not an error condition.

Rare but can happen in strongly radiative scenarios. Handled inline, no special flag.

### High-T relativistic regime

Well out of scope. At stellar core temperatures (10⁷ K), radiation dominates and T⁴ becomes enormous. If a scenario ever reaches this regime, u16 energy saturates and Tier 3 refund + `EXCLUDED` kicks in. Sim freezes that region gracefully.

## Invariants

The verifier checks:

- **Energy conservation** (with tolerance for radiative losses and ratchet gains). Total energy across grid at tick N = total at tick 0 + net ratchet gain − net radiative loss − net convective export (if open-flux boundaries exist).
- **No conduction across INSULATED bonds.**
- **No energy into FIXED_STATE cells.**
- **T monotonically approaches equilibrium** in isolated test scenarios (given enough ticks).

## Cost

Per sub-iteration at 250k cells:
- Read mass deltas (shared buffer, no new cost)
- Conduction: 250k × 6 bonds × ~5 FLOPs = 7.5 MFLOP
- Convection lookup: 250k × 6 bonds × ~3 FLOPs = 4.5 MFLOP
- Scatter: trivial
- Once-per-tick radiation: count × ~20 FLOPs; for a 250k grid with 2000 radiating faces, ~40 kFLOP

Total ~12 MFLOP per sub-iteration × 7 sub-iters = ~85 MFLOP per tick. Single-digit microseconds on the 3090.

## Future extensions

- **Variable specific heat with T.** Currently we assume c_p is constant per (element, phase). Real materials have `c_p(T)`. Upgrade path: replace scalar `c_p` with interpolated table.
- **Heat transport in plasma.** Plasma radiates strongly and has very high thermal conductivity. Plasma phase is deferred; when reached, Stage 4 gains a plasma-specific branch.
- **Convective boiling / droplet dynamics.** Currently all convection is bulk mass movement. Discrete-droplet effects (e.g., air bubble rising with surface tension) aren't modeled. Probably not needed at grid scale.
