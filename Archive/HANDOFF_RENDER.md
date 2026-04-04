# Verdant — Render Crate Handoff

This document is for a Claude instance (or human) picking up work on `crates/render`.
Read `CLAUDE.md` first — it has the full GDD, aesthetic goals, and architecture context.

---

## 1. What Verdant Is

Verdant is a pixel-simulation terraforming game. The player arrives on a dead world and
coaxes it back to life — seeding water cycles, planting ecosystems, restoring atmosphere.
The world is an infinite 2D grid of simulation cells, each evolving via cellular automaton
rules on the CPU. The renderer's job is to display that grid as a living, breathing image
and get out of the way.

Reference DNA: Noita (pixel sim), Terraria (mining world), Metroid (ability-gated
exploration), Lemmings (emergent agents), Scorched Earth (ballistics), Solar Jetman (flight).
Aesthetic goal: dark-to-green palette progression. Start grey and dead; end lush and warm.

---

## 2. Architecture

```
┌──────────┐       ┌──────────┐      ┌──────────────────┐
│  app     │ owns  │   sim    │      │     render       │
│ (binary) │──────▶│ crates/  │──────▶ crates/render/  │
│          │       │ sim/     │  &   │                  │
└──────────┘       └──────────┘[Cell]└──────────────────┘
```

- **sim** (`crates/sim`): Pure logic. No I/O, no GPU, WASM-portable. Owns the world state.
- **render** (`crates/render`): Display only. Reads sim data, never writes it.
- **app** (`crates/app`): Wires them together. Owns the game loop.

The contract between sim and render: `sim` produces `&[Cell]` (262,144 cells = 512×512) per
visible chunk. Render uploads that slice as a GPU texture and draws it. **Zero shared mutable
state.** Render never calls any `&mut` method on sim types.

---

## 3. The Cell Struct (exact layout)

```rust
// crates/sim/src/cell.rs
#[repr(C)]
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct Cell {
    // Physics layer (bytes 0–3) — updated every tick
    pub water:       u8,   // 0 = bone dry,   255 = fully saturated
    pub mineral:     u8,   // 0 = vacuum/air, 255 = dense hard rock
    pub temperature: u8,   // 0 = frozen,     128 = ambient, 255 = molten
    pub vector:      u8,   // velocity: high nibble = dx (-8..+7), low nibble = dy (-8..+7)

    // Biology layer (bytes 4–7) — updated per biology tick
    pub species:     u8,   // 0 = inorganic; 1-255 = species ID
    pub tile_type:   u8,   // TILE_ROOT=1, TILE_STEM=2, TILE_LEAF=3, TILE_FLOWER=4
    pub growth:      u8,   // 0 = seed/sprout, 255 = fully mature
    pub energy:      u8,   // 0 = dying, 255 = thriving

    // Root reference (bytes 8–11) — plant tiles only
    pub root_row:    i16,  // absolute grid row of this plant's root
    pub root_col:    i16,  // absolute grid col of this plant's root

    // Light layer (bytes 12–15) — GPU-friendly
    pub light:       u8,   // computed light level (0=dark, 255=full)
    pub sunlight:    u8,   // direct sunlight reaching this cell
    pub _pad:        u16,  // explicit padding to reach 16 bytes
}
// sizeof(Cell) == 16 (compile-time asserted in cell.rs)
```

Key threshold constants (from `crates/sim/src/cell.rs`):

```rust
// Temperature
pub const TEMP_AMBIENT:   u8 = 128;
pub const TEMP_FREEZE:    u8 = 64;   // water → ice below this
pub const TEMP_BOIL:      u8 = 192;  // water → steam above this
pub const TEMP_MELT_ROCK: u8 = 240;  // mineral → lava above this

// Water
pub const WATER_TRACE:     u8 = 20;
pub const WATER_DAMP:      u8 = 80;
pub const WATER_WET:       u8 = 150;
pub const WATER_SATURATED: u8 = 220;

// Mineral
pub const MINERAL_TRACE: u8 = 20;
pub const MINERAL_SOIL:  u8 = 80;
pub const MINERAL_DIRT:  u8 = 140;
pub const MINERAL_ROCK:  u8 = 200;
pub const MINERAL_HARD:  u8 = 240;
```

