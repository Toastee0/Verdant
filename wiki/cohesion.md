# Cohesion

The topological property that makes solids into coherent objects. Without cohesion, any dense solid surrounded by lighter fluid would fall apart (buoyancy + diffusion). Cohesion is what holds a stalactite tip up, what lets a mountain stand, what keeps a rock from diffusing into water.

## The rule

Cohesion is **implicit**. No stored bond state. Two adjacent cells are cohesively bonded **this tick** iff:

1. Both cells have `phase == solid`, AND
2. Their compositions match (same dominant element, OR same composition signature — scenario-dependent), AND
3. Neither cell has `flags.FRACTURED` set

The map is recomputed fresh each tick in Stage 0b. No persistent bond storage.

## Why implicit

- **Zero stored state.** Nothing in the cell struct records bonds.
- **Automatic handling of re-contact.** If two rock fragments slide into contact via Stage 3 flow, they become cohesive next tick with no explicit "merge" code.
- **Automatic handling of separation.** If a cell fractures, bonds to it vanish next tick — no explicit "break apart" bookkeeping.

## Scratch buffer

```
cohesion[cell][direction ∈ 0..5]   // 1 bit per bond; stored as u8 with 6 bits used + 2 reserved
```

~250k bytes = 250 KB for a 250k-cell grid. Trivial.

## What cohesion does

**In Stage 2 (elastic flow):**
Cohesive bonds transmit stress. Non-cohesive bonds transmit nothing. A stalactite tip only holds up because cohesive bonds upward carry tensile force to the anchor.

**In Stage 3 (mass flow):**
Cohesion adds an ∞ barrier to the μ(cell, element) for each element that is part of a cohesively-bonded solid, *in the direction of cohesion*. Effect: intact rock doesn't diffuse, doesn't buoyant-swap, doesn't get dissolved.

```
cohesion_barrier(cell, element, direction) =
    if cohesion[cell][direction] AND element is part of self's composition:
        ∞    # can't flow this way
    else:
        0    # normal rules apply
```

Note: cohesion is element-specific in effect. A bonded solid Si cell still lets, e.g., trace H₂O seep through if H₂O isn't what the cohesion bond is made of. (Limit: the cell's composition slots only have 4 of them, so there's implicit capacity limits.)

**In Stage 1 (phase resolve):**
Cohesion doesn't directly affect phase resolution, but phase transitions break cohesion: if one of a pair of cohesive cells melts, the bond ceases to exist on the next tick's cohesion map.

## Composition matching — strict vs. loose

How tight is "same material"? Two options:

### Strict: same dominant element AND same composition signature

Two solid cells bond iff their full composition vectors match (same elements, similar fractions). Safe default. Doesn't accidentally bond iron blocks to stone floors.

**Trade-off:** bedrock composed of 4 slightly-different mineral cells won't bond — may need scenario-init to normalize compositions within a region.

### Loose: same dominant element only

Two solid cells bond iff `composition[0].element == other.composition[0].element`. Simpler. Iron stays iron, stone stays stone, but minor composition differences don't break cohesion.

**Trade-off:** an iron block falling onto a stone floor doesn't bond (different dominant elements), but two stone regions with slightly different mineral mixes will bond. Probably what we want for most scenarios.

### Tier-ladder recommendation

- **Tier 0–1** (Si only, Si + H₂O): strict or loose equivalent (only one element type anyway). No decision needed yet.
- **Tier 2+** (Fe, C introduced): adopt **loose** match. Iron-on-iron bonds, stone-on-stone bonds, iron-on-stone does not.
- **Tier 4+** (real geology with Al, K, Ca, Mg, Na): may need a **material-pair table** — some element pairs bond (Ca–Mg, like in dolomite), some don't (Fe–Si at low T). Defer until we need it.

## Edge cases

### Liquids and gases
No cohesion. Period. Fluid phases never bond.

### Fractured solids
Cohesion bonds involving a `FRACTURED` cell are zero. The cell is "broken" — it transmits no stress and allows mass to flow freely through its boundaries.

Un-fracture: if a fractured cell re-ratchets under compression (consolidation), or if Stage 1 decides to heal it, `FRACTURED` is cleared and next tick's cohesion map restores bonds. Policy for healing is material-dependent; start with "ratchet heals" and refine.

### `FIXED_STATE` cells
Walls don't form cohesion bonds with neighbors (different "composition" — walls are their own material, effectively). A solid cell adjacent to a wall bonds to its *other* neighbors normally but not to the wall.

This matters: rocks don't weld to the bottle. If you want to pin rocks to a wall, use a different material (same composition as the rock, `FIXED_STATE` not set but other flags appropriate for a fixed anchor).

### `EXCLUDED` cells
No cohesion to `EXCLUDED` cells. They're numerically frozen and shouldn't participate in structural load paths.

## How cohesion enables mechanical behaviors

| Behavior | Cohesion mechanism |
|---|---|
| Stalactite hangs | Tensile cohesion chain from tip to anchor |
| Stalagmite stands | Compressive cohesion chain from base to tip |
| Mountain supports itself | Compressive chain through bedrock |
| Arch or bridge | Lateral cohesive bonds carry horizontal load |
| Rock shatters under impact | Localized tensile/compressive overload → cohesion bonds break → `FRACTURED` cascade |
| Sandpile flows (pseudo-granular) | Each grain barely cohesive; small stress breaks bonds; avalanches like loose sand |
| Iron bar bends | Elastic strain under load; snaps back if below yield, ratchets if above |
| Block dropped on floor | Both blocks become cohesive next tick if same material |

All of these come from the one bond rule + Stage 2 propagation. No per-behavior code.

## Invariants

The verifier checks:

- Cohesion map is strictly derived from current cell state — no "sticky" bonds from prior ticks.
- Every cohesive bond is symmetric (A↔B implies B↔A).
- No cohesion involving liquid, gas, plasma, fractured, or fixed_state cells.

## Future extensions

- **Material-pair table for Tier 4+.** Sparse lookup of `bonds[element_A][element_B] → bool`. Geological accuracy for silicate rock and ore-stone interfaces.
- **Bond strength grade.** Currently bonds are binary (bonded or not). A scalar strength per bond (0..1) would let partially-damaged rock transmit reduced force. Complex; defer.
- **Sintering at high T.** Two adjacent solids of *different* materials might fuse under sustained high temperature. This needs Stage 1 to upgrade compositions over time. Long-horizon scenario feature.
