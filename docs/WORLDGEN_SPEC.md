# Verdant — Worldgen Current State Spec

*Conversation starter for level generation design. Written from the code, not the spec.*
*Last updated: 2026-03-25*

---

## 1. What's Built — `generate()` pipeline

Entry point: `crates/sim/src/worldgen/mod.rs::generate(coord)`
Called once per chunk on first player discovery. Deterministic: same coord → same output.

### World seed
```
WORLD_SEED = 12345  (hardcoded, no save-file hookup yet)
```

### 5 noise passes (all Fbm<Perlin>, 2 octaves each)

| Pass | Frequency | Scale | Purpose |
|------|-----------|-------|---------|
| `surface` | 0.003 | ±60 cells | 1D heightmap (surface y) |
| `layers` | 0.005 | ±40 cells | Layer boundary wobble |
| `caves` | 0.008 | threshold | Cave carving |
| `ore` | 0.020 | threshold | Ore pocket placement |
| `geothermal` | 0.010 | 0–1 | Heat blob spots |

Seeds are derived from WORLD_SEED + integer salts (0–4), so all passes are uncorrelated.

### Generation flow per cell

1. Compute `surface_y` from 1D surface noise at wx
   — flattened to 0 within ±200 cells of origin (flat start area)
   — variation: ±60 cells elsewhere
2. Sky check: `depth < -10` → thin atmosphere cell (cold, trace water)
3. Surface transition: `depth -10 to 0` → partial soil blend
4. Cave check: `noise.is_cave(wx, wy, depth, near_impact)` → Cell::AIR if true
5. Layer profile → base `(mineral, temp)` values
6. Sector modifiers applied on top
7. Ore check → may override mineral to MINERAL_HARD
8. Lava vein check: if `temp >= TEMP_MELT_ROCK (240)` → zero water

### Special-case chunks (bypass normal generation)

**Lava core** (`cy >= 16`): fills chunk with `{mineral: 255, temp: 255}`. Not simulated.

**Machine pocket** (`cx == 0, cy == 1`):
- Runs normal geology first
- Then carves an ellipse centered at local `(256, 80)` → world y ≈ 592
- Ellipse: half-width=20, half-height=10 (40×20 cells of air)
- Walls within 1.3× ellipse radius: MINERAL_HARD at temp 150 (impact-compressed)

---

## 2. World Topology

```
128 chunks wide (65,536 cells). Wraps horizontally.
Vertical: cy < 0 = sky, cy 0-15 = simulated world, cy >= 16 = lava core sentinel
Each chunk: 512×512 cells. Each cell: 16 bytes.
```

### Depth profile (depths are below surface_y, not absolute cy)

```
depth < -10       SKY
                  Cold atmosphere. Temp drops with altitude (up to -60 from ambient).
                  Trace moisture if within 40 cells of surface.

depth -10 to 0    SURFACE TRANSITION
                  Partial soil: mineral lerps from 0 toward MINERAL_SOIL.

depth 0–80        SURFACE SOIL
                  mineral: MINERAL_SOIL(80) → MINERAL_DIRT(140)  [lerp]
                  ±40 cell boundary wobble from layer noise
                  temp: TEMP_AMBIENT(128)
                  Caves: threshold 0.75 (rare small pockets)

depth 80–300      ROCK LAYER
                  mineral: MINERAL_ROCK(200)
                  temp: 128 → ~138 (slow rise: +10 over 220 cells)
                  Caves: threshold 0.75 → 0.60 (increasing)
                  Ore: Iron band starts at depth 80

depth 280–450     IMPACT COMPRESSION ZONE  (|wx| < 1024 only)
                  mineral: MINERAL_HARD(240)
                  temp: TEMP_AMBIENT + 22 = 150
                  Caves: SUPPRESSED (crushed shut)
                  Contains machine pocket at approx depth 592

depth 300–550     DEEP ROCK
                  mineral: MINERAL_HARD(240)
                  temp: 128 → ~188 (+60 over 250 cells)
                  Caves: threshold 0.60 → 0.45
                  Ore: Fuel band (200-350), Deep ore (400-550)

depth 550+        GEOTHERMAL ZONE
                  mineral: MINERAL_HARD(240)
                  temp: 188+ rising, geothermal hotspot blobs push toward 240+
                  If temp >= TEMP_MELT_ROCK(240): lava vein (no water)

cy >= 16          LAVA CORE SENTINEL
                  mineral=255, temp=255. Render-only. Never simulated.
```

### Cave threshold curve
```
depth   0   →  threshold 0.75  (rare)
depth 400   →  threshold 0.45  (large systems)
Intermediate: linear interpolation
```

---

## 3. Sector Biomes

8 sector bands, repeating twice around the globe:

| cx range | Sector | Cell modifier |
|----------|--------|---------------|
| 0–15 | **Origin** | None |
| 16–31 | **Volcanic** | temp +30 everywhere; if depth > 300 and geo_noise > 0.6: temp = TEMP_MELT_ROCK(240) → lava vein |
| 32–47 | **MineralRich** | mineral +20 (saturating) |
| 48–63 | **DeepWater** | water +20 in any cell with mineral >= MINERAL_SOIL |
| 64–79 | **Debris** | No cell modifiers (cave density tuning noted in spec but not wired in) |
| 80–95 | **Volcanic** | (same) |
| 96–111 | **MineralRich** | (same) |
| 112–127 | **DeepWater** | (same) |

### Ore thresholds by sector
```
Sector        Iron (d80-250)   Fuel (d200-350)   Deep (d400-600)
Origin        > 0.85           > 0.88            > 0.92
MineralRich   > 0.82           > 0.85            > 0.89   (-0.03 bonus)
All others    > 0.85           > 0.88            > 0.92
```

