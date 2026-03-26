# For the sim agent — worldgen

This document specifies the procedural world generation system.
Read GDD.md first for full context. Read CLAUDE.md for cell encoding and architecture.

---

## World topology

The world is a large rectangle of chunks that **wraps horizontally**.
Chunk coordinate `cx` wraps at `WORLD_WIDTH_CHUNKS` — chunk (-1, y) == chunk (WORLD_WIDTH_CHUNKS - 1, y).

Vertically the world is bounded:
- Top: sky / atmosphere (open air, cy < 0)
- Bottom: lava core (cy >= LAVA_CORE_DEPTH_CHUNKS — not simulated, render only)

Suggested dimensions (tunable):
```
WORLD_WIDTH_CHUNKS:      128   (65,536 cells wide — about 5 minutes of pod flight to loop)
LAVA_CORE_DEPTH_CHUNKS:   16   (8,192 cells deep from surface)
MACHINE_DEPTH_CELLS:     600   (machine is buried ~600 cells below surface, ~1.2 chunks)
```

Chunk (0, 0) is the origin. Surface is approximately at world y = 0. Machine pocket is
at approximately world y = MACHINE_DEPTH_CELLS. Sky chunks have cy < 0.

---

## Depth profile (vertical)

At any given x, the column from sky to lava core looks like this.
All depths are approximate — noise offsets each boundary ±20-50 cells.

```
cy < 0        SKY
              Air cells. Temperature drops with altitude (thin atmosphere).
              Cold, dry, dead. The pod can fly here after surface breakthrough.

y = 0         SURFACE
              Terrain heightmap. Rock/soil exposed. Dry. No life at worldgen.
              Determined by 1D surface noise (see below).

y = 0–80      SURFACE SOIL LAYER
              mineral: MINERAL_SOIL (80) to MINERAL_DIRT (140)
              water:   0–WATER_TRACE (bone dry at worldgen)
              Caves start appearing here as small pockets.

y = 80–300    ROCK LAYER
              mineral: MINERAL_ROCK (200) ± noise
              Larger cave systems. First ore deposits.
              Temperature: ambient (128) rising slowly toward bottom.

y = 280–450   IMPACT COMPRESSION ZONE  (near origin only, fades with distance)
              The rock the machine punched through. Harder than normal.
              mineral: MINERAL_HARD (240) — dense, barely crackable with Drill I
              Slightly elevated temperature from impact heat.
              Few caves (compressed shut by impact force).
              Contains the machine pocket (see below).

y = 300–550   DEEP ROCK
              mineral: MINERAL_HARD (240)
              Rare deep ore pockets. AncientCistern POIs possible.
              Temperature rising: 160–200 range.
              Larger caves again — geological age, not impact-formed.

y = 500+      GEOTHERMAL ZONE
              temperature: 200–240, approaching TEMP_MELT_ROCK (240)
              Lava veins. Steam pockets. Unique heat-ores.
              Caves flooded with hot water or lava.

cy >= 16      LAVA CORE
              Not simulated. Render agent renders as solid lava (mineral=255, temp=255).
              ChunkManager should not generate chunks here — return a sentinel.
```

---

## The machine pocket

A hand-authored open space at approximately (cx=0, cy=1, local_y ≈ 80).
This is where the player starts. The ChunkManager generates this chunk with a
pre-carved pocket instead of running normal noise generation.

```
Shape: ellipse, approximately 40 cells wide × 20 cells tall
Contents: Cell::AIR (all-zero)
Walls: MINERAL_HARD (impact-compressed rock)
Temperature: slightly elevated (150) from residual impact heat

The machine itself will be represented as a structure placed in this pocket.
For now: leave the pocket as open air. The machine structure is future work.
```

All chunks above the machine pocket (same cx, lower cy) are in the impact
compression zone — hard rock, almost no caves, requires Drill III to move through
quickly. This is intentional: reaching the surface is the early game.

---

## Noise stack

Use a seeded deterministic noise function. Seed for chunk (cx, cy):
```rust
fn chunk_seed(cx: i32, cy: i32, world_seed: u64) -> u64 {
    // Simple but good enough — hash the coordinates
    let cx = cx as u64;
    let cy = cy as u64;
    world_seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(cx.wrapping_mul(2654435761).wrapping_add(cy.wrapping_mul(1013904223)))
}
```

Use `noise` crate (add to sim Cargo.toml: `noise = "0.9"`).
`Fbm<Perlin>` with 2-3 octaves works well for all passes below.

### Pass 1 — Surface heightmap (1D, per x column)

