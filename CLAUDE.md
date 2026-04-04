# Verdant — Claude Context

## Read this first

**This is the F1 prototype** — multi-file C using raylib. Entry point is `src/main.c`.
The old Rust/wgpu multi-crate prototype is in `crates/` for reference only.

**GDD is authoritative.** The game design, narrative, vehicle systems, and progression arc
are in `Archive/GDD.md`. Key facts:
- Player is **Biomimetica** — an AI that chose to come here. You ARE the machine.
- World is a **fake globe** — horizontal wrap, lava core, machine buried at depth.
- Surface starts dead and dry. Player creates the water cycle. Life follows.
- Tone: over-the-top cartoon science. Serious simulation, ridiculous execution.

---

## Build

```sh
make            # build verdant_f1.exe
make clean      # remove executable
```

Requires GCC (MinGW on Windows) and the bundled raylib in `deps/raylib/`.
`raylib.dll` must be next to the executable at runtime (already in repo root).

Run with `./verdant_f1.exe`.

---

## Developer notes

**Adrian knows C well.** Don't explain pointers, memory layout, or bitwise ops.
Do explain any new math (noise functions, physics formulas) with a comment on intent.

---

## Current state — what's working

**World** (`src/terrain.c` generates, `src/world.h` provides collision queries)
- 480×270 pixel world stored as `uint8_t world[WORLD_W * WORLD_H]`
- Cell types: AIR, STONE, DIRT, PLATFORM (one-way), WATER
- `FLAG_STICKY` (bit 7) prevents dirt from falling — terrain starts sticky,
  digging a neighbour clears it; rover weight erodes sticky edges
- Static test scene: stone floor, raised ramp, 10px dirt layer, platforms,
  procedural ceiling stalactites (fbm + spike), communicating basins water demo

**Physics simulations** (`src/sim/`)
- `dirt.c` — sand-fall: straight down → diagonal; falls through water
- `water.c` — liquid: fall → spread sideways, communicating-vessels pressure
  3 passes per frame in main.c for fast equalization

**Player** (`src/player.h/c`)
- 4×8 pixel sprite (in `src/sprites.h`), 2-frame walk, mirror-flip on turn
- Gravity, jumping, coyote time (8 frames), step-up (3px)
- Platform fall-through (S/down, 15-frame grace timer)
- Dig (LMB/E) and place (RMB) handled in `src/main.c`; inventory in `PlayerState`

**Rover** (`src/rover.h/c`)
- 24×16 pixel sprite, slope-sensing, sprite sheared to terrain angle
- Physics: gravity (1.4×), accel/drag (roll/brake/park), step-up (4px), bounce
- Enter/exit (F key, within 24px radius), handbrake (P)
- Edge erosion: rover weight unsticks exposed sticky dirt

**Ballistic arm** (`src/rover_arm.h/c`)
- Rover-only; 9px barrel, absolute angle 5–175°, clamps to facing half when rolling
- Charge-based power, 3 ammo types (Tab/Q): Soil Ball, Sticky Soil, Liquid Soil
- Deposit radius scales with charge; trajectory preview arc in render
- Single active projectile; detonates on floor, walls, and ceiling

**Input** (`src/input.h/c`)
- KB+Mouse and Xbox gamepad, auto-detect and auto-switch
- All bindings in one place — `input_poll()` is the remapping shim

**Rendering** (`src/render.h/c`)
- Pixel buffer → `UpdateTexture` → `DrawTexturePro` (pixel-perfect scaled canvas)
- Debug overlay (backtick), F11 fullscreen

---

## File structure

```
src/
  INTERFACES.md   ← KEEP UPDATED: all struct fields and function signatures
  defs.h          — all #define constants, cell type macros, common includes
  sprites.h       — sprite arrays + palettes (header-only, static const)
  noise.h/c       — hash1, vnoise, fbm, triwave, spike
  world.h/c       — box_solid_ex, ground_y_at
  terrain.h/c     — terrain_generate() — replace this for worldgen
  sim/
    dirt.h/c      — tick_dirt
    water.h/c     — tick_water, unstick
    impact.h/c    — explode, impact_soil_ball/sticky/liquid
  player.h/c      — PlayerState, player_update()
  rover.h/c       — RoverState, rover_update(), draw_rover_sheared()
  rover_arm.h/c   — ArmState, ProjState, arm_update(), arm_fire(), proj_update()
  input.h/c       — InputState, input_poll()
  render.h/c      — render_world_to_pixels(), render_player_to_pixels(),
                    render_rover_to_pixels(), render_screen_overlay()
  main.c          — game loop, terrain gen, state instances, dig/place, enter/exit

Makefile
deps/raylib/      — bundled headers + lib
assets/           — sprites, shaders (future use)
Archive/          — old docs (GDD.md, HANDOFFs) + old CLAUDE.md
crates/           — old Rust/wgpu prototype (reference only)
```

---

## GDD build order — next steps

```
1. ✅ Cell physics — dirt falls (through water), water flows, sticky flag, erosion
2. ✅ Player + rover — movement, enter/exit, slope shear
3. ✅ Ballistic arm — angle/power/ammo, trajectory preview, impacts on all surfaces
4. Worldgen — replace terrain_generate() with noise-stack system
           — spec in Archive/HANDOFF_SIM_WORLDGEN.md
5. Pod vehicle — Solar Jetman flight (16 angles, spring tow, fuel)
6. Plant growth — spec in Archive/HANDOFF_SIM_PLANTS.md
7. Ore system — dig stone yields minerals; dual-track upgrade currency
8. Creature populations — Lotka-Volterra dynamics
9. Base upgrade tree
```