**Note:** Ore cells currently just get `MINERAL_HARD` — there's no separate `ore_type` field. Iron / Fuel / Deep Ore are depth-band concepts only; they're not tagged in the cell data.

---

## 4. Color Mapping — current `cell_to_rgba`

`crates/render/src/cell_color.rs` — CPU-side, called 262,144× per visible chunk per frame.

### Base mineral/water colors
```
void/air                   →  near-black (8, 8, 13)
low mineral (soil)         →  warm brown  rgb(89, 71, 56)
high mineral (hard rock)   →  cool grey   rgb(128, 128, 140)
water (pure, no mineral)   →  blue        rgb(20, 89, 217)
wet soil                   →  darker version of soil brown
```

Blending is continuous — mineral value drives a lerp from soil-brown to rock-grey. Water visibility is suppressed by mineral (barely shows through solid rock).

### Temperature overrides
```
ice  (temp < 64,  water >= 150)   →  pale blue-white  rgb(191, 217, 242)
steam (temp >= 192, water >= 150) →  pale blue-white  rgb(217, 230, 255)
lava  (temp >= 240, mineral >= 140) → orange-red     rgb(255, 89, 13)
heat glow (temp > 180)            →  +15% red, -10% green tint
```

### Biology colors (future state, wired but worldgen doesn't place plants yet)
```
roots    →  dark brown
stems    →  green-brown
leaves   →  vivid green, brightens with energy
flowers  →  cycles through 6-color palette: red/yellow/pink/orange/purple/white (by species % 6)
dying    →  desaturates toward grey-brown
```

### Lighting
Lighting pass not yet implemented. Fallback: full brightness on all cells.

---

## 5. POI System — status

**Specced in `HANDOFF_SIM_WORLDGEN.md`, not yet in the code.**

The spec defines:

| POI | Sector weight |
|-----|---------------|
| `CrashedPod` | Debris: 25%, Origin: 15% |
| `AncientCistern` | DeepWater: 6% |
| `OldWaterPump` | DeepWater: 7% |
| `ImpactDebris` | Volcanic: 8% |
| `OvergrownField` | All others: 5% |

Current implementation: `generate()` has no POI pass. No `roll_poi()`, no placement, no stencils. The machine pocket is hand-authored (not a POI system entry).

---

## 6. Open Questions for Design Review

### Cave systems
- Current density feels right as a baseline but caves are **not connected across chunks** — each chunk's caves are independent noise islands. Deep exploration feels fragmented. Worth adding a large-scale "cave corridor" pass?
- **Debris sector** has no actual modifier — the spec says "more accessible caves" but it's not wired. What should distinguish Debris terrain visually?

### Surface terrain
- ±60 cell variation gives gentle rolling hills. Is this enough visual interest at the surface, or do we want more dramatic topography (cliffs, overhangs, mesas)?
- Surface is bone dry at worldgen. First impression is just grey-brown undulation. Reference images (below) suggest the underground should be significantly more interesting than the surface early on.

### Biome color palettes
Reference images uploaded for art direction:
- **Bioluminescent cave** — teal/purple glowing walls, dark voids
- **Crystal cave** — bright blue/purple crystal formations, high contrast
- **Coral/mushroom cave** — warm pinks and oranges, organic shapes
- **Ice cave** — pale blue, almost white, cold and sparse

Current palette is utilitarian brown/grey. These references suggest each sector could have a distinct visual signature even before life arrives — the geology itself should look different. Questions:
- Should MineralRich have crystal color hints (blue-purple mineral tint)?
- Should Volcanic have visible orange warmth even in unmelted rock?
- Should DeepWater have a wet sheen or blue-green mineral color?
- Should Debris look weathered/rusted (warm brown-red tint)?

### Ore distribution
- Ores are currently **visually identical to hard rock** (both MINERAL_HARD=240). No way to tell ore from rock without a separate data field. Need: either a new `ore_type: u8` cell field, or a color pass that uses depth + mineral combo to suggest "this looks like ore."
- Rarity feels roughly right by the thresholds (top 15%, 12%, 8% of noise range) but untested with actual exploration. Iron may be too common; Deep Ore may be too rare.

### Water table / underground rivers
- Currently nothing — no water anywhere at worldgen. DeepWater sector adds moisture to rock cells but no liquid water pools.
- Spec mentions "surface cave pools at y=50-100" in DeepWater. Worth implementing? This would give the player something to find immediately after first breakthrough.
- Underground rivers crossing chunk boundaries would require the large-scale cave connector pass above.

### POI placement rules
- No stencils exist. What does a CrashedPod look like as a cell pattern? How big? Does it block the cave around it, or sit in open air?
- Density question: 25% per chunk in Debris means almost every chunk has a POI. Is that right, or should it be 1 per N chunks?
- Machine pocket is currently open air — no machine structure. When does the visual representation get built?

### Sector transitions
- Currently hard cuts at chunk boundaries (cx=15→16 flips from Origin to Volcanic instantly).
- Should there be a blend zone? If so, how wide, and which values blend?

### Vertical layering within sectors
- All sectors apply modifiers uniformly across all depths. Could instead have depth-dependent sector colors/feels — e.g., Volcanic could look grey near surface (cooled lava flows) and only show heat deeper.
- DeepWater could have dry surface caves but flooded lower caves.

---

*Code references: `crates/sim/src/worldgen/` (geology.rs, noise.rs, mod.rs), `crates/render/src/cell_color.rs`, `crates/sim/src/cell.rs`*
