# Verdant — Fluid Simulation Architecture (Definitive)

**Status:** Authoritative design document  
**Supersedes:** WATER_SIM_ARCH.md, WATER_PRESSURE_PLAN.md, PRESSURE_FIELD_MODEL.md, COLUMN_SCAN_PRESSURE.md  

---

## Foundational principle

**Air and water are both fluids. Soil and rock are not.**

The simulation does not distinguish between "water sim" and "air sim." There is one fluid simulation that handles all fluid-type cells. Air is a fluid with different density. Water is a fluid with different density. They share the same blob system, the same pressure model, the same interface transfer rules.

Solids (soil, rock) are inert. They don't participate in pressure. They form the walls of chambers. They settle under gravity (soil only — rock is fixed). A thin solid boundary can be breached by overpressure.

---

## Cell encoding

```c
typedef struct {
    uint8_t type;      // CELL_AIR / CELL_WATER / CELL_SOIL / CELL_ROCK / CELL_PLATFORM
    uint8_t amount;    // fluid density/amount: 0–255
                       //   AIR:   128 = 1atm resting. <128 = rarefied. >128 = compressed.
                       //   WATER: 0 = dry, 255 = saturated
                       //   SOIL/ROCK: unused (0)
    uint8_t temp;      // temperature: 0–255 (128 = ambient). Drives evap/condensation in pass 4.
    uint8_t vector;    // velocity: hi nibble = dx (-8..+7), lo nibble = dy (-8..+7)
                       //   Set during interface transfer. Decayed by gravity + drag.
                       //   0x00 = at rest (vast majority of cells).
} Cell;
```

**Fluid cells:** CELL_AIR, CELL_WATER. These get blobs, pressure, interface transfers.  
**Solid cells:** CELL_SOIL, CELL_ROCK, CELL_PLATFORM. These form boundaries. Soil settles. Rock is immovable.

---

## Pass 1 — Blob scan + pressure table

### 1a. Flood fill all fluid cells

BFS flood fill across the entire active region. Only fluid cells (AIR or WATER) participate. Solid cells are boundaries.

**Air blobs may contain solid objects up to 2px in size.** A 1px or 2px floating solid embedded in air does not split the blob — the air flows around it. This prevents micro-fragmentation of air regions from tiny debris.

**Chamber walls must be ≥ 3px thick** to register as blob boundaries. This is a world-design constraint, not a sim rule — the flood fill just follows adjacency. If a wall is 2px thick and the air blob rule above bridges 2px solids, the wall doesn't seal. 3px guarantees separation.

