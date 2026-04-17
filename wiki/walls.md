# Walls

Walls are **real cells**, not metadata. They're cells flagged with specific combinations of `NO_FLOW`, `INSULATED`, `RADIATES`, and `FIXED_STATE`. Everything about wall behavior follows from those flags; there's no separate wall mechanism.

## Why cells, not metadata

Earlier versions of the design proposed walls as a "neighbor oracle" — when a real cell reads a neighbor position outside the sim region, the oracle returns a fake wall neighbor. That was rejected in favor of real cells because:

1. **One mechanism.** Every neighbor read goes through the same cell struct path. No second code path for "am I near a boundary?"
2. **Visible in JSON.** Walls appear in every emission. The viewer renders them; `verify.py` checks them.
3. **Non-convex shapes for free.** An L-shaped lab, a beaker with a heater at the bottom and an open top — all just different flag patterns across different cells.
4. **Player-placed walls later** use the same mechanism. Glass panes, iron plates, pipes, whatever — flag-combinations.

## Common wall recipes

Each recipe is a flag combination:

### Sealed insulated box (sandbox wall)

`NO_FLOW + INSULATED + FIXED_STATE`

Perfect isolation. Nothing crosses, no heat conducts, state never changes.

Use for: bring-up scenarios, pure conservation tests, reference-simulator sandbox.

### Sealed radiative (open to space)

`NO_FLOW + RADIATES + FIXED_STATE`

Mass can't cross, heat radiates to `T_space`. Incoming solar (if configured) absorbed by cell.

Use for: planetary simulations, vacuum-exposed exterior surfaces, lunar scenarios.

### Fixed-T heat source/sink (conductive)

`NO_FLOW + FIXED_STATE`

Not insulated. Wall cell has scenario-held energy (and thus held T). Neighbors conduct to this T via Stage 4 normally. Heat flows in/out infinitely.

Use for: isothermal lab walls, heater elements, fixed-temperature baths.

### Fixed-T heat source (insulated sides, open top)

Composite: sides = `NO_FLOW + INSULATED + FIXED_STATE`, top = `NO_FLOW + RADIATES + FIXED_STATE`.

Practical: a crucible with open top radiating, insulated sides.

### Open-flux drain

`FIXED_STATE` alone.

Composition/energy held at ambient values. Flows across its boundaries update the neighbor cells but this cell's state never changes — effectively infinite reservoir.

Use for: open-system scenarios where mass/energy can leave and new mass can be injected at ambient conditions.

### Rigid non-thermal barrier

`NO_FLOW + INSULATED`

Mass and heat both blocked. `FIXED_STATE` NOT set — the wall cell can still change state if scenario physics somehow injects into it (rare). Most of the time this behaves like the fully sealed version, but if the scenario wants the wall itself to evolve (compressing under load, breaking), it can.

Use for: breakable barriers, movable rigid plates.

## How flags compose with flow passes

### Mass flow (Stage 3)

`NO_FLOW` on either end of a bond → no flow. `μ` gets ∞ contribution from the barrier.

`FIXED_STATE` absorbs any incoming mass into the void (or more practically: the mass delta targeting the FIXED_STATE cell is zeroed — nothing arrives, but also nothing leaves the source). Equivalent to infinite capacity at fixed state.

### Energy flow (Stage 4)

`INSULATED` on either end → no conduction across that bond. Convection is also zero (coupled to mass, which is zero).

`RADIATES` → per-tick energy loss to `T_space`, per-tick solar absorption if applicable.

`FIXED_STATE` → incoming energy deltas absorbed (cell state doesn't update). Effectively infinite thermal mass at held T.

### Phase resolve (Stage 1)

`FIXED_STATE` cells don't phase-resolve. Their phase and mohs are held.

### Elastic (Stage 2)

Wall cells generally aren't solid-with-composition the same as nearby real cells. Cohesion map treats them differently based on composition match. Usually walls have a "wall" sentinel composition that cohesion doesn't match on — so nothing bonds to the wall, rocks can rest against it but don't stick.

If a scenario wants rocks to bond to a fixed anchor (e.g., a rigid ceiling), set the anchor cells to the same composition as the rock but with `FIXED_STATE + NO_FLOW + INSULATED`. They'll be cohesively bonded but can't move.

## Scenario configuration

The scenario init code specifies which cells are walls and their flag combos. Examples:

```python
# All edge cells of the hex disc are radiative walls
for q, r in grid:
    if at_edge(q, r):
        cell.flags = NO_FLOW | RADIATES | FIXED_STATE
        cell.energy = energy_corresponding_to_T_space  # doesn't matter; RADIATES dominates

# A crucible: insulated sides, hot bottom, radiative top
scenario.init_cells(
    sides=NO_FLOW | INSULATED | FIXED_STATE,
    bottom=NO_FLOW | FIXED_STATE,         # conducts to held T = 2000 K
    bottom_energy=T_2000,
    top=NO_FLOW | RADIATES | FIXED_STATE,
)
```

This is more flexible than pre-defined wall "types." Scenarios compose whatever combos are needed.

## What to render walls as

In the viewer (`viewer/viewer.html`):
- `NO_FLOW + INSULATED` cells: dark gray, thick border.
- `RADIATES` cells: red-tinted border (heat-radiating).
- `FIXED_STATE` cells: padlock icon or hash pattern overlay.
- Combinations: overlay the treatments.

Walls don't display their composition/pressure/energy by default (scenario-held values aren't interesting to debug). Click-to-inspect shows their flag combo and held state values.

## Invariants

- **`NO_FLOW` means zero mass transfer across that boundary** — verifier checks.
- **`FIXED_STATE` means the cell's stored state never changes tick-to-tick** — easy to verify.
- **No cohesion bond involves a wall cell** (unless the wall has rock composition for anchoring purposes — rare and explicit).
- **Energy conservation accounts for RADIATES losses** — exiting energy equals `Σ ε σ T⁴ dt × area` across all radiative faces.

## What isn't a wall

- **`EXCLUDED` cells** are not walls. They're temporarily frozen due to numeric saturation, not deliberately boundary'd. Different flag (bit 7), different semantics.
- **`CULLED` cells** are not walls. They're unconverged, will retry next tick.
- **`FRACTURED` cells** are not walls. They're broken solids with lost cohesion.

All three of these reflect transient sim states, not authored boundary conditions. Walls are always `FIXED_STATE` (or at least NO_FLOW + INSULATED to behave wall-like).
