# Verdant — Claude Context

## Read this first

**This is the F1 prototype** — a single C file using raylib. The old Rust/wgpu multi-crate
prototype is in `crates/` for reference only. All active work is in `verdant_f1.c`.

**GDD is authoritative.** The game design, narrative, vehicle systems, and progression arc
are in `GDD.md`. Key facts:
- Player is **Biomimetica** — an AI that chose to come here. You ARE the machine.
- World is a **fake globe** — horizontal wrap, lava core, machine buried at depth.
- Surface starts dead and dry. Player creates the water cycle. Life follows.
- Tone: over-the-top cartoon science. Serious simulation, ridiculous execution.

---

## Build

GCC + bundled raylib (MinGW on Windows):

```sh
gcc verdant_f1.c -o verdant_f1 \
    -Ideps/raylib/include \
    -Ldeps/raylib/lib \
    -lraylibdll \
    -lopengl32 -lgdi32 -lwinmm
```

`raylib.dll` must be next to the executable (already in repo root). Run with `./verdant_f1`.

---

## Developer notes

**Adrian knows C well.** Don't explain pointers, memory layout, or bitwise ops.
Do explain any new math (noise functions, physics formulas) with a comment on the intent.

---

## Current state — what's working

**World** (`verdant_f1.c`, top of file)
- 480×270 pixel world stored as `uint8_t world[WORLD_W * WORLD_H]`
- Cell types: `CELL_AIR=0`, `CELL_STONE=1`, `CELL_DIRT=2`, `CELL_PLATFORM=3`, `CELL_WATER=4`
- `FLAG_STICKY` (bit 7) prevents dirt from falling — generated terrain starts sticky,
  digging a neighbour clears it; rover weight erodes sticky edges
- Static terrain: flat stone floor, raised stone ramp on right, 10px dirt layer,
  three one-way platforms, ceiling with procedural rock+stalactite profile (fbm + spike waves),
  communicating basins water demo (left basin fills right via channel)

**Physics simulations** (run each frame)
- `tick_dirt()` — sand-fall: straight down → diagonal, alternating-bias scan order
- `tick_water()` — liquid: fall → spread sideways, communicating-vessels pressure (column height)
  — 3 passes per frame for fast equalization

**Player** (`cx, cy, cvx, cvy`)
- 4×8 pixel sprite, 2-frame walk animation, mirror-flip on turn
- Gravity, jumping, coyote time (8 frames), step-up (3px)
- Platform fall-through (S/down while on platform, 15-frame grace timer)
- Dig dirt (LMB or E), place dirt (RMB), inventory cap 99
- Pickup radius 18px, cell selection highlight; mouse vs gamepad input mode auto-switch

**Rover** (`rx, ry, rvx, rvy`) — 24×16 pixel sprite
- Enter/exit: F key within 24px radius; player spawns at rover's facing side
- Physics: gravity (1.4× player), acceleration/drag (roll/brake/park), step-up (4px), bounce
- Slope sensing: samples ground under each wheel, applies roll force; sprite sheared to terrain angle
- Handbrake (P): prevents rolling; throttle input releases it
- Edge erosion: rover weight unsticks exposed sticky dirt cells at its footprint edge

**Ballistic arm** (rover-only)
- 9px barrel, absolute angle 5–175°, clamps to facing half when driving
- Charge-based power: hold to charge (0→1 in ~60 frames), release to fire
- 3 ammo types (Tab/Q to cycle): SOIL BALL (loose dirt circle), STICKY SOIL (adheres to ceilings/walls),
  LIQUID SOIL (floods impact zone, flows into gaps via dirt sim)
- Deposit radius scales with charge: 2–5px
- Single active projectile; trajectory preview arc (120 steps); power bar HUD

**Rendering**
- Pixel buffer `worldImg.data` → `UpdateTexture` → `DrawTexturePro` (pixel-perfect scaled canvas)
- Water surface highlight: bright top row, dark below
- Debug overlay: backtick to toggle; shows player/rover state
- F11 borderless fullscreen toggle

**Input**
- KB+Mouse and Xbox gamepad, auto-detect and auto-switch
- Mouse mode: click/hover for cell selection, facing follows cursor
- Gamepad mode: left stick moves, right stick aims arm, A fires/jumps

---

## File structure

```
verdant_f1.c    — entire game (~1250 lines)
  CONSTANTS      — world dims, player/rover/arm params (top of file)
  SPRITE data    — player (2 frames), rover (8-color palette)
  HELPERS        — hash1, vnoise, fbm, triwave, spike, ground_y_at, draw_rover_sheared
  COLLISION      — box_solid_ex (platform-aware)
  SIMULATIONS    — tick_dirt, tick_water, unstick, explode, impact_*
  main()
    world setup  — terrain gen, basin construction, ceiling gen
    game loop
      input
      dirt sim, water sim (3 passes)
      rover enter/exit
      rover physics
      arm + projectile
      player physics (on foot only)
      cell selection / inventory
      render to pixel buffer
      draw rover, arm, projectile, player
      upload texture, composite, HUD, debug

deps/raylib/    — bundled headers + lib
assets/         — sprites, shaders (future use)
crates/         — old Rust/wgpu prototype (reference only, not built)
Archive/        — old docs and CLAUDE.md
```

---

## GDD build order — next steps

```
1. ✅ Cell physics — dirt falls, water flows, sticky flag, erosion
2. ✅ Player + rover — movement, enter/exit, slope shear
3. ✅ Ballistic arm — angle/power/ammo, trajectory preview
4. Worldgen — procedural terrain replacing the hardcoded test scene
           — spec in Archive/HANDOFF_SIM_WORLDGEN.md
5. Pod vehicle — Solar Jetman flight (16 angles, spring tow, fuel)
6. Plant growth — spec in Archive/HANDOFF_SIM_PLANTS.md
7. Ore system — dig stone yields minerals; dual-track upgrade currency
8. Creature populations — Lotka-Volterra dynamics
9. Base upgrade tree
```

---

## Key constants (quick reference)

| Constant | Value | Purpose |
|---|---|---|
| `WORLD_W / WORLD_H` | 480 / 270 | World dimensions in pixels |
| `CHAR_W / CHAR_H` | 4 / 8 | Player sprite size |
| `ROVER_W / ROVER_H` | 24 / 16 | Rover sprite size |
| `PICKUP_RADIUS` | 18 | Dig/place range (pixels) |
| `ARM_LEN` | 9 | Barrel length |
| `BLAST_RADIUS` | 5 | Explosion carve radius |
| `PROJ_GRAVITY` | 0.25 | Projectile gravity (softer arc than player) |
| `FLAG_STICKY` | 0x80 | Bit 7 of cell byte: dirt won't fall |
| `CELL_TYPE(c)` | `(c) & 0x7F` | Mask to get type, ignoring flags |