Implementation: during BFS, when a solid cell is encountered, check if there's a fluid cell within 2px on the other side that's already in the current blob. If yes, continue the flood through the small solid (treat it as fluid for blob membership, but don't give it pressure or flow). This handles 1–2px inclusions without breaking blob boundaries.

**Result:** `blob_id[idx]` for every fluid cell. `blobs[1..blob_count]` with volume, sealed flag, bounding box.

### 1b. Compute blob height + pressure lookup

For each blob:

```
blob.top_y    = min y of any cell in this blob
blob.bottom_y = max y of any cell in this blob
blob.height   = bottom_y - top_y + 1
```

**Pressure at any row in the blob** is a direct function of depth from the top:

```
pressure_at(y) = BASE_PRESSURE + (y - blob.top_y) * HYDROSTATIC_K
```

For open (unsealed) blobs: `BASE_PRESSURE = PRESSURE_ATM`  
For sealed blobs: `BASE_PRESSURE` is computed from Boyle's law on the gas pocket.

This is a lookup. Given a blob ID and a Y coordinate, you know the pressure. No per-cell storage. No iteration. O(1).

**Per-column refinement:** within a blob, different columns may have different fluid heights (water on one side, air on the other, within the same connected region). The pressure contribution depends on what fluid is above you:

```
For each column x in blob bounding box:
  Scan top to bottom within blob cells at this x.
  Track cumulative pressure:
    Each CELL_WATER above adds HYDRO_K_WATER per cell (water is dense)
    Each CELL_AIR above adds HYDRO_K_AIR per cell (air is light, nearly zero)
  Store: col_pressure[x][y] = cumulative pressure at this (x,y) within this blob
```

This is the **height index table**. For a 480-wide world it's 480 column scans per blob. Each scan is at most blob.height cells deep. For typical blobs (10–100 cells tall), this is trivial.

The table is scratch — recomputed every tick, not stored.

### 1c. Sealed blob gas pressure (Boyle's law)

When a blob is sealed (no world-boundary contact):

```
air_cells   = count of CELL_AIR in blob
water_cells = count of CELL_WATER in blob

if (blob just became sealed):
  blob.initial_air_vol = air_cells
  blob.initial_pressure = PRESSURE_ATM

// Boyle: P₁V₁ = P₂V₂
if (air_cells > 0):
  blob.gas_pressure = blob.initial_pressure * blob.initial_air_vol / air_cells
else:
  blob.gas_pressure = PRESSURE_VERY_HIGH  // fully flooded, incompressible
```

`gas_pressure` replaces `BASE_PRESSURE` in the pressure_at() lookup for sealed blobs.

---

## Pass 2a — Interface transfer with velocity

### Interface detection

Scan the active region. For each fluid cell, check its 4 neighbors. If a neighbor is a fluid cell in a **different blob**, that's an interface.

At each interface point:
```
P_here = col_pressure[this_blob][x][y]    // from height index table
P_there = col_pressure[other_blob][nx][ny]  // from other blob's table

deltaP = P_here - P_there
```

If `|deltaP| > PRESSURE_EPSILON`: transfer fluid from high pressure to low pressure.

### Transfer mechanics — pop from top with velocity

The fluid being transferred is **removed from the top of the source blob's column** at the interface x position. The fluid is **placed at the interface cell** on the receiving side.

**Velocity assignment:**

The fluid was at the top of the column. It's being pushed out at the interface point, which is lower (or to the side, or above — depends on geometry). The height delta between where it was popped and where it's being placed is stored energy.

```
pop_y       = surface of source blob at this column (topmost fluid cell)
interface_y = y of the interface cell
interface_x = x of the interface cell (may differ from pop column)

// Direction: from source cell toward receiving cell
dx_dir = sign(receiving_x - source_x)   // -1, 0, or +1
dy_dir = sign(receiving_y - source_y)   // -1, 0, or +1

// Magnitude: proportional to the pressure head that was released
// The height delta from pop point to interface IS the pressure head
height_delta = interface_y - pop_y      // positive = popping down, negative = popping up
velocity_magnitude = sqrt(abs(height_delta)) * VELOCITY_K   // Torricelli-ish

// Encode velocity in the flow direction
// Horizontal exit (pushing left or right):
//   vx = dx_dir * velocity_magnitude
//   vy = 0 (or slight upward if interface is above pop point)
// Vertical exit (pushing up through nozzle):
//   vx = 0
//   vy = -velocity_magnitude (upward)
// Diagonal: decompose naturally

cell[receiving_idx].vector = encode(vx, vy)
```

**What this gives you:**

- **Communicating vessels:** Gentle equalization. Pop point is near the interface. Small height delta → small velocity → water just settles across. Looks like calm leveling.

- **Fountain jet:** Tall column behind narrow nozzle. Pop from top (high up), interface at nozzle exit (low down). Huge height delta → large upward velocity on the exit water. Water shoots up, gravity decays the vector, parabolic arc, lands.

- **Lateral spray:** High-pressure blob behind a side opening. Pop from top, interface is to the side. Height delta gives the magnitude, direction is horizontal. Water sprays sideways.

- **Air bubbles:** Air blob trapped under water. Air pressure > water pressure at that depth. Interface transfer pushes air cells upward into the water blob. The air "bubble" has upward velocity because it's lighter than the surrounding water column. Air rises. Emergent buoyancy.

### Velocity decay (in CA pass)

Each tick, before the CA processes a cell with nonzero vector:

```
// Move water in vector direction
dx, dy = decode(cell.vector)
target = idx + dy * WORLD_W + dx

if target is fluid and has room:
  move water to target
  transfer vector to target (carry momentum)

// Gravity: increase dy by 1 each tick (pull down)
dy += 1

// Drag: reduce magnitude slightly each tick
dx = dx * DRAG   // e.g. 0.875 — your Solar Jetman drag constant, funny enough
dy = dy * DRAG

// Re-encode
cell.vector = encode(dx, dy)

// If magnitude < 1 in both axes: zero the vector, water is at rest
```

Water with a vector is "in flight." It moves ballistically until the vector decays to zero, then it becomes regular CA water — gravity + equalization only.

**This keeps velocity rare.** 99% of water cells have vector = 0x00. Only water that just exited a pressurized interface carries a vector, and only for a few ticks.

---

## Pass 2b — Soil pressure erosion

After fluid interface transfers, scan fluid-solid interfaces.

For each fluid cell adjacent to a CELL_SOIL cell:

```
fluid_pressure = col_pressure[blob][x][y]
soil_threshold = SOIL_RESIST_PRESSURE   // how much pressure soil can take

if fluid_pressure > soil_threshold:
  // Check if the soil is only 1 cell thick at this point
  // Look through the soil cell — is the other side fluid (air or water)?
  behind = cell on the opposite side of the soil from the fluid
  if behind is fluid (different blob or open air):
    // 1px thick soil under overpressure — breach it
    cells[soil_idx].type = CELL_AIR   // soil gets pushed away
    cells[soil_idx].amount = 0
    blob_mark_dirty(...)              // topology changed — re-flood-fill next tick
    
    // Optionally: spawn the soil as a falling particle nearby
    // (dirt was pushed out by water pressure)
```

**What this gives you:**

- Dam breaks: a 1px soil wall holding back a lake. Pressure exceeds threshold → wall breaches → water floods through.
- Aquifer puncture: digging close to a pressurized water pocket, leaving 1px of soil. Pressure pops it.
- Deliberate pressure engineering: player builds thin soil barriers as valves, knowing they'll blow at specific pressure.

**3px walls are safe.** Even at maximum pressure, only 1px soil can be breached. 2px soil holds. 3px is a reliable permanent wall (2px of safety margin).

The threshold is tunable. High threshold = soil is strong, only extreme pressure breaches it. Low threshold = soil is fragile, any significant water column pops 1px barriers. Gameplay feel knob.

---

## Pass 3 — Solids settle under gravity

After all fluid simulation:

```
tick_dirt(cells, bias)
```

Unchanged from current implementation. CELL_SOIL without FLAG_STICKY falls straight down, then diagonally. Bias-alternated scan prevents drift.

**Interaction with fluids:** When dirt falls into a water cell, the cells swap (existing behavior). The dirt sinks, the water rises. This should dirty the blob at the swap location since topology changed (water displaced upward, potentially into a different region).

When dirt settles onto a solid surface under water, it stays put. The water above it is part of the blob and has pressure. If the dirt is only 1px on top of air, and the water pressure above exceeds the soil threshold, the dirt gets pushed down (pass 2b triggers next tick).

---

## Pass 4 — Evaporation and condensation

Temperature-driven phase transfer between water and air.

### Evaporation

At any water-air interface (water cell adjacent to air cell):

```
if cell.temp > EVAP_THRESHOLD:
  // Transfer a small amount of water → increase humidity in adjacent air
  // "humidity" is tracked in the air cell's amount byte
  // (air amount > 128 = humid, < 128 = dry, 128 = neutral)
  water_cell.amount -= EVAP_RATE
  air_cell.amount   += EVAP_RATE   // above 128 = increasingly humid
```

Hot surfaces evaporate faster. Cold surfaces don't evaporate. This is a straight temperature lookup.

### Condensation

When humid air (amount > CONDENSATION_THRESHOLD) contacts a cold surface or cold air:

```
if air_cell.amount > CONDENSATION_THRESHOLD && air_cell.temp < CONDENSE_TEMP:
  // Humidity condenses into water droplets
  // Spawn a CELL_WATER at this location with small amount
  air_cell.type   = CELL_WATER
  air_cell.amount = condensed_amount
  air_cell.temp   = air_cell.temp   // inherits temperature
```

### Cave rain

The water cycle in a cave:
1. Warm zone at the bottom (geothermal, player equipment, whatever heat source).
2. Water at warm zone evaporates → humid air rises (hot air is buoyant — pressure is lower per cell at same depth for warm air).
3. Humid air reaches the cold ceiling.
4. Condensation on ceiling → water droplets form.
5. Droplets fall as rain.
6. Rain collects in pools at the bottom.
7. Cycle repeats.

No special rain system. No particle spawner. The temperature field + evap/condensation rules + fluid pressure + gravity produce rain as emergent behavior.

**This is pass 4 because it depends on all previous passes:**
- Blob pressure determines where humid air goes (rises toward low pressure).
- Temperature field determines where evaporation and condensation happen.
- Gravity (pass 3 / CA) determines where condensed droplets fall.

---

## Tick order (complete)

```
1. blob_fill()                 — flood fill all fluid cells → blob_id[], blobs[]
                                  (only re-run if any blob dirty; else skip)
2. blob_pressure()             — per-blob: compute height, gas_pressure (Boyle if sealed),
                                  per-column: build height index table (col_pressure[][])
3. blob_interface_transfer()   — scan interfaces between blobs:
                                  compare col_pressure at each interface point,
                                  pop from top of high-pressure blob,
                                  place at interface with velocity vector
4. soil_pressure_erode()       — scan fluid-solid interfaces:
                                  if fluid pressure > threshold and soil is 1px: breach
5. tick_dirt()                  — soil settles under gravity (sand-fall CA)
6. tick_water_ca()             — fluid CA: move vectored cells first (ballistic),
                                  then gravity, then equalization (settling only)
7. tick_evap_condense()        — temperature-driven phase transfer at fluid interfaces
8. render
```

---

## What we keep from current code

| Current | Fate |
|---------|------|
| `Cell` struct (type/water/temp/vector) | **Rename** `water` → `amount`. Semantics change: amount is fluid density for both air and water. |
| `blob.c` flood fill | **Keep + extend.** Add 2px solid bridging, bounding box tracking, gas pressure. |
| `blob_mark_dirty()` | **Keep.** Called on terrain changes, dirt/water swaps. |
| `water.c` pass 1 (gravity + equalization) | **Keep** as CA settling pass (step 6). Remove cross-blob concerns. |
| `water.c` pass 2 (upward pressure) | **Remove.** Replaced by blob interface transfer. |
| `dirt.c` tick_dirt | **Keep.** Add blob_mark_dirty on dirt/water swap. |
| `impact.c` all functions | **Keep.** Add blob_mark_dirty after terrain modification. |
| `terrain.c` | **Update** to place CELL_WATER with amount instead of relying on separate water array. |
| `render.c` water rendering | **Update** to read Cell.amount instead of separate water[]. |

---

## What's new

| New | Location |
|-----|----------|
| `blob_pressure()` — height index table, Boyle for sealed | `src/sim/blob.c` |
| `blob_interface_transfer()` — ΔP scan, pop-from-top, velocity | `src/sim/blob.c` |
| `soil_pressure_erode()` — 1px soil breach under overpressure | `src/sim/blob.c` or new `src/sim/erode.c` |
| `tick_evap_condense()` — temperature phase transfer | new `src/sim/evap.c` |
| Velocity handling in CA (move vectored cells, decay) | `src/sim/water.c` |
| Column pressure table (scratch buffer) | `src/sim/blob.c` static array |
| 2px solid bridging in flood fill | `src/sim/blob.c` fill_blob() |

---

## Implementation order

### Phase A — Foundation (get blob pressure working)

1. Rename `Cell.water` → `Cell.amount` across entire codebase
2. Extend `fill_blob()`: bounding box, 2px bridging for air
3. Implement `blob_pressure()`: column scan, height index table
4. Implement `blob_interface_transfer()`: ΔP detection at interfaces, basic transfer (no velocity yet)
5. Remove `tick_water()` pass 2 (upward pressure)
6. **Test:** Communicating vessels equalize. Sealed blob resists filling (Boyle).

### Phase B — Velocity

7. Add velocity encoding/decoding helpers for Cell.vector byte
8. Add velocity assignment in `blob_interface_transfer()` based on height delta
9. Add vectored-cell movement at start of `tick_water_ca()`
10. Add gravity + drag decay on vectors
11. **Test:** Fountain shoots up and arcs. Lateral spray from side openings.

### Phase C — Erosion

12. Implement `soil_pressure_erode()`: scan fluid-solid interfaces, breach 1px soil
13. Add `blob_mark_dirty()` calls on breach
14. **Test:** Dam break. Aquifer puncture. 3px walls hold.

### Phase D — Atmosphere

15. Implement `tick_evap_condense()`: evap at warm water-air interfaces, condense at cold surfaces
16. Add temperature sources (geothermal zones, equipment)
17. **Test:** Cave rain cycle. Humidity visible in debug overlay. Condensation forms droplets.

---

## Constants

```c
// Fluid types
#define CELL_AIR       0
#define CELL_WATER     1
#define CELL_SOIL      2
#define CELL_ROCK      3
#define CELL_PLATFORM  4
#define FLAG_STICKY    0x80

// Pressure
#define PRESSURE_ATM        1.0f    // baseline atmospheric
#define HYDRO_K_WATER       0.04f   // pressure per cell of water depth
#define HYDRO_K_AIR         0.001f  // pressure per cell of air depth (nearly zero)
#define PRESSURE_EPSILON    0.02f   // min ΔP to trigger transfer
#define TRANSFER_RATE       0.25f   // fraction of ΔP → flow per tick
#define MAX_TRANSFER        48      // max fluid moved per interface per tick
#define PRESSURE_VERY_HIGH  100.0f  // fully flooded sealed blob

// Velocity
#define VELOCITY_K          1.0f    // height delta → velocity scaling
#define GRAVITY_TICK        1       // added to vy each tick (pulls vectors down)
#define DRAG_FACTOR         0.875f  // velocity decay per tick per axis

// Soil erosion
#define SOIL_RESIST         3.0f    // pressure needed to breach 1px soil

// Blob
#define MAX_BLOBS           2048
#define BLOB_NONE           0
#define BLOB_BRIDGE_PX      2       // max solid thickness that air blobs bridge

// Evaporation (pass 4, deferred)
#define EVAP_THRESHOLD      160     // temp above which water evaporates
#define EVAP_RATE           1       // amount transferred per tick at interface
#define CONDENSE_THRESHOLD  180     // air humidity above which condensation occurs
#define CONDENSE_TEMP       100     // temp below which humid air condenses
```

---

## Debug overlay additions

When debug mode is active (backtick):
- Blob boundaries drawn as colored outlines (each blob a different hue)
- Sealed blobs highlighted (different outline style)
- Interface cells marked with arrows showing transfer direction
- Pressure value at cursor position (from height index table)
- Blob ID, volume, gas_pressure, sealed flag at cursor
- Velocity vectors drawn as lines from cells with nonzero vector
- Soil breach candidates highlighted (1px soil under overpressure)
