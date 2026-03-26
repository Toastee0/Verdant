# Sim Crate — Progress

**Date:** 2026-03-25
**Author:** Sim agent (Claude)
**Status:** Water cycle complete. Worldgen, vehicles, plants not yet started.

---

## What's done

### 1. Cell encoding (`crates/sim/src/cell.rs`)

- 16-byte `#[repr(C)]` named struct — ABI-compatible with C
- Three layers: physics (water/mineral/temperature/vector), biology (species/tile_type/growth/energy), light (light/sunlight/_pad)
- Root reference fields (root_row/root_col i16) for plant body tracking
- All state derived from continuous values — no discrete material enum
- Preset constructors: `new_water()`, `rock()`, `loose_soil()`, `steam()`, `ice()`, `lava()`, `dust()`, `mud()`, `air(moisture)`
- Derived predicates: `is_air()`, `is_liquid()`, `is_vapor()`, `is_ice()`, `is_solid()`, `is_powder()`, `is_molten()`, `is_plant()`, `is_active()`
- `density()` = mineral×3 + water (integer, no floats)
- i4 nibble velocity encoding: `encode_i4`, `decode_i4`, `make_vector`, `dx()`, `dy()`
- Compile-time size assert: `sizeof(Cell) == 16`
- 8 passing tests

### 2. Chunk system (`crates/sim/src/chunk.rs`)

- `Chunk` struct: 512×512 double-buffered cell grid + GhostRing
- `ChunkCoord { cx, cy }` with `offset()` and `chebyshev()` methods
- `ChunkState` enum: Active / KeepAlive / Dormant
- `IDLE_DAYS_BEFORE_DORMANT = 3`
- `GhostRing`: 4 edge Vecs + 4 corner Cells, defaults to solid rock
- `prepare_tick()` — front→back memcpy, reset has_activity
- `swap()` — O(1) pointer swap
- `get()`, `set_next()`, `get_with_ghost()` — front/back buffer access
- `tick_physics(tick_count)` — calls water::tick
- `tick_daily()` — stub (plants/creatures/decomp TODO)
- `scan_for_activity()` — any active cell in front buffer
- `fill_rect()` — direct front buffer write for worldgen/tests
- `front_slice()` — `&[Cell]` for GPU texture upload
- 7 passing tests

### 3. Boundary / ghost ring (`crates/sim/src/boundary.rs`)

- Two-phase design (borrow-safe): collect (immutable) → apply (mutable)
- `GhostData` struct holds 4 edge Vecs + 4 corners
- `collect_ghost_data(coord, &HashMap)` — reads neighbor edges, returns rock for unloaded neighbors
- `apply_ghost_data(&mut Chunk, GhostData)` — writes to chunk's ghost ring
- 3 passing tests

### 4. Chunk manager (`crates/sim/src/chunk_manager.rs`)

- `ChunkManager { chunks: HashMap<ChunkCoord, Chunk>, player_chunk, active_radius, tick_count }`
- Discovery: `set_player_chunk()` → `discover_active_zone()` + `refresh_chunk_states()`
- Chunk generation via `generate_chunk()` — **currently a stub (empty air)**
- `tick_high_frequency()`: collect→ghost→physics→swap pipeline; two-phase ghost update
- `tick_daily_pass()`: biology tick, activity rescan, idle_days increment, eviction via `retain()`
- `iter_chunks()` for renderer
- 5 passing tests

### 5. Water simulation (`crates/sim/src/water/`)

Split into 6 modules. All rules are cellular automata — local neighborhood, no global solves.

```
mod.rs        — tick() entry, process_cell() dispatch, liquid_spread(), tests
gravity.rs    — rise() (vapor buoyancy), liquid_fall(), powder_fall() (returns bool)
pressure.rs   — try_pressure_relief(), has_pressure_from_above() (DF-style)
diffusion.rs  — moisture_diffuse() (humidity gradients through air)
absorption.rs — soil_absorb() (returns bool), is_absorbent_soil()
transfer.rs   — write_swap(), equalize_water(), can_receive_water()
```