---

## 4. Chunk Layout and GPU Texture Pages

Each chunk is `512 × 512` cells = 262,144 cells.

```rust
// crates/sim/src/chunk.rs
pub const CHUNK_WIDTH:  usize = 512;
pub const CHUNK_HEIGHT: usize = 512;
pub const CHUNK_AREA:   usize = CHUNK_WIDTH * CHUNK_HEIGHT;
```

A chunk occupies a single `512×512` GPU texture. Cell data is a flat row-major array:
`index = y * CHUNK_WIDTH + x`. Chunk (cx, cy) maps to world position:
`world_x = cx * 512 + local_x`, `world_y = cy * 512 + local_y`.

The `ChunkManager::iter_chunks()` method returns all loaded chunks as
`impl Iterator<Item = (&ChunkCoord, &Chunk)>`. The renderer iterates this to find
which chunks are on screen, then calls `chunk.front_slice() -> &[Cell]` to get the
flat cell slice for upload.

---

## 5. Rendering Philosophy — Continuous Values, Not Type Lookups

**There is no material-type enum.** Cell appearance is derived entirely from the continuous
values `water`, `mineral`, `temperature`, `species`/`tile_type`. Do not add a lookup table
like `match material { Water => blue, Rock => grey }`. Instead, blend from ratios.

### Suggested fragment shader approach:

```wgsl
// Fragment shader pseudocode (WGSL)
fn cell_color(water: f32, mineral: f32, temp: f32, species: u32, tile_type: u32) -> vec4<f32> {
    // Normalize 0-255 → 0.0-1.0
    let w = water   / 255.0;
    let m = mineral / 255.0;
    let t = temp    / 255.0;

    // Base colors
    let air_color      = vec3(0.05, 0.05, 0.08);   // dark near-black
    let water_color    = vec3(0.1,  0.4,  0.9);    // deep blue
    let mineral_color  = mix(vec3(0.35, 0.28, 0.22),  // soil brown
                             vec3(0.5,  0.5,  0.55),  // rock grey
                             clamp((m - 0.3) / 0.7, 0.0, 1.0));
    let hot_tint       = vec3(1.0, 0.3, 0.0);      // orange-red for lava/heat
    let plant_color    = vec3(0.1, 0.6, 0.15);     // green for living cells

    // Blend by dominant component
    var color = air_color;
    color = mix(color, mineral_color, m);           // mineral tints base
    color = mix(color, water_color,   w * (1.0 - m)); // water shows through if not solid
    color = mix(color, hot_tint,      clamp((t - 0.7) / 0.3, 0.0, 1.0)); // heat glow

    // Plant override (species > 0)
    if species > 0u {
        color = mix(color, plant_color, 0.7);
    }

    // Temperature vaporizes water visually (steam = pale blue-white)
    if t > 0.75 && w > 0.5 {
        color = mix(color, vec3(0.85, 0.9, 1.0), 0.6); // steam
    }

    // Light modulation from cell.light
    // (light pass writes to cell.light; multiply final color by it)
    return vec4(color, 1.0);
}
```

This gives the right aesthetic: rock is grey-brown, wet soil is darker, deep water is blue,
hot zones glow orange, plants pulse green.

---

## 6. What the Render Crate Needs to Do

### Stack
- **wgpu** — GPU abstraction (works on Vulkan/DX12/Metal)
- **winit** — cross-platform window + event loop
- **bytemuck** — safe `Pod` casting for buffer uploads

### Tasks

1. **Window + wgpu init** — `winit::EventLoop`, `wgpu::Device`, `wgpu::Queue`, swap chain.

2. **Chunk texture management** — `HashMap<ChunkCoord, wgpu::Texture>`. On first render of a
   chunk, create a `512×512` `Rgba8Unorm` texture. Each frame, upload the chunk's cell slice
   as texture data. The upload is the hot path — keep it async if possible.

