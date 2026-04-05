# Verdant — Fluid Sim Handoff

**For:** Opus planning session  
**Date:** 2026-04-04  
**Status:** Blob infrastructure working. Interface transfer is wrong. Velocity not implemented.

---

## What exists right now (implemented, compiling, running)

### Cell struct (`src/defs.h`)
```c
typedef struct {
    uint8_t type;    // CELL_AIR=0 / CELL_STONE=1 / CELL_DIRT=2 / CELL_PLATFORM=3 + FLAG_STICKY(bit7)
    uint8_t water;   // fluid amount 0–255 (only meaningful on CELL_AIR cells)
    uint8_t temp;    // 0–255, 128=ambient, reserved for thermal sim
    uint8_t vector;  // reserved for velocity, currently unused
} Cell;
```
Note: authoritative design (`FLUID_SIM_DEFINITIVE.md`) renames `water`→`amount` and promotes CELL_WATER to a first-class type, but that rename hasn't happened yet. The field is called `water` in all current code.

### Blob system (`src/sim/blob.c/h`)

**What it does:**
- BFS flood fill over all CELL_AIR cells at init
- Assigns `blob_id[WORLD_W*WORLD_H]` (uint16_t per cell, BLOB_NONE=0 for solid)
- Tracks per-blob: `water_sum`, `volume`, `sealed`, `dirty`, `active`, bounding box (min/max x/y), `gas_pressure`, `initial_gas_vol`
- `blob_mark_dirty()` — marks a blob and its 4 neighbours dirty on cell type change. Called from dig, place, and projectile impact in main.c.
- `blob_update()` — if any blob is dirty, re-runs full `blob_init()`. Full re-flood on any topology change (targeted re-fill is a TODO).
- `blob_pressure_tick()` — column scan → interface scan → transfer (see below)

**Phase separation (just added):**  
BFS now separates wet cells (`water > WATER_DAMP=8`) from dry air cells. They get different blob IDs even if 4-connected. This means a full water body and an adjacent empty chamber are separate blobs, which is required for interface transfer to fire.

**What's broken about blob_pressure_tick:**  
The current implementation does a per-blob column scan to find water surface, then walks the bounding box looking for adjacent cells with a different `blob_id`. When it finds one, it computes pressure on each side and transfers via `pop_from_top()`.

The problem: `pop_from_top()` removes water from the topmost cells of the source blob and adds it at the interface cell. There is **no velocity assigned**. The water just appears at the interface and then relies on CA equalization to spread it. For slow communicating-vessel equalization this might eventually converge, but:
1. It's very slow (CA equalization is diff/2 per tick lateral spread)
2. For a tall pressurized column exiting into open space, the water should shoot sideways at high velocity — instead it just dribbles one cell at a time

### Water CA (`src/sim/water.c`)

Pass 1 only — gravity + equalization. The upward-pressure pass (pass 2) was removed when blob pressure was introduced.

```c
void tick_water(Cell *cells, int bias);
```

No cross-blob logic. No velocity handling. Pure settling CA.

### Tick order (`src/main.c`)

```
1. blob_update()           — re-flood if dirty
2. blob_pressure_tick()    — column scan, interface transfer (broken, see above)
3. tick_dirt()             — sand-fall CA
4. tick_water() × 3        — gravity + equalization CA
```

### Editor (`editor.html`)

MSPaint-style map editor in HTML/JS. Exports `test.map` as raw binary matching the Cell struct layout (4 bytes per cell, WORLD_W×WORLD_H). Game loads `test.map` if present, else runs `terrain_generate()`.

### Debug overlay (backtick)

- Water cells → pressure heat map (blue=low, red=high) based on depth from water surface × HYDROSTATIC_K
- Dry air cells → dark grey (open) or purple (sealed)
- Lets you see blob boundaries and pressure gradient visually

---

## What needs to happen next

The authoritative design is `FLUID_SIM_DEFINITIVE.md`. The gap between current code and that design, in order:

### 1. Velocity on interface transfer (the immediate blocker)

When `blob_pressure_tick` places water at an interface cell, it must encode velocity in `Cell.vector` based on the pressure head (height delta from pop point to interface). Without this, high-pressure water just oozes rather than spraying.