**Implemented behaviors:**
- Liquid gravity (falls through less-dense cells)
- Vapor buoyancy (rises through denser cells)
- Powder gravity + diagonal slide (returns bool — settled powder can absorb)
- ONI-style horizontal equalization (communicating vessels over multiple ticks)
- DF-style pressure relief (saturated+pressurized → pushes to orthogonal relief)
- Moisture diffusion through air (1/8 gradient per tick)
- Soil capillary absorption (wicks water from wet neighbors when settled)
- Ice: frozen water skips all movement rules
- Spread direction alternates each tick (Noita bias elimination)

**8 passing tests:** fall, settle, spread, pressure U-tube, soil absorption, freeze, vapor rise, water_settles_at_bottom

### Total: 31/31 tests passing

---

## Current state of generate_chunk()

```rust
// crates/sim/src/chunk_manager.rs
fn generate_chunk(coord: ChunkCoord) -> Chunk {
    let _ = coord;
    Chunk::new(coord)  // empty air — stub
}
```

Every chunk is empty air. No terrain, no rock, no caves. The water sim works correctly
but there is nothing for it to interact with. Worldgen is the highest-priority next task.

---

## What's not started

### Worldgen — highest priority
Full spec in `HANDOFF_SIM_WORLDGEN.md`. Summary:
- Surface heightmap (1D Perlin)
- Rock/soil layer fill by depth
- Cave carving (2D noise threshold)
- Machine pocket at origin depth (~600 cells)
- Ore placement (depth-gated noise clusters)
- Temperature gradient (ambient + geothermal noise)
- Horizontal sector variation (volcanic/aquifer/mineral/debris zones)
- POI placement (probability roll per chunk per sector)
- Lava core sentinel (chunks below LAVA_CORE_DEPTH_CHUNKS)
- Dependency to add: `noise = "0.9"` to `crates/sim/Cargo.toml`

### Temperature diffusion
Hot cells slowly heat neighbors. Drives convection, lava spread, geothermal zones.
Same pattern as moisture_diffuse() — small module, clean addition to water/diffusion.rs
or a new `temperature.rs`. Unblocked — can implement anytime.

### Particle physics
The `vector` byte exists on every cell but nothing reads it for movement.
Needed before ballistic projectiles can work.
Pattern: each tick, cell with non-zero vector moves one step in dx/dy direction,
displacing what's there (density comparison, same as liquid_fall).

### Pod physics
Solar Jetman: 16-angle thrust, constant gravity, spring tow (k=0.000488), fuel consumption.
Pod needs a representation in sim — entity position + state, separate from the cell grid.
Full spec in GDD.md (vehicles section) and HANDOFF_RENDER_GAMEPAD.md (controls).

### Tank physics
Wheeled movement, limited slope climbing, ballistic arm.
Ballistic arm: fires cell-state payloads on arc trajectory.
Full projectile taxonomy in GDD.md (ballistic arm section).

### Plant growth system
Full spec in `HANDOFF_SIM_PLANTS.md`. Summary:
- Energy model per tile type (ROOT absorbs water, LEAF photosynthesizes, FUNGUS eats organic)
- Body connectivity via daily BFS from root tile
- Detachment logic: cut cells propagate if sufficient resources, else die
- SpeciesTemplate struct for species definitions
- First three species: Cave Moss (id=1), Cave Fern (id=2), Surface Grass (id=3)

### Creature system (Lotka-Volterra)
Not yet designed in detail. Predator-prey population dynamics.
How creatures are represented (cell entities? external position list?) TBD.

---

## Architecture notes for next sim work

**Two-phase ghost update** is borrow-safe but verbose. If adding new cross-chunk systems,
follow the same collect→apply pattern in chunk_manager.rs.

**Daily pass has ChunkManager access** — use this for expensive operations like plant
connectivity BFS. Per-frame tick only has access to one chunk at a time.

**has_activity flag**: set `chunk.has_activity = true` whenever any rule fires.
This keeps KeepAlive chunks alive. If you add new simulation rules, make sure
active cells set this flag or the chunk will go dormant prematurely.

**Powder-before-absorption ordering**: powder cells call `powder_fall()` first.
If it returns false (settled), `is_absorbent_soil()` is checked. This ordering
is intentional — falling powder doesn't absorb mid-flight.