```rust
// For each world x, the surface y (where air → rock transition happens):
fn surface_height(wx: i32, noise: &impl NoiseFn<f64, 2>) -> i32 {
    let n = noise.get([wx as f64 * 0.003, 0.0]); // low frequency
    (n * 60.0) as i32  // ±60 cells variation around y=0
}
```

Near origin (wx within ±200 cells): flatten toward 0. The machine's area should
be relatively flat — no dramatic hills right at start.

### Pass 2 — Layer boundary offsets (2D)

Each layer boundary (soil/rock, rock/deep, etc.) is offset by 2D noise:
```rust
fn layer_offset(wx: i32, wy: i32, noise: &impl NoiseFn<f64, 2>) -> i32 {
    let n = noise.get([wx as f64 * 0.005, wy as f64 * 0.005]);
    (n * 40.0) as i32  // ±40 cells
}
```

### Pass 3 — Cave carving (2D)

Caves are any cell where cave noise exceeds a threshold:
```rust
fn is_cave(wx: i32, wy: i32, noise: &impl NoiseFn<f64, 2>) -> bool {
    // Two-octave cave noise
    let n = noise.get([wx as f64 * 0.008, wy as f64 * 0.008]);
    let threshold = cave_threshold(wy); // deeper = more caves
    n > threshold
}

fn cave_threshold(wy: i32) -> f64 {
    // Surface: very few caves (0.75 = rare)
    // Mid depth: common caves (0.55)
    // Deep: large cave systems (0.45)
    let depth_factor = (wy as f64 / 400.0).clamp(0.0, 1.0);
    0.75 - depth_factor * 0.30
}
```

Suppress caves in the impact compression zone (near cx=0, in y=280-450 band).

### Pass 4 — Ore placement (2D, depth-gated)

```rust
fn ore_at(wx: i32, wy: i32, noise: &impl NoiseFn<f64, 2>) -> Option<OreType> {
    let n = noise.get([wx as f64 * 0.02, wy as f64 * 0.02]);
    match wy {
        80..=250  if n > 0.85 => Some(OreType::Iron),
        200..=350 if n > 0.88 => Some(OreType::Fuel),
        400..=600 if n > 0.92 => Some(OreType::DeepOre),
        _ => None,
    }
}
```

Ore cells: `mineral = MINERAL_HARD, water = 0` (still rock, distinguished by
a future ore_type field or by mineral value above a threshold — TBD with Adrian).

### Pass 5 — Temperature gradient

Temperature is deterministic from depth, plus geothermal noise blobs:
```rust
fn cell_temperature(wy: i32, geothermal_noise: f64) -> u8 {
    let base = TEMP_AMBIENT as f64; // 128
    let depth_heat = (wy as f64 / 600.0).clamp(0.0, 1.0) * 100.0;
    let geo_heat = if geothermal_noise > 0.7 {
        (geothermal_noise - 0.7) * 200.0 // hotspot blobs
    } else { 0.0 };
    (base + depth_heat + geo_heat).clamp(0.0, 255.0) as u8
}
```

---

## Sector biomes (horizontal variation)

The world width is `WORLD_WIDTH_CHUNKS = 128`. Different horizontal regions have
different characters. Use a sector assignment based on `cx`:

```rust
fn sector(cx: i32) -> Sector {
    // Map cx to 0..WORLD_WIDTH_CHUNKS range (handle wrap)
    let cx = cx.rem_euclid(WORLD_WIDTH_CHUNKS as i32) as usize;
    match cx {
        0..=15   => Sector::Origin,       // machine starting zone, mixed geology
        16..=31  => Sector::Volcanic,     // heat, lava near surface, geothermal
        32..=47  => Sector::MineralRich,  // dense ore, hard rock, mining-heavy
        48..=63  => Sector::DeepWater,    // aquifer zone, wet caves, AncientCisterns
        64..=79  => Sector::Debris,       // ancient debris field, CrashedPod POIs dense
        80..=95  => Sector::Volcanic,     // second volcanic band (globe is varied)
        96..=111 => Sector::MineralRich,
        112..=127 => Sector::DeepWater,   // wraps back toward origin
        _ => Sector::Origin,
    }
}
```

Per-sector modifiers applied on top of the base noise:

```
Volcanic:
  cave_threshold -= 0.05 (more open space from gas erosion)
  temperature += 30 everywhere
  lava veins appear at y=300+ (mineral=MINERAL_ROCK, temp=TEMP_MELT_ROCK)
  unique ore: thermal crystals at y=200-400

MineralRich:
  mineral values +20 throughout (harder, denser rock)
  ore density ×1.5 (lower thresholds)
  fewer caves (rock is too dense to erode)

DeepWater:
  water content +20 in soil and rock cells (moist geology)
  cave threshold -0.05 near y=300-500 (aquifer cave systems)
  AncientCistern POI probability ×3
  surface cave pools possible (small water pockets at y=50-100)

Debris:
  CrashedPod POI probability ×4
  Surface is littered with anomalies (surface noise more dramatic)
  More accessible caves (previous pod impacts carved paths)
```

---

## POI placement

POIs are placed after all noise passes. One POI per chunk maximum.

```rust
fn roll_poi(cx: i32, cy: i32, chunk_seed: u64, sector: Sector) -> Option<PoiType> {
    let rng = chunk_seed; // deterministic from coord
    let roll = (rng % 100) as u8; // 0-99

    match sector {
        Sector::Debris   => if roll < 25 { Some(PoiType::CrashedPod) } else { None },
        Sector::Origin   => if roll < 15 { Some(PoiType::CrashedPod) } else { None },
        Sector::DeepWater => match roll {
            0..=5  => Some(PoiType::AncientCistern),
            6..=12 => Some(PoiType::OldWaterPump),
            _ => None,
        },
        Sector::Volcanic => if roll < 8 { Some(PoiType::ImpactDebris) } else { None },
        _ => if roll < 5 { Some(PoiType::OvergrownField) } else { None },
    }
}
```

POIs are placed at valid positions within their chunk (not inside solid rock,
not in the sky, not overlapping the machine pocket). POI stencils are future work —
for now, mark the chunk as having a POI and place a single distinctive cell as placeholder.

---

## `generate_chunk` implementation sketch

Replace the stub in `chunk_manager.rs`:

```rust
fn generate_chunk(coord: ChunkCoord) -> Chunk {
    let mut chunk = Chunk::new(coord);
    let sector = sector(coord.cx);

    // Special case: machine pocket chunk
    if coord.cx == 0 && is_machine_pocket_chunk(coord.cy) {
        return generate_machine_pocket(coord);
    }

    // Special case: lava core
    if is_lava_core(coord.cy) {
        return generate_lava_core(coord);
    }

    // Normal generation
    let world_x0 = coord.cx * CHUNK_WIDTH as i32;
    let world_y0 = coord.cy * CHUNK_HEIGHT as i32;

    for ly in 0..CHUNK_HEIGHT {
        for lx in 0..CHUNK_WIDTH {
            let wx = world_x0 + lx as i32;
            let wy = world_y0 + ly as i32;
            let cell = generate_cell(wx, wy, sector, WORLD_SEED);
            chunk.fill_rect(lx, ly, lx + 1, ly + 1, cell);
        }
    }

    // POI pass
    if let Some(poi) = roll_poi(coord.cx, coord.cy, chunk_seed(coord.cx, coord.cy, WORLD_SEED), sector) {
        place_poi(&mut chunk, poi);
    }

    chunk
}

fn generate_cell(wx: i32, wy: i32, sector: Sector, seed: u64) -> Cell {
    let surf_y  = surface_height(wx, &surface_noise);
    let is_sky  = wy < surf_y - 10;
    let is_deep = wy > surf_y + 500;

    if is_sky {
        // Thin atmosphere — trace moisture, cold
        return Cell::new(WATER_TRACE / 4, 0, TEMP_AMBIENT - 20, 0);
    }

    if is_cave(wx, wy, &cave_noise) {
        return Cell::AIR;
    }

    let mineral = mineral_at(wx, wy, sector);
    let temp    = cell_temperature(wy - surf_y, geothermal_noise.get([wx as f64 * 0.01, wy as f64 * 0.01]));
    let water   = 0u8; // dry at worldgen — player creates water

    Cell::new(water, mineral, temp, 0)
}
```

---

## What to implement first

1. **Surface heightmap** — gives the world a terrain silhouette. Immediate visual payoff.
2. **Rock/soil layer fill** — everything below surface is rock with a soil veneer.
3. **Cave carving** — instant visual interest, needed for water sim to be interesting.
4. **Machine pocket** — so the player start area is correct.
5. **Temperature gradient** — unlocks lava zones and geothermal sector.
6. **Ore placement** — needed for progression.
7. **Sector variation** — adds horizontal diversity.
8. **POI placement** — last, since it requires stencil work.

---

## Dependencies to add to `crates/sim/Cargo.toml`

```toml
noise = "0.9"
```

The `noise` crate provides `Perlin`, `Fbm`, and the `NoiseFn` trait.
It is pure Rust, no C deps, WASM-portable — fits the sim crate constraints.
