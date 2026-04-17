# Phase Transitions (Stage 1)

The only stage in the pipeline that changes a cell's phase, mohs_level, or magnetization. Also where latent-heat shedding and precipitation events are triggered. Produces *deltas* for downstream flow passes; doesn't directly write most state itself.

## Stage 1 responsibilities

In order within the stage:

1. **Phase resolve** — check each cell's `(P, U, T, composition)` against the phase diagram; trigger phase change if crossed.
2. **Latent-heat shedding** — for a cell undergoing phase change, shed the converted material to an adjacent fluid neighbor with the correct enthalpy.
3. **Ratchet check** — for solids that exceeded elastic_limit in compression during Stage 2 (deferred from last tick's Stage 2), increment mohs_level, dump compression work to energy.
4. **Curie demagnetization** — for ferromagnetic cells above Curie temperature, zero `magnetization`.
5. **Precipitation / dissolution** — for cells with composition exceeding / below solubility limits, shift composition per the solubility rules.

All of these queue outputs as deltas for Stages 3/4 to consume, not direct writes. This keeps overflow accounting unified in Stage 5.

## Phase resolve

Each phase has a region in `(P, U)` space defined by the material's phase diagram. For a single-element cell:

```
if composition is single element:
    look up phase_diagram[element]
    determine new_phase from (P, U) location
else:
    compute composition-weighted phase centers
    interpolate phase diagram across composition
```

Mixed composition uses weighted-average phase-transition points. For water (H+O) alone, transitions happen at pure H₂O points (273 K melt, 373 K boil at 1 atm). For salt water (H+O+Na+Cl), the melt point drops — standard eutectic behavior falls out automatically if the weighted average is computed correctly.

## Latent-heat shedding

When a cell transitions (say, solid Si → liquid Si because energy crossed the fusion threshold), the standard rule is:

```
energy_to_absorb = L_fusion × mass × fraction_converted
target_phase = liquid

find_fluid_neighbor(cell, target_phase):
    # Prefer same-phase neighbors that already have capacity
    for neighbor in 6 neighbors:
        if neighbor.phase == target_phase and has_capacity:
            return neighbor
    return None

if fluid_neighbor:
    # Shed the converted mass + its energy to that neighbor
    queue mass delta:   cell → neighbor, fraction_converted of each element
    queue energy delta: cell → neighbor, L_fusion × mass + per-element c_p × T
    cell retains original phase, loses equivalent mass and energy
else:
    # No fluid escape — cell itself converts in-place
    cell.phase = target_phase
    cell.mohs_level = 0 (non-solid)
    # Strain is zeroed by phase change to non-solid
```

**Why this avoids needing a "partial melt" state:**
- If shedding is possible, the donor cell never holds partial state — it stays fully solid (just with less mass) until all its mass has been converted-and-shed.
- If shedding isn't possible (encased ice cube in rock), the cell flips phase entirely. Now there's liquid water inside solid rock, with no room. Next tick, Stage 3 applies pressure; if cohesion of the rock fails, fracture opens a path. Everything cascades via existing rules.

See [`framework.md`](framework.md) "latent-heat via shedding" section.

## Ratchet check

Mohs ratcheting is the plastic deformation mechanism. Only applies to solids.

```
if cell.phase == solid:
    if last-tick Stage 2 recorded plastic overflow for this cell:
        cell.mohs_level = min(cell.mohs_level + 1, mohs_max[element])
        cell.flags.RATCHETED = true
        # Queue energy delta — compression work turns to heat
        compression_work = stored_plastic_strain × modulus × V
        queue energy delta to self: +compression_work
        # Strain is reset — plastic ratchet "took" the excess
        cell.elastic_strain = 0
```

The cell now has a higher Mohs level, which shifts its dead-band pressure center exponentially upward (solid pressure encoding is `mantissa × 8 × 1.5^(level-1)`). Further compression needs much more force; the cell can also "contain" much more pressure before ratcheting again. Real metamorphic geology.

At `mohs_level == mohs_max`, further compression triggers `FRACTURED` instead — the material has exceeded its maximum ratcheting capacity.

## Curie demagnetization

For cells with ferromagnetic composition:

```
if cell has ferromagnetic element in composition AND T(cell) > curie_K[element]:
    cell.magnetization = 0
```

Curie is a sharp threshold in the model. Below it, magnetization persists. Above it, zeroed.

Reverse direction (re-magnetization when cooling): handled in Stage 1 via hysteresis. If cell T drops below Curie and `B(cell) ≠ 0`, the cell acquires magnetization:

```
cell.magnetization = remanence_fraction[element] × susceptibility[element] × |B(cell)| × sign(B(cell))
```

See [`magnetism.md`](magnetism.md).

## Precipitation / dissolution

For each cell, check each element slot against solubility in the host phase:

```
host_phase = cell.phase
host_element = dominant element in composition

for each (element, fraction) in composition:
    if element == host_element: continue  # host doesn't precipitate from itself
    
    limit = solubility_table[host_phase][host_element][element]
    
    if fraction > limit:
        # Supersaturated — precipitate
        excess = fraction - limit
        # Shift excess to adjacent solid of same material, OR crystallize in-place
        handle_precipitation(cell, element, excess)
    
    elif fraction < limit AND adjacent_source_of(element) exists:
        # Under-saturated — dissolve some from neighbor
        intake = min(limit - fraction, neighbor_available)
        handle_dissolution(cell, element, intake)
```

See [`precipitation.md`](precipitation.md) for the full mechanism.

## Deltas emitted, not state written

Stage 1 doesn't directly mutate `cell.composition`, `cell.energy`, `cell.pressure_raw` in a way visible to the rest of this tick. Instead it queues:

- **Mass deltas** into Stage 3's scratch buffer (latent-heat shedding, precipitation, dissolution).
- **Energy deltas** into Stage 4's scratch buffer (latent heat, ratchet work).
- **Direct state writes for `mohs_level`, `phase`, `magnetization`, `flags`** — these are phase/integer state, not flow quantities, so they're safe to write inline.

This approach means Stage 5's overflow cascade sees Stage 1 outputs identically to how it sees Stage 3/4 outputs. Unified accounting.

## Invariants

- **Energy-conserving latent heat:** the energy transferred during phase change equals `L_phase × mass_converted`. Verifier can check totals before/after.
- **Mass-conserving shedding:** what leaves the donor cell enters the neighbor. No net loss.
- **Monotonic mohs_level within a tick:** can only increase, never decrease.
- **Curie consistency:** no ferromagnetic cell above Curie has non-zero magnetization.

## Scheduling within Stage 1

The sub-phases are largely independent per cell but have local couplings (ratcheting heats a cell, which may push its temperature across a Curie threshold; phase change may cross a Curie threshold; etc.). Order within Stage 1:

```
1. Ratchet check (Stage 2's deferred plastic overflow → mohs_level, energy delta)
2. Phase resolve (may trigger latent-heat shedding, queue mass+energy deltas)
3. Curie demagnetization (after any phase-change temperature update)
4. Precipitation / dissolution (composition-driven)
```

Each sub-phase is fully parallel across cells. Sub-phases are serial within Stage 1 because later sub-phases may depend on earlier ones' outputs.

## What Stage 1 doesn't do

- Does not update pressure. Pressure evolves through Stage 3/4/5.
- Does not update energy *except* via queued deltas.
- Does not move composition between cells — that's Stage 3 via the delta buffer.
- Does not propagate cohesion, strain, or any field requiring neighbor iteration — those are Stages 0b, 2, etc.

Stage 1 is the "phase + ratchet + magnetization + composition shift" state-change pass. Everything else is flow.
