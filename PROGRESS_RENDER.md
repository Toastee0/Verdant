# Render Crate — Progress

**Date:** 2026-03-25
**Author:** Render agent (Claude)
**Status:** Initial implementation complete — builds, passes tests, window opens

---

## What's done

### 1. Dependencies wired (`crates/render/Cargo.toml`)

- wgpu 24, winit 0.30, bytemuck 1 (with derive), pollster 0.4, log, env_logger
- Same winit/pollster added to `crates/app/Cargo.toml` for the event loop

### 2. WGSL shader (`assets/shaders/chunk.wgsl`)

- Camera uniform (group 0, binding 0): `mat4x4<f32>` view-projection
- Per-vertex: unit quad position + UV
- Per-instance: `chunk_offset` (world-pixel position of chunk's top-left)
- Vertex shader: scales unit quad to 512×512, offsets by chunk position, projects
- Fragment shader: samples pre-computed RGBA texture (color derivation is CPU-side)

### 3. Camera (`crates/render/src/camera.rs`)

- Orthographic camera centered in world-pixel space
- Pan (screen-space delta / zoom), zoom (clamped 0.25×–8×)
- `view_proj_matrix()` → column-major `[f32; 16]` for wgpu upload
- `visible_chunk_range()` → `(min_cx, min_cy, max_cx, max_cy)` for viewport culling
- Default: centered on origin chunk (256,256), 2× zoom

### 4. Cell→pixel conversion (`crates/render/src/cell_color.rs`)

- `cell_to_rgba(&Cell) -> [u8; 4]` — the core aesthetic function
- Continuous value blending, no material-type enum (matches GDD philosophy)
- Color derivation:
  - Base: dark void (0.03, 0.03, 0.05)
  - Mineral: soil brown → rock grey gradient
  - Water: blue tint, attenuated by mineral density
  - Wet soil: darkened (universal visual truth — wet earth looks darker)
  - Ice: pale blue-white when cold + wet
  - Steam: pale blue-white when hot + wet + low mineral
  - Lava: orange-red glow when mineral above melt point
  - Heat: warm tint for hot cells
  - Biology: per-tile-type colors (root=brown, stem=green-brown, leaf=vivid green, flower=species-dependent palette)
  - Dying plants: desaturate toward grey-brown
  - Light: multiplied by combined ambient+sunlight (with fallback to 1.0 while lighting pass is unimplemented)
- `cells_to_rgba(&[Cell]) -> Vec<u8>` bulk converter
- 6 passing tests: air is dark, water is blue, rock is grey, lava is hot, leaf is green, correct buffer length

### 5. Renderer (`crates/render/src/lib.rs`)

- Full wgpu initialization: instance, surface, adapter (high-performance), device, queue
- sRGB surface format preferred, auto-vsync present mode
- Render pipeline: vertex + instance buffer layouts, chunk-shader bound
- Nearest-neighbor sampler (pixel-perfect — no blur at any zoom)
- Camera uniform buffer + bind group (group 0)
- Per-chunk texture bind group layout (group 1): texture + sampler
- `update_chunk(coord, &[Cell])` — CPU color conversion → `write_texture` upload
  - Reusable 1MB pixel buffer (no per-frame allocation)
  - Lazy texture creation via `HashMap<ChunkCoord, ChunkGpuData>`
- `evict_chunk(coord)` — frees GPU texture
- `present(&Camera)` — viewport-culled draw loop:
  - Clear to dark void (0.02, 0.02, 0.04)
  - Per-chunk instance buffer with world offset
  - Skips chunks outside visible_chunk_range()
  - Handles surface lost/outdated gracefully
- `resize(width, height)` — reconfigures swap chain

### 6. App game loop (`crates/app/src/main.rs`)

- winit 0.30 `ApplicationHandler` pattern (not deprecated `run()`)
- Window: 1280×720 logical size, title "Verdant"
- Input:
  - WASD / arrow keys: pan camera
  - +/- / numpad: zoom
  - Mouse wheel: zoom
  - Left mouse drag: pan
  - Escape: quit
- Per-frame: `world.tick_high_frequency()` → upload visible chunks → `renderer.present()`
- Test content seeded in origin chunk: rock band, soil layer, water pool, mud, plant (root→stem→leaves)

---

## Build status

```
cargo build     — compiles (0 errors, 0 warnings)
cargo test      — 37 tests pass (31 sim + 6 render)
cargo run       — opens window, renders test scene
```

---

## Known limitations / next steps

| Item | Notes |
|------|-------|
| **Per-frame instance buffer allocation** | Each visible chunk creates a tiny instance buffer every frame. Fine for 9 chunks, would need a pooled instance buffer for 50+. |
| **Lighting pass** | `cell.light` and `cell.sunlight` are always 0. The `cell_to_rgba` fallback renders at full brightness. GPU compute lighting pass is the next big render task. |
| **Fog of war / void** | Currently renders all loaded chunks. No "unknown" state. Scanner overlay doc (HANDOFF_RENDER_SCANNER.md) specifies the three-state system needed. |
| **Scanner overlay** | Not started. See review notes below and HANDOFF_RENDER_SCANNER.md. |
| **Chunk texture format** | Using Rgba8UnormSrgb. If the surface format is not sRGB this could double-gamma. Works on all tested hardware but worth a defensive check. |
| **No worldgen visuals** | All chunks except origin are empty air (black). Worldgen is sim-side; once it produces geology, the renderer will show it automatically. |
| **Daily pass UI** | No sleep intermission screen. `tick_daily_pass()` can be called but has no visual feedback. |
| **Zoom labels** | No HUD, no coordinate display, no chunk grid overlay. |

---

## Design decisions (not yet implemented)

### Lighting model — three source types

The world has three categories of light source. All must support **shadow occlusion**
(light does not pass through solid cells).

**1. Pod ambient (radial)**
- Radial falloff centered on the pod's world position.
- Lights everything within radius R regardless of direction — the "lantern."
- Makes nearby cave walls, ceilings, and floors visible even behind the pod.
- Inverse-square or linear falloff (tuning parameter).
- Requires: pod (x, y) position each frame.

**2. Pod spotlight (directional cone)**
- Longer range than ambient, narrower coverage — the "flashlight."
- Defined by: pod position, facing angle, cone half-angle, max distance.
- Light contribution = distance falloff × cone attenuation (dot product of
  cell-to-pod vs facing direction, clamped by half-angle).
- This is the primary exploration light. Pointing the pod reveals the cave ahead.
- Requires: pod position + facing angle each frame.

**3. World light sources (cell-driven)**
- **Bioluminescence**: cells with `species > 0` and sufficient `energy` emit light
  at their position with a species-dependent radius and color tint.
- **Lava glow**: cells with `temperature >= TEMP_MELT_ROCK` emit warm orange light.
- **Future sources**: artificial lights, base lighting, etc.
- These make the world progressively self-lit as the player seeds life — matches
  the dead-to-alive aesthetic arc.
- Can be computed less frequently than pod lights (once per N frames or on change)
  since bio/lava sources move slowly.

**Shadow occlusion approach:**
- Pod lights: 2D raycast (Bresenham line walk) from pod position through the cell
  grid. If a solid cell is hit before the target cell, that cell is in shadow.
  Runs per-frame, parallelizable per-cell.
- World lights: flood-fill (BFS) outward from emitting cells. Propagation stops at
  solid cells. Can be cached and updated incrementally.
- Sunlight: top-down raycast per column. Stop at first solid cell. Writes to
  `cell.sunlight` field.

**Where it runs:**
- GPU compute pass is the natural fit (massively parallel per-cell work).
- CPU-side is also viable since we already do per-cell work in `update_chunk`.
- Pod lights must run every frame. Bio/lava lights can be cached.

**Cell struct fields used:**
- `cell.light` — combined computed light level (all sources summed/maxed).
- `cell.sunlight` — direct sunlight only (separate for gameplay: plants need this).
- `cell_to_rgba` already multiplies final color by these fields; currently falls back
  to 1.0 because both are always 0.

---

### Scanner overlay — surface definition and scan model

See HANDOFF_RENDER_SCANNER.md for the full spec. Key design decisions from review:

**"Surface" = any exposed solid face, not just topmost-per-column.**
The pod flies through caves. It can look at walls, ceilings, and floors. "Surface"
means any solid mineral cell (`mineral >= MINERAL_TRACE`, not water/plant/creature)
that is adjacent to an air/cavity cell. Exposed faces exist in all directions.

**Scan depth penetrates inward from exposed faces, not just downward.**
When the pod scans, it reveals N cells deep into solid rock measured from the nearest
exposed face — perpendicular to the surface, in all directions. If you're in a cave,
the scanner sees into the walls, ceiling, and floor equally.

**Implementation model: distance field from air cells.**
For each cell in the chunk, compute distance to the nearest air/cavity cell:
- **distance == 0** and cell is air/water/bio → visible (pod can see it directly)
- **distance == 0** and cell is solid → exposed surface → full visited color
- **0 < distance <= scan_depth** → false-color overlay (scanned subsurface)
- **distance > scan_depth** → void (unknown)

This is a BFS/flood-fill outward from air cells, stopping at `scan_depth`. The ghost
ring already handles cross-chunk boundary lookups, so the same infrastructure supports
neighbor checks at chunk edges.

**Three knowledge states per cell** (renderer-side, no sim changes):
```
Unknown   — void (pure black)
Scanned   — false-color overlay (desaturated, 60% alpha over void)
Visited   — full cell rendering
```

**Scan tiers** (cumulative — each tier adds to previous):
```
Tier 0: Surface only (pod light illumination, no scanner needed)
Tier 1: depth ~5 cells — cavity shapes, rough rock type (white=air, grey=rock)
Tier 2: depth ~15 cells — adds ore highlights (orange for mineral > MINERAL_HARD)
Tier 3: depth ~30 cells — adds water highlights (blue for water > WATER_WET)
Tier 4: depth ~15 cells — adds bio highlights (green for species > 0)
```

**State storage**: `HashMap<ChunkCoord, ScanTier>` in the Renderer (Option A).
Zero sim impact. Revisit if per-cell granularity is needed (directional scan shadow,
partial cave reveals).

**When implementing, changes needed:**
- Add `CellKnowledge` enum + scan state map to Renderer
- Add `knowledge` parameter to `cell_to_rgba` (three code paths)
- Add `false_color()` functions per tier
- Switch `BlendState::REPLACE` → `BlendState::ALPHA_BLENDING` for scanned cells
- BFS distance-field computation during `update_chunk`

---

## File inventory

```
crates/render/
├── Cargo.toml         — deps: wgpu 24, winit 0.30, bytemuck 1, pollster, log, env_logger
└── src/
    ├── lib.rs         — Renderer struct, wgpu init, pipeline, chunk textures (515 lines)
    ├── camera.rs      — Camera struct, ortho projection, viewport culling
    └── cell_color.rs  — cell_to_rgba, cells_to_rgba, palette logic, 6 tests

assets/shaders/
└── chunk.wgsl         — vertex + fragment shader for chunk quads

crates/app/src/
└── main.rs            — winit event loop, input handling, sim↔render wiring
```
