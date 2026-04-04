# Verdant

A pixel-art terraforming sandbox. You are **Biomimetica** — an AI that chose to
come here, buried deep in a dead world. You create the water cycle. Life follows.

See `Archive/GDD.md` for the full game design, narrative, and systems.

---

## Current state — F1 prototype

Multi-file C + raylib. Active source is in `src/`. The old Rust/wgpu prototype is in `crates/` for reference only.

### World & terrain

- 480×270 pixel world, one byte per cell: `AIR`, `STONE`, `DIRT`, `PLATFORM` (one-way)
- Procedural ceiling: fbm + triangle + spike waves → rock base + sticky-dirt stalactites
- Static test scene: stone floor, raised ramp, dirt layer, one-way platforms
- Scaled pixel-perfect canvas — fills any resolution. F11 fullscreen

### Dirt simulation

- Sand-fall CA: falls straight down → diagonal, bias-alternated to eliminate drift
- `FLAG_STICKY` (bit 7 of cell byte) pins dirt to ceilings and walls
- Digging a neighbour unsticks it; rover weight erodes exposed sticky edges
- Dirt **sinks through water** — swaps cells so sand displaces water upward

### Water simulation

See [Water system](#water-system) section below for design detail.

- Continuous amount model: parallel `uint8_t water[W×H]` (0–255 per AIR cell), not a cell type
- Three rules per tick, bottom-to-top:
  1. **Gravity** — fall into cell below as much as will fit
  2. **Equalization** — halve difference with each horizontal neighbour (flat surfaces, U-tubes)
  3. **Upward pressure** — cells blocked below push water upward; cascades the full column in one pass
- 3 passes per frame for fast equalization across connected basins
- Current test scene: **fountain** — large sealed basin, 3px sealed nozzle, pressurized jet exits the top

### Player

- 4×8 pixel sprite, 2-frame walk cycle, mirror-flip on turn
- Gravity, jumping, coyote time (8 frames), step-up (3px)
- Platform fall-through (S/down, 15-frame grace)
- Dig (LMB/E, autorepeat 80ms), place (RMB), inventory cap 99

### Rover vehicle

- 24×16 pixel sprite; vertically sheared to terrain angle
- Enter/exit: F key within 24px
- Physics: gravity (1.4×), acceleration, momentum, drag modes (roll/brake/park), step-up (4px), bounce
- Handbrake (P): no slope roll, full 5–175° arm arc; driving: arm locked to facing half
- Edge erosion: rover weight unsticks exposed sticky-dirt edges as it drives

### Ballistic arm (Scorched Earth style)

- Absolute angle system: 0°=right, 90°=up, 180°=left
- Aim: ← / → (continuous) or Shift/Ctrl + ← / → (stepped ±1/±10°)
- Power: ↑ / ↓; Fire: Space
- Trajectory preview arc rendered in screen space
- Impact center backed up opposite velocity so deposits land in open air, not inside terrain
- 3 ammo types (Tab/Q to cycle):
  - **Soil Ball** — loose dirt circle, falls immediately via sand sim
  - **Sticky Soil** — same shape, FLAG_STICKY set, adheres to ceiling and walls
  - **Liquid Soil** — tall dense column, slumps and flows to fill low spots

### Input

- KB+Mouse and Xbox gamepad, auto-detect and auto-switch
- All bindings in `src/input.c` — `input_poll()` is the remapping shim

---

## Water system

The water sim is a **CA-style continuous amount model** — not a particle sim, not a discrete cell-type sim. Each AIR cell holds a `uint8_t` amount (0–255). Solid cells (STONE, DIRT, PLATFORM) hold no water.

### Files involved

| File | Role |
|------|------|
| `src/defs.h` | Water amount thresholds (`WATER_DRY/DAMP/SHALLOW/FULL`), all world/cell constants |
| `src/sim/water.h` | `tick_water()` and `unstick()` declarations |
| `src/sim/water.c` | Full simulation logic — gravity, equalization, upward pressure passes |
| `src/sim/dirt.c` | `tick_dirt()` — dirt/water cell swap so sand sinks through water |
| `src/render.c` | `render_world_to_pixels()` — maps water amounts to pixel colors (surface vs. deep vs. shallow) |
| `src/terrain.c` | `terrain_generate()` — builds the fountain test scene; writes initial `water[]` amounts |
| `src/main.c` | Owns the `water[WORLD_W*WORLD_H]` array; calls `tick_water` 3× per frame |

The `water[]` array is separate from `world[]`. `world[]` stores cell material (AIR/STONE/DIRT/PLATFORM). `water[]` stores the water amount at the same index — only meaningful for AIR cells.

### Current model — three passes per tick

**Pass 1 — Gravity + Equalization (bottom-to-top)** — `src/sim/water.c`, first loop

For each wet AIR cell, in order:
1. Fall: move as much water as fits into the cell directly below (if AIR).
2. Equalize sideways: halve the difference with the left and right neighbour. Bias-alternated scan direction prevents left/right drift. This is the communicating-vessels rule — connected bodies reach the same level without needing explicit column-height measurement.

**Pass 2 — Upward pressure (bottom-to-top)** — `src/sim/water.c`, second loop

For each wet AIR cell that cannot fall (blocked by solid or saturated cell below):
- Halve the water deficit between this cell and the one above it.
- Because the scan is bottom-to-top, this cascades upward through a sealed tube in a single pass — approximating instantaneous pressure propagation in an incompressible column.
- This is what drives the fountain jet and enables siphon geometry.

**Rendering** — `src/render.c`, `render_world_to_pixels()`

```c
if (w >= WATER_FULL)  → deep blue (60, 80, 160) or surface blue (90, 160, 230)
if (w >= WATER_DAMP)  → surface blue (shallow fringe)
else                  → transparent air
```

**Rendering thresholds** — `src/defs.h`:
```
WATER_DRY    =   0   — no water, renders as air
WATER_DAMP   =   8   — below this: treated as dry (transient settling)
WATER_SHALLOW=  64   — surface/shallow color
WATER_FULL   = 200   — fully saturated, solid water color
```

**3 passes per frame** — `src/main.c` calls `tick_water` three times per game loop iteration to accelerate equalization across wide basins. Each pass alternates scan direction bias.

### What works

- Gravity and flat-surface equalization (communicating vessels)
- Upward pressure through sealed tubes → fountain jet
- Sand sinking through water (dirt/water cell swap in tick_dirt)
- Water rendered at multiple opacity/color thresholds

### Known limitations / design questions for rework

- **Velocity is not tracked** — the model is purely pressure-driven. Water has no momentum, no kinetic energy. A jet exits a nozzle under pressure but immediately loses that energy and falls straight down — it doesn't arc horizontally. True fountain arcing requires carried velocity.
- **No flow rate vs. pipe diameter** — a 1px and 100px pipe equalize at the same rate (both halve their diff each tick). Real pressure should scale with cross-section.
- **Thin-wall tunneling** — fast-moving (or high-amount) water can skip over 1px boundaries in one tick. The sand sim has the same problem.
- **3-pass equalization is a hack** — it compensates for the CA's single-step locality. A pressure-propagation model or a flood-fill approach would be more principled and could be faster.
- **No surface tension / droplets** — small isolated amounts don't behave distinctly from large bodies.
- **No evaporation / humidity** — needed for the water cycle (the game's core loop).

### Alternative approaches worth considering

| Approach | Character | Cost |
|----------|-----------|------|
| Noita-style particle sim | True velocity, arcing, splashing | O(wet cells), complex interactions |
| Pressure-field solver (iterative) | Accurate U-tube / siphon / fountain | O(W×H) per frame, overkill for pixel CA |
| Flow-rate CA (à la Dwarf Fortress) | Column-height pressure, pipe diameter effects | Moderate; good match for the game's scope |
| Hybrid: CA amounts + velocity field | Best of both — velocity carries momentum across cells | Two arrays to maintain, more complex rendering |

The current model is closest to a **flow-rate CA** but lacks the flow-rate-scales-with-pressure detail. Adding a separate per-cell velocity vector (even just a `int8_t vel_y` per wet cell) would let water carry momentum out of a nozzle without a full particle sim.

---

## Build

Requires GCC (MinGW on Windows) and the bundled raylib in `deps/raylib/`.
`raylib.dll` must be next to the executable at runtime (already in repo root).

```sh
make          # build verdant_f1.exe
make clean    # remove executable
```

**Run:**
```sh
./verdant_f1.exe
```

---

## Controls

### On foot

| Action                  | KB+Mouse       | Gamepad      |
|-------------------------|----------------|--------------|
| Move                    | A / D          | Left stick   |
| Jump                    | W / Space / ↑  | A button     |
| Fall through platform   | S / ↓          | Left stick ↓ |
| Dig                     | LMB or E       | —            |
| Place                   | RMB            | —            |
| Enter rover             | F (near rover) | X button     |

### In rover

| Action          | KB             | Gamepad       |
|-----------------|----------------|---------------|
| Drive           | A / D          | Left stick    |
| Brake           | S              | —             |
| Handbrake       | P              | —             |
| Aim arm         | ← / →          | Right stick X |
| Adjust power    | ↑ / ↓          | Right stick Y |
| Fine aim (±1°)  | Shift + ← / →  | —             |
| Coarse aim (±10°) | Ctrl + ← / → | —             |
| Fire            | Space          | A / RT        |
| Cycle ammo      | Tab / Q        | —             |
| Exit rover      | F              | X button      |

### Global

| Action        | Key          |
|---------------|--------------|
| Fullscreen    | F11          |
| Debug overlay | ` (backtick) |
| Quit          | Escape       |

---

## Repo layout

```
src/                    — all C source
  main.c                — game loop entry point
  defs.h                — all constants and macros
  sprites.h             — sprite bitmaps and palettes (header-only)
  noise.h/c             — hash1, vnoise, fbm, triwave, spike
  world.h/c             — AABB collision queries (box_solid_ex, ground_y_at)
  terrain.h/c           — terrain_generate() — replace for real worldgen
  sim/
    dirt.h/c            — tick_dirt (sand-fall CA)
    water.h/c           — tick_water, unstick (water CA)
    impact.h/c          — explode, impact_soil_ball/sticky/liquid
  player.h/c            — PlayerState, player_update()
  rover.h/c             — RoverState, rover_update(), draw_rover_sheared()
  rover_arm.h/c         — ArmState, ProjState, arm_update(), arm_fire(), proj_update()
  input.h/c             — InputState, input_poll()
  render.h/c            — render_world_to_pixels(), render_screen_overlay(), etc.
  INTERFACES.md         — master function/struct reference (keep updated)
Makefile
deps/raylib/            — bundled raylib headers + libs
assets/                 — sprites, shaders (future use)
Archive/                — design docs + old single-file version
  GDD.md                — authoritative game design document
  HANDOFF_SIM_WORLDGEN.md
  HANDOFF_SIM_PLANTS.md
  HANDOFF_RENDER_GAMEPAD.md
crates/                 — old Rust/wgpu prototype (reference only)
```

---

## GDD build order — next steps

```
1. ✅ Cell physics — dirt falls (through water), water flows, upward pressure / fountain
2. ✅ Player + rover — movement, enter/exit, slope shear
3. ✅ Ballistic arm — angle/power/ammo, trajectory preview, impacts on all surfaces
4. Water rework — velocity, momentum, true fountain arcing (planning in progress)
5. Worldgen — replace terrain_generate() with noise-stack system
6. Pod vehicle — Solar Jetman flight (16 angles, spring tow, fuel)
7. Plant growth — spec in Archive/HANDOFF_SIM_PLANTS.md
8. Ore system — dig stone yields minerals; dual-track upgrade currency
9. Creature populations — Lotka-Volterra dynamics
10. Base upgrade tree
```
