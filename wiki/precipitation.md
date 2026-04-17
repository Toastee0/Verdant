# Precipitation & Dissolution

Composition-driven phase changes at the boundary between a liquid and a solid surface. The mechanism that builds stalactites, stalagmites, mineral veins, salt flats — and the reverse that erodes them (dissolution). Fires in Stage 1 ([`phase-transitions.md`](phase-transitions.md)).

## The rule

Each liquid (or gas, for dissolved gases) has per-element **solubility limits** — how much of each dissolved element the host phase can hold. When composition exceeds the limit, excess **precipitates** (becomes solid at a boundary). When a liquid is near a solid it can dissolve and its composition is under the limit, it **dissolves** material from the solid.

## Solubility table

Sparse 3D lookup:

```
solubility[host_phase][host_element][dissolved_element] → max u8 fraction (0–255)
```

Example values (approximate, by order of magnitude):

| Host phase | Host | Dissolved | Limit (fraction out of 255) | Analog |
|---|---|---|---|---|
| liquid | H₂O | Si | 1 | ~6 mg/L silica |
| liquid | H₂O | Ca | 3 | ~400 mg/L calcium as Ca(HCO₃)₂ |
| liquid | H₂O | NaCl | 90 | ~360 g/L (saturated brine) |
| liquid | H₂O | Fe | 0 | effectively insoluble at room T |
| liquid | H₂O | CO₂ (gas) | 2 | ~1.5 g/L at STP |

Temperature-dependence is deferred — starting with constants at reference T. Most solubilities rise with T; real tables are 2D. If a scenario needs hot-springs behavior, upgrade to `solubility(host_phase, host_element, dissolved_element, T)`.

Defaults: any entry not explicitly listed → solubility = 0 (insoluble).

## Precipitation algorithm

Per cell in Stage 1:

```
host_element = dominant element in cell.composition
for each (element, fraction) in cell.composition:
    if element == host_element: continue
    
    limit = solubility_table[cell.phase][host_element][element]
    
    if fraction > limit:
        excess = fraction - limit
        deposit_on_adjacent_solid(cell, element, excess)
```

### Deposit rules

```
deposit_on_adjacent_solid(cell, element, excess):
    # Case 1: adjacent solid of same element — accrete
    same_element_solid = find_neighbor(cell, 
                                         phase=solid, 
                                         dominant_element=element)
    if same_element_solid:
        # Shift mass from cell to neighbor via Stage 3 delta
        queue composition_delta: cell.element -= excess, neighbor.element += excess
        # Re-normalize cell composition (fractions sum to 255)
        queue re-normalize delta on cell
        return
    
    # Case 2: no adjacent same-element solid — crystallize in place
    # The cell phase-transitions from liquid to solid, with `element` as dominant
    cell.phase = solid
    cell.mohs_level = 1  # fresh crystal, softest grade
    cell.elastic_strain = 0
    # Composition may need re-balancing; excess element becomes dominant, others drop
    queue composition rebalance delta
```

Case 2 is the **stalactite-tip extension** event: a hanging water cell with supersaturated Si converts entirely to solid Si when saturation is reached. The stalactite grew by one cell.

## Dissolution algorithm

Reverse direction:

```
for each (element, fraction) in cell.composition:
    if fraction < limit AND adjacent_solid_of(element) exists:
        available = adjacent_solid[element_fraction]
        intake = min(limit - fraction, available, precipitation_rate × dt)
        
        queue composition_delta: cell.element += intake, neighbor.element -= intake
        queue re-normalize both cells
```

Dissolution is typically rate-limited by the `precipitation_rate` constant (one per element or per material). Real dissolution is slow (limestone into slightly acidic water takes years to form a cave). Sim tunes this per scenario.

## Rate limiting

Real geological time is too slow for a real-time sim. Per-element `precipitation_rate` is a multiplier that lets scenarios run at "sim-acceptable" speeds:

```
actual_transfer_per_tick = min(raw_transfer, precipitation_rate × dt)
```

- **Real-time geology** (stalactite visibly growing over seconds): rate multiplier ~10⁸ above reality.
- **Player-scale timescales**: rate multiplier 1 (natural).
- **Accelerated erosion / weathering demos**: custom.

The multiplier lives in the element table and can be overridden per scenario.

## What this unlocks

Same mechanism, many emergent behaviors:

| Behavior | Mechanism |
|---|---|
| **Stalactite growth** | Evaporating water ceiling-drop → precipitate Si on ceiling |
| **Stalagmite growth** | Same, on floor |
| **Salt flats** | Evaporating brine → NaCl precipitates onto nearest surface |
| **Limestone cave formation** | CO₂-acidified water dissolves CaCO₃ rock — dissolution over time |
| **Mineral vein deposits** | Hot water (high solubility) cools → low-T solubility → ore precipitates in cracks |
| **Weathering of exposed stone** | Gentle rain dissolves surface minerals slowly |
| **Evaporite layers** | Ancient shallow sea evaporates → layered precipitate sequence (Ca → Mg → Na) |
| **Ice formation on surfaces** | H₂O precipitation from gas phase at cold surfaces |

All from `solubility_table + precipitation_rate`. No behavior-specific code.

## Interactions with other stages

### With evaporation (Stage 1 phase resolve)
Evaporation of a water cell is its own phase transition — it sheds H₂O gas to a neighbor (latent-heat shedding rule, see [`phase-transitions.md`](phase-transitions.md)). The remaining cell becomes more concentrated in its dissolved content. Over many ticks, it crosses the solubility limit.

This is the mechanism for "rain drop evaporates leaving salt on the rock." The evaporation mechanism and precipitation mechanism are separate; they interact through the composition vector.

### With mass flow (Stage 3)
All precipitation / dissolution events queue mass deltas that Stage 3 respects. No direct state mutation — goes through the unified delta + overflow machinery.

### With cohesion
Newly crystallized precipitate cells aren't immediately cohesive to everything (next tick's Stage 0b recomputes the map). But if they crystallize adjacent to same-material solid, they become cohesive next tick. Stalactite grows as a single cohesive solid.

### With temperature
Future: solubility depends on T. Stage 1 would look up `solubility_table(host, dissolved, T)` instead of a constant. This matters for realistic hot-spring ore deposition.

## Invariants

- **Mass conservation**: what precipitates out of a liquid enters an adjacent solid; total fraction accounting is preserved across the grid.
- **Composition normalization**: each cell's composition still sums to 255 after precipitation events.
- **No precipitation into EXCLUDED cells.**
- **No precipitation across NO_FLOW boundaries.**
- **Monotonic solubility check**: at convergence, no cell has `fraction > solubility` for any dissolved element (every supersaturation has been resolved or converted to a new solid).

## Scenarios that test this

| Scenario | What it exercises |
|---|---|
| `t1_drop_stalactite` | One cell liquid H₂O+Si at ceiling; evaporates; Si fraction rises; precipitation fires when saturated; cell converts to solid Si; next cell of water drips; stalactite extends |
| `t1_stalagmite` | Same but water drops from a height and accumulates on floor |
| `t1_dissolve_chalk` | Water touching CaCO₃ rock; Ca gradually enters water; rock shrinks |
| `t1_salt_flat` | Pool of brine on a flat surface with a radiative top; evaporation drives precipitation to the underlying stone |

Each is a canonical physics unit test for the precipitation mechanism.