3. **Cell → pixel conversion** — Either:
   a. CPU-side: convert `&[Cell]` → `&[u8; 512*512*4]` (RGBA bytes) before upload, or
   b. GPU-side: upload raw cell bytes as a custom texture format and decode in the shader.

   Option (b) is more elegant. Upload the 16-byte cell as 4 × `Rgba8Unorm` channels
   (bytes 0-3, 4-7, 8-11, 12-15). The fragment shader samples all 4 channels and derives
   color. But this requires a custom shader setup. Option (a) is simpler for a first pass.

4. **Camera system** — Pan in chunk space. `camera_cx`, `camera_cy` = which chunk is
   centered on screen. Support zoom levels where 1 cell = 1, 2, 4, or 8 pixels.

5. **Render pass** — Render visible chunks as quads (one quad per chunk, textured).
   Chunks outside the viewport are skipped.

6. **Lighting pass (later)** — GPU compute shader that propagates `sunlight` and `light`
   values top-down and outward from light sources. Written back to the CPU-side cell array
   OR kept GPU-side if you track a separate light buffer per chunk. Discuss with the sim
   author before adding light fields to the CPU sim.

### Interface to the Sim

```rust
// In app/src/main.rs (orchestration layer):
let chunks = manager.iter_chunks();
for (coord, chunk) in chunks {
    renderer.update_chunk(*coord, chunk.front_slice()); // uploads cell data
}
renderer.present(&camera);
```

The render crate should expose something like:

```rust
pub struct Renderer { ... }

impl Renderer {
    pub fn new(window: &winit::window::Window) -> Renderer { ... }
    pub fn update_chunk(&mut self, coord: ChunkCoord, cells: &[Cell]) { ... }
    pub fn evict_chunk(&mut self, coord: ChunkCoord) { ... } // free GPU texture
    pub fn present(&self, camera: &Camera) { ... }
}
```

---

## 7. What NOT to Touch

- **`crates/sim/`** — all sim logic lives here. Do not add physics to the renderer.
- **`crates/app/src/main.rs`** — the game loop owner. Renderer just plugs in.
- **`assets/shaders/`** — this is where your `.wgsl` files live. Put them here.
- **`Cell` struct layout** — do not change byte widths or field order. The struct is
  `#[repr(C)]` and the sim crate uses it everywhere. If you need extra render data
  (e.g. a GPU-side light buffer), keep it renderer-local.

---

## 8. Reference DNA — Aesthetic Context

| Game              | What to borrow                                    |
|-------------------|---------------------------------------------------|
| Noita             | Pixel-perfect simulation feel; particles react    |
| Terraria          | Chunky pixel world; mining is satisfying          |
| Metroid           | Dark, hostile atmosphere → earned warmth          |
| Lemmings          | Emergent life feels surprising, not scripted      |
| Scorched Earth    | Explosive ballistics look chunky and satisfying   |
| Solar Jetman      | Floaty, physics-based vehicle feel               |

Palette progression: **start grey-black (dead world)** → **warm browns (geology)**
→ **blue (water cycle active)** → **green (first plants)** → **full color biosphere**.
The renderer should support a "saturation" or "vitality" global uniform that the app
drives as the world comes alive.

---

## 9. Running Instructions

```bash
# From D:\verdant
cargo build              # build everything
cargo test -p verdant-sim  # run sim tests only
cargo run                # runs the app (stub for now)
```

Rust is at `C:\Users\digit\.cargo\bin\`. Full instructions in `CLAUDE.md`.

---

## 10. Current State

The sim crate compiles and has passing tests for cell encoding, chunk mechanics,
boundary stitching, and chunk management. Water simulation is implemented (gravity,
spread, pressure, absorption, diffusion) — tests being finalized.

The render crate is a stub (`crates/render/src/lib.rs`) with no dependencies yet.
Your job is to flesh it out. Start with a simple "draw grey rectangles per chunk"
and iterate toward the full color shader.
