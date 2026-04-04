# Verdant — Claude Context

## Read this first

**Before doing any work, read `GDD.md`.** It is the authoritative game design document
and contains the full narrative, world structure, vehicle systems, upgrade trees, and
progression arc. This file (CLAUDE.md) is a technical quick-start — the GDD is the why.

**Key things the GDD contains that you need to know:**
- The player is **Biomimetica** — an AI that *chose* to come here. The Panspermia Project.
  Not a player character piloting a machine — you ARE the machine.
- The world is a **fake globe** — horizontal wrap, lava core, machine buried at depth.
  No surface crater. Surface starts dead and dry. Player creates the water cycle.
- **Tone:** over-the-top cartoon science. Serious simulation, ridiculous execution.
- Full vehicle upgrade trees, biome system, ecological conflict model.

**Other docs in the repo root** (check before starting work in any area):
```
GDD.md                      — full game design, narrative, systems, progression
HANDOFF_SIM_WORLDGEN.md     — worldgen spec: noise stack, depth profile, sectors, POIs
HANDOFF_SIM_PLANTS.md       — plant growth system spec
HANDOFF_RENDER.md           — render crate architecture and shader philosophy
HANDOFF_RENDER_SCANNER.md   — scanner overlay: three knowledge states, false-color tiers
HANDOFF_RENDER_GAMEPAD.md   — Xbox gamepad controls (NES Solar Jetman scheme for pod)
PROGRESS_RENDER.md          — render agent's current state (written by render instance)
```

---

## Developer notes

**Adrian knows C well, is new to Rust.** Code comments should bridge C → Rust
concepts. Don't explain pointers, memory layout, bitwise ops. Do explain ownership,
borrow checker patterns, Copy vs Clone, trait dispatch, Option/Result idioms.
Frame Rust-specific things by analogy to C equivalents where possible.

---

## Crate structure

```
crates/
├── sim/        Pure simulation. No graphics deps. WASM-portable in principle.
│               All game logic lives here.
├── render/     wgpu rendering layer. Consumes sim output.
└── app/        Binary entry point. Wires sim + render, drives the game loop.
```

---

## Architecture

### World: fake globe, horizontal wrap

The world wraps horizontally. Chunk coordinate `cx` wraps at `WORLD_WIDTH_CHUNKS`.
Going deep enough hits the lava core (never simulated — render only).
The machine is buried at approximately y=600 cells below surface. No surface crater.

```
World coords  (wx, wy):  any i32 — global pixel position
Chunk coords  (cx, cy):  (wx / 512, wy / 512)
Local coords  (lx, ly):  (wx % 512, wy % 512) — position within chunk
```

**Discovery rule:** Chunks do not pre-generate. A chunk enters existence only
when the player enters its Chebyshev range. Undiscovered chunks contribute
rock-default ghost cells to loaded neighbors.

### Chunk states

```
Active    — within active_radius of player; ticked every sim step
KeepAlive — off-screen but has active biology/water; keeps simulating
Dormant   — no activity; evicted after IDLE_DAYS_BEFORE_DORMANT daily passes
```

### Cross-chunk stitching: ghost ring

Before each physics tick, boundary.rs copies the outermost row/column of each
neighboring chunk into a GhostRing on the target chunk. Sim rules call
`get_with_ghost(lx, ly)` for all neighbor lookups. Rules never special-case chunk edges.
Unloaded neighbors contribute solid rock ghost cells.

### Simulation frequencies

**Per-frame (high frequency):**
- Water cycle: gravity, spread, pressure, absorption, diffusion  ← DONE
- Particle physics: vector-based movement, collision
- Lighting (GPU compute pass only)

**Daily pass (~30 real min/in-game day):**
- Plant growth, creature AI, population dynamics, decomposition
- Keep-alive re-evaluation / dormancy transitions

---

## Cell encoding — 16 bytes, #[repr(C)]

```
Offset  Field        Type   Description
──────  ───────────  ─────  ──────────────────────────────────────────────────
0       water        u8     Water/moisture (0=bone dry, 255=saturated)
1       mineral      u8     Mineral density (0=vacuum, 255=dense hard rock)
2       temperature  u8     Thermal state (0=frozen, 128=ambient, 255=molten)
3       vector       u8     Velocity: hi nibble=dx(i4,-8..+7), lo=dy(i4,-8..+7)
4       species      u8     0=inorganic; 1-255=species ID
5       tile_type    u8     TILE_AIR/ROOT/STEM/LEAF/FLOWER (only if species>0)
6       growth       u8     Growth stage (0=seed, 255=mature)
7       energy       u8     Stored energy (0=depleted, 255=thriving)
8-9     root_row     i16    Absolute row of this plant's root tile
10-11   root_col     i16    Absolute col of this plant's root tile
12      light        u8     Computed light level (0=dark, 255=full brightness)
13      sunlight     u8     Direct sunlight
14-15   _pad         u16    Explicit padding; sizeof(Cell) == 16 guaranteed
```

**No discrete material type.** Behavior derives from value ratios:
```
high water + low mineral + high temp  → steam
high water + low mineral + mid temp   → liquid water
high water + low mineral + low temp   → ice
low water  + low mineral              → air / vacuum
high mineral + low water              → dry rock
high mineral + medium water           → wet soil / mud
high mineral + high temp              → lava
```

All-zero = Cell::AIR = vacuum. A calloc'd buffer is a valid empty world.

---

## Current build status

```
cargo test -p verdant-sim   — 31/31 passing
cargo build                 — compiles clean
cargo run                   — window opens, renders test scene (render agent work)
```

Water simulation complete: `crates/sim/src/water/` (6 modules).
Render crate functional: window, camera, cell→color, chunk textures.

---

## Key GDD systems — build order

```
1. ✅ Water cycle (gravity, spread, pressure, absorption, diffusion)
2. Worldgen — spec: HANDOFF_SIM_WORLDGEN.md
3. Pod physics (Solar Jetman: 16 angles, spring tow, fuel)
4. Tank ballistic arm (Scorched Earth: arc, cell-state payloads)
5. Plant growth — spec: HANDOFF_SIM_PLANTS.md
6. Lotka-Volterra creature populations
7. Base upgrade progression (ore + biomass dual tracks)
```

---

## Running

```sh
C:/Users/digit/.cargo/bin/cargo build
C:/Users/digit/.cargo/bin/cargo test
C:/Users/digit/.cargo/bin/cargo run --bin verdant
```