The vector byte is currently unused (`0x00` everywhere). It needs:
- Encoding: hi nibble = dx signed (-8..+7), lo nibble = dy signed (-8..+7)
- Set at transfer time: magnitude = `sqrt(height_delta) * VELOCITY_K`, direction = toward receiving blob
- Decay in `tick_water`: move vectored cells first (ballistic), then apply gravity (+1 to dy), then drag (× DRAG_FACTOR per axis), zero when magnitude < 1

Constants from the definitive spec:
```c
#define VELOCITY_K    1.0f
#define DRAG_FACTOR   0.875f
```

### 2. Column pressure table (replace inline scanning)

Currently `blob_pressure_tick` re-scans each column inline during the interface loop, which is redundant and slow. The definitive design calls for a scratch `col_pressure[x]` table built once per blob per tick, then used during interface detection. This is a performance and correctness improvement — the inline scan misses the per-column surface height for columns in the same blob that have dry sections.

### 3. Rename `Cell.water` → `Cell.amount`, promote CELL_WATER

The definitive design tracks air and water as separate cell types (not just an amount on CELL_AIR). This unblocks air bubble buoyancy, humidity tracking, and evaporation. It's a mechanical rename across all files — no behavior change — but it needs to happen before the evap/condense pass.

### 4. 2px solid bridging in flood fill

Current flood fill stops at any solid cell. The definitive design says air blobs should bridge 1–2px solid inclusions (small debris doesn't split an air blob). Not blocking current work.

### 5. Soil pressure erosion (`soil_pressure_erode()`)

Scan fluid-solid interfaces. If fluid pressure > SOIL_RESIST and soil is 1px thick with fluid on both sides: breach it (convert to CELL_AIR, call blob_mark_dirty). Gives dam breaks and aquifer puncture.

### 6. Evap/condense (pass 4) — deferred

Temperature-driven phase transfer. Depends on all previous passes working. Not needed until the water cycle is being built.

---

## Current constants in code

```c
// defs.h
#define WATER_DAMP              8
#define WATER_SHALLOW          64
#define WATER_FULL            200
#define PRESSURE_ATM          1.0f
#define HYDROSTATIC_K         0.04f
#define PRESSURE_EPSILON      0.02f
#define TRANSFER_RATE         0.25f
#define MAX_TRANSFER_PER_TICK 48
#define MAX_BLOBS             2048
#define BLOB_NONE             0
```

Constants from `FLUID_SIM_DEFINITIVE.md` not yet in code:
```c
#define HYDRO_K_AIR    0.001f  // air is nearly weightless per cell
#define VELOCITY_K     1.0f
#define DRAG_FACTOR    0.875f
#define SOIL_RESIST    3.0f
#define BLOB_BRIDGE_PX 2
```

---

## File map

```
src/defs.h          — Cell struct, all constants, Blob struct
src/sim/blob.h/c    — flood fill, dirty marking, blob_pressure_tick
src/sim/water.h/c   — CA settling only (gravity + equalization)
src/sim/dirt.h/c    — sand-fall CA
src/sim/impact.h/c  — projectile impacts (calls blob_mark_dirty)
src/main.c          — tick order, test.map loader, dig/place/impact dirty marking
src/render.c        — world render + pressure debug overlay
editor.html         — map editor (open in browser, export test.map)
FLUID_SIM_DEFINITIVE.md  — full authoritative design (read this)
src/INTERFACES.md   — all function signatures (keep updated)
```

---

## The one thing Opus needs to plan

**How to implement velocity on interface transfer, and integrate it cleanly into the CA pass.**

Specifically:
- The vector byte encoding (signed nibbles, range, encoding/decoding helpers)
- Where in `blob_pressure_tick` velocity gets assigned (after pop_from_top, before placing at interface)
- How `tick_water` changes to process vectored cells first (before gravity/equalization) and decay vectors
- Whether vectored cells skip the normal gravity/equalization rules (they should — they're in flight)
- How vectored water interacts with solid walls (bounce? stop? splash?)
- How it interacts with entering a water body (deposit, clear vector, merge into blob)

The pressure system (column scan, ΔP detection, pop-from-top) is structurally correct. The missing piece is that the popped water needs momentum.
