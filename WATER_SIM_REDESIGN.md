# Verdant — Water Sim Redesign Plan

**Status:** Design decision, not a bug fix.  
**Context:** The current binary cell water sim (cell = CELL_WATER or CELL_AIR) cannot
produce communicating vessels, U-tube pressure behavior, or flat equalized surfaces.
This is a fundamental architectural limit, not a fixable edge case. The replacement is
a continuous amount-per-cell model, as described in the GDD under **Fluid simulation**.

---

## What must change

### 1. Cell encoding

The current model stores water as a cell type:
```c
// BEFORE: binary — cell is water or air
CELL_TYPE(world[idx]) == CELL_WATER
```

The new model stores water as a **continuous amount alongside cell type**:
```c
// AFTER: continuous — every cell has a water amount 0..255
// The world array stores cell material type (stone, dirt, air)
// A parallel water[] array stores water amount per cell
uint8_t water[WORLD_W * WORLD_H];  // 0 = dry, 255 = fully saturated
```

`CELL_WATER` as a discrete type goes away. A cell is "water" when:
- its material type is `CELL_AIR` (open space), AND
- `water[idx] > THRESHOLD_WATER_MIN` (e.g. > 8)

Stone and dirt cells can also hold water amounts — this is how soil saturation and
mud states will work later. For now, only air cells participate in flow.

---

## 2. Sim rules (local only, no global solve)

All rules are applied per-cell, per-tick. No global pressure field. No iterative solver.

### Gravity (vertical fall)

```c
// If cell above has water and cell below has room:
int space = 255 - water[below];
int move  = MIN(water[here], space);
water[below] += move;
water[here]  -= move;
```

Water falls as much as will fit. Dense columns fall fast. Shallow puddles fall slow.

### Equalization (horizontal spread — ONI mass transfer)

Applied at every cell after gravity:

```c
int diff = (int)water[here] - (int)water[neighbor];
if (diff > 1) {
    uint8_t transfer = (uint8_t)(diff / 2);
    water[here]     -= transfer;
    water[neighbor] += transfer;
}
```

Run left neighbor then right neighbor (or use `bias` alternation as currently done).
This produces flat surfaces over multiple ticks. It also produces U-tube behavior
naturally — a tall saturated column on the left has more cells equalizing than the
right, net flow goes right, rises on the right. No explicit pressure needed.

### Pressure (DF-style upward push — saturated columns)

When a cell is fully saturated (water[here] == 255) and the cell above it is also
saturated, it searches orthogonally for a neighbor with room and pushes one unit:

```c
if (water[here] == 255 && water[above] == 255) {
    // search left and right for first cell with water < 255
    // push 1 unit toward it
}
```

This is what drives water up through a U-tube against gravity. It is the DF pressure
rule from the GDD. Diagonal gaps break the pressure chain — this is intentional and
player-facing (a diagonal notch is a valve).

---

## 3. Rendering

Replace the binary water tile check with an amount-based render:

```c
// Amount thresholds drive visual state
if (water[idx] == 0)        // dry — render cell material normally
if (water[idx] < 64)        // damp — render cell with moisture tint
if (water[idx] < 200)       // shallow — render partial water fill
if (water[idx] >= 200)      // full — render solid water color
```

The partial fill rendering (draw water from the bottom of the cell up proportionally
to amount / 255) is what gives the smooth surface appearance at pixel scale.

---

## 4. Terrain setup change

`terrain_generate()` must be updated to fill the left basin using the new water array
instead of placing `CELL_WATER` tiles:

```c
// BEFORE
FILL(lx, BY, BW, BH, CELL_WATER);

// AFTER — fill world[] with AIR, set water[] amounts
FILL(lx, BY, BW, BH, CELL_AIR);
for (int wy = BY; wy < BY + BH; wy++)
    for (int wx = lx; wx < lx + BW; wx++)
        water[wy * WORLD_W + wx] = 255;
```

`terrain_generate` signature becomes:
```c
void terrain_generate(uint8_t *world, uint8_t *water);
```

---

## 5. Interface changes summary

| File | Change |
|---|---|
| `src/sim/water.h` | `tick_water(world, water, bias)` — add water array param |
| `src/sim/water.c` | Full rewrite per rules above. Remove binary CELL_WATER logic. |
| `src/terrain.h` | `terrain_generate(world, water)` — add water param |
| `src/terrain.c` | Replace `FILL(..., CELL_WATER)` with water array fill |
| `src/render.c` | Replace binary water tile with amount-based partial fill render |
| `src/main.c` | Allocate `water[WORLD_W * WORLD_H]` alongside `world[]`, pass to all systems |
| `src/defs.h` | Remove or repurpose `CELL_WATER`. Add water amount thresholds. |

---

## 6. What this unlocks (in order)

1. **Flat surfaces** — equalization produces level water immediately
2. **Communicating vessels** — the basin demo works correctly
3. **U-tube / pressure** — water rises on the far side of a sealed column
4. **Soil saturation** — dirt cells accumulate water amounts → mud state
5. **Waterfalls** — high velocity downward flow (vector byte, later)
6. **Aquifer breaching** — sealed cistern POI releases large water volume
7. **Gas sim** — same architecture, humidity byte replaces water byte

---

## 7. What this does NOT change yet

- Temperature system — deferred
- Gas / vapor — same architecture, separate pass, deferred  
- Vector / velocity byte — deferred until waterfalls are needed
- Chunk active/dormant system — deferred

---

## Implementation order

1. Add `water[]` allocation in `main.c`
2. Update `terrain_generate` signature and basin fill
3. Rewrite `tick_water` — gravity first, equalization second, pressure third
4. Update render to use amount-based fill
5. Remove `CELL_WATER` from `defs.h` or demote to a render threshold alias
6. Test: left basin should drain rightward through bottom channel until both sides equalize

The test passes when the communicating basin demo produces a flat level surface
across both chambers with no mountain peak and no oscillation.
