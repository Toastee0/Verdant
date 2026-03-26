# Verdant — Claude Context

## Project
Pixel-simulation terraforming game. An ancient machine crashes into a dead planet
and buries itself deep in the impact crater. From underground, it begins: generating
pods to explore, tanks to terraform, and you to pilot them. Mine materials, seed life
from the deepest caves up to the surface. No enemies. The planet starts dead — you
make it breathe.

**Author:** Adrian Neill
**Status:** Pre-production
**Lineage:** Terrarium prototype (React/JS, proven water cycle) → this

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

### World: infinite chunked grid

The world is an infinite 2D grid of chunks. Each chunk is 512×512 cells.

```
World coords  (wx, wy):  any i32 — global pixel position
Chunk coords  (cx, cy):  (wx / 512, wy / 512)
Local coords  (lx, ly):  (wx % 512, wy % 512) — position within chunk
```

**Discovery rule:** Chunks do not pre-generate. A chunk enters existence only
when the player enters its Chebyshev range. Undiscovered chunks are not in
memory at all — they contribute rock-default ghost cells to loaded neighbors.
"The world is frozen in geological time until you arrive."

### Chunk states

```
Active    — within active_radius of player; ticked every sim step
KeepAlive — off-screen but has active biology/water; keeps simulating
Dormant   — no activity; evicted after IDLE_DAYS_BEFORE_DORMANT daily passes
            (serialized to disk on eviction — TODO)
```

Activity is auto-detected: any cell with moving particles, flowing water, or
living biology marks its chunk has_activity = true. No explicit registration.

### Cross-chunk stitching: ghost ring

Before each physics tick, boundary.rs copies the outermost row/column of each
neighboring chunk into a GhostRing on the target chunk. Sim rules call
`get_with_ghost(lx, ly)` for all neighbor lookups — out-of-bounds coords
transparently return ghost cells. Rules never special-case chunk edges.

Unloaded (undiscovered) neighbors contribute solid rock ghost cells — correct
for geological boundary behavior.

### Simulation frequencies

**Per-frame (every tick) — high frequency:**
- Water cycle: evaporation, convection, nucleation, gravity, absorption, capillary
- Particle physics: vector-based movement, collision, displacement
- Lighting (GPU compute pass only — not CPU sim)

**Daily pass (once per in-game day, ~30 real minutes) — low frequency:**
- Plant body plan advancement
- Creature state machines
- Population dynamics (Lotka-Volterra)
- Biomass harvesting
- Soil enrichment from decomposition
- Keep-alive re-evaluation / dormancy transitions

The daily pass runs during the sleep intermission (player rests at base, time
advances, player wakes to results). It can be expensive since it's not on the
frame budget. All KeepAlive chunks get the full daily pass even if off-screen —
this is how distant ecosystems keep running.

### CPU sim, GPU rendering only

Physics runs on CPU (Noita approach: ghost ring stitching, checkerboard
parallel scheduling — even-parity chunks first, then odd-parity).

GPU is used for:
- Rendering: each visible chunk uploads front_slice() as a 512×512 texture
- Lighting pass: GPU compute shader only (not sim physics)

The fragment shader reads cell value ratios (water/mineral/temperature) and
derives color/opacity. No discrete material lookup table.

---

## Cell encoding — u32 per field, 16 bytes total, C-compatible (#[repr(C)])

```
Offset  Field        Type   Description
──────  ───────────  ─────  ──────────────────────────────────────────────────
0       water        u8     Water/moisture content (0=bone dry, 255=saturated)
1       mineral      u8     Mineral density (0=vacuum, 255=dense hard rock)
2       temperature  u8     Thermal state (0=frozen, 128=ambient, 255=molten)
3       vector       u8     Velocity: hi nibble=dx(i4,-8..+7), lo=dy(i4,-8..+7)
4       species      u8     0=inorganic; 1-255=species ID
5       tile_type    u8     TILE_AIR/ROOT/STEM/LEAF/FLOWER (only if species>0)
6       growth       u8     Growth stage or general vitality (0=seed, 255=mature)
7       energy       u8     Stored energy (0=depleted, 255=thriving)
8-9     root_row     i16    Absolute row of this plant's root tile
10-11   root_col     i16    Absolute col of this plant's root tile
12      light        u8     Computed light level (0=dark, 255=full brightness)
13      sunlight     u8     Direct sunlight (unobstructed path to sky)
14-15   _pad         u16    Explicit padding; sizeof(Cell) == 16 guaranteed
```

**No discrete material type tag.** Physical behavior and rendering are derived
from the ratios of water/mineral/temperature, not from a type enum:

```
high water + low mineral + high temp  → steam / vapor
high water + low mineral + low temp   → liquid water
low water  + low mineral              → air / vacuum
high mineral + low water              → dry rock
high mineral + medium water           → wet soil / mud
high mineral + high temp              → lava
high water + low mineral + low temp   → ice (when temp < TEMP_FREEZE)
```

All-zero cell = Cell::AIR = vacuum on a cold dead planet. A calloc'd buffer
is a valid empty world.

---

## Worldgen pipeline (planned)

```
1. Geological base layer   — layered noise → rock/soil/ore by depth
2. Cave carving            — worm algorithm; deep caves may be flooded
3. Ore placement           — noise + depth bands → ore deposit seeding
4. Points of Interest      — data-driven POI stencils placed after base gen
```

### POI system (data-driven, JSON templates in assets/data/pois/)

POIs tell the story of the ancient machine's previous attempts ("the planet's
history is written in garbage"). Each chunk can have at most one POI, placed
by a seeded probability roll during generation.

| POI type         | Description                                                    | Depth    | Rarity   |
|------------------|----------------------------------------------------------------|----------|----------|
| CrashedPod       | Wreckage of a previous pod attempt. Contains upgrade fragments.| Surface  | Common near origin |
| OldWaterPump     | Broken machinery. Residual moisture. Repairable for permanent water source. | Mid | Uncommon |
| OvergrownField   | Sealed chamber with established plant colony from prior attempt. Pre-built ecosystem (moss, lichen, cave fern). | Mid-shallow | Rare |
| AncientCistern   | Sealed cavity with preserved water. Breaching releases large water volume. | Deep | Rare |
| ImpactDebris     | Ore-rich crash fragments. May contain salvageable components.  | Shallow  | Common near origin |

---

## Early game progression

**The pod doesn't launch at the start.** The opening sequence is tank-only:

1. You start in the base. The pod requires fuel — you don't have it yet.
2. The tank rolls out across a flat patch of ground beside the base.
3. A rock wall blocks progress. The tank mines it — extracting iron and fuel.
4. Haul ~10 units of iron/fuel back to the base.
5. That's enough to fuel the pod for its first launch.
6. The pod flies over the wall — the game opens up.

**Crashed pod wreckage** (CrashedPod POI) is found near the starting area.
These are previous attempts by the same ancient machine. Scavenging them gives
the first equipment upgrades — you bootstrap using the machine's own failed history.

The wall is a literal and metaphorical gate: you can see past it but can't reach
it until you've earned the fuel. Discovery IS the trigger.

---

## Reference DNA

| Game | What Verdant takes | What Verdant leaves |
|------|-------------------|---------------------|
| **Solar Jetman** | 16-angle thrust physics, tow cable spring mechanics, fuel as constraint | Combat, enemies |
| **Scorched Earth** | Ballistic trajectory tools for the tank (aiming, arc, impact physics) | PvP, destruction-as-goal |
| **Noita** | Pixel simulation, falling sand water cycle, CPU sim + GPU render split, chunk architecture | Roguelike death loop, combat focus |
| **Lemmings** | Emergent ecosystem behavior, agents following simple rules producing complex results | Puzzle framing, player-directed agents |
| **Terraria** | 2D side-scroll world, mining loop, base-building, progression through depth | Combat, boss gates, power fantasy |
| **Metroid** | Ability-gated exploration (areas visible but unreachable), backtracking with new capabilities reveals old-area secrets, environmental storytelling with zero dialogue, isolated atmosphere (world existed before you and doesn't care), base as save-room sanctuary, map connectivity surprises | Combat focus, boss gates, linear critical path, power fantasy escalation. Verdant's gates are **ecological** (need a water cycle, need a plant species, need soil enrichment) not combat-based |
| **Dwarf Fortress** (water sim) | Pressure-as-behavior (not pressure-as-state). No per-cell pressure field. Diagonal gaps break pressure chains as a player-facing mechanic. Lazy evaluation — only process active events. Orthogonal-only propagation. | DF's 1-7 depth levels (Verdant uses 0-255 for finer granularity) |
| **Oxygen Not Included** (gas sim) | Single substance per cell stored as mass/amount. Density-based stratification (light gases rise, heavy sink). Cellular automata, per-tile local rules — NOT Navier-Stokes. Gradual mass equalization over multiple sim cycles. Temperature separate from density. | ONI's fixed density hierarchy (Verdant derives density from water+mineral values continuously) |

---

## Fluid system design — DF + ONI synthesis

The prototype uses simple gravity + flow + nucleation. Production needs proper
hydrostatic pressure: communicating vessels, water seeking its own level.

Three fluid subsystems, all cellular automata (per-tile, local neighborhood, no global solves):

### 1. Water (DF-inspired)

The `water` byte (0-255) is the amount of water in a cell. **Pressure is not stored — it is a behavior rule.**

**Pressure-as-behavior (key DF insight):**
When a saturated cell (water=255) has a full-water source above it and nowhere to fall, the sim traces orthogonally through connected saturated cells to find the first cell with room. Water is pushed there. This is pressure without a pressure field.

Diagonal gaps break pressure chains — orthogonal-only traversal means diagonally adjacent cells don't participate. This is the primary player-facing pressure control mechanism (build a diagonal gap to break a pressure seal).

U-tube equalization happens over multiple ticks as the pressure signal propagates along connected saturated cells. Not instant (unlike a global flood-fill), but correct.

**Target behaviors:**
- Lake connected to a U-passage → water rises to match lake height on both sides
- Sealed pressurized cave → floods when wall breaks (all adjacent cells receive water simultaneously)
- Narrow straw → water rises through it if lake surface is higher than exit

### 2. Air / Gas (ONI-inspired)

Air cells: `water` byte = moisture/humidity. `mineral` ≈ 0.

**Density-based stratification:** the `density()` function (mineral×3 + water) determines which cells rise and which sink. A dense, humid air cell sinks through lighter dry air. Vapor (hot moist air) rises because its `density()` is lower than surrounding cells.

**Gradual equalization:** moisture diffuses from high-moisture air cells to neighboring low-moisture cells at a slow rate. This creates the atmospheric moisture gradient naturally — no global diffusion solve needed.

**Overpressure:** sealed gas pockets don't accumulate infinitely. When a cell is fully saturated and cannot equalize, has_activity drops and the pocket stabilizes.

### 3. Temperature (separate system)

The `temperature` byte drives state transitions only. It does NOT affect density directly (ONI simplification — works well, avoids complex thermodynamics).

Transitions:
- `temp < TEMP_FREEZE` + liquid water → ice (cells stop moving, classified as solid)
- `temp > TEMP_FREEZE` + ice → liquid water (melts)
- `temp >= TEMP_BOIL` + liquid water → vapor (cell rises instead of falls)
- `temp >= TEMP_MELT_ROCK` + rock/mineral → lava (molten rock flows)

Thermal diffusion (planned): hot cells slowly heat neighbors. This drives convection cells in water — hot water rises, cold water sinks — naturally through the same buoyancy rules.

### Implementation notes

- All passes: cellular automata, top-to-bottom scan, double-buffer (read front, write back)
- Spread direction alternates each tick (`tick_count % 2`) to eliminate directional bias
- Powder (dust, loose soil) uses same gravity pass as liquid — same displacement rule
- Cross-chunk pressure: ghost ring must be current before pressure pass runs (already guaranteed by boundary.rs ordering in chunk_manager)

## Ecological tension — invasives and pests

The conflict in Verdant is ecological, not combat. The tank's tools (drill,
water cannon, etc.) are gardening tools that double as pest control.

Tension sources:
- **Invasive plants:** fast-spreading species that crowd out the ecosystem
  you're cultivating. Need weeding, pruning, containment.
- **Pest insects:** consume plants, spread disease, destabilize food chains.
  Population management, not extermination — they're part of the system.
- **Ecological imbalance:** if you over-water, over-plant, or don't manage
  decomposition, the system tips — algal blooms, soil exhaustion, pest booms.

The Lotka-Volterra population dynamics system handles predator-prey balance.
"Hostile" species are just organisms following their own survival rules.
The player is an ecosystem engineer, not a soldier.

---

## Key GDD systems (build order)

1. Pixel-sim water cycle ← **next** (port from React/JS prototype)
2. Solar Jetman pod physics (16 thrust angles, spring tow k=0.000488)
3. Scorched Earth ballistic tank tools
4. Body-plan plant growth (ROOT/STEM/LEAF/FLOWER tile advancement)
5. Lotka-Volterra creature populations
6. Card event system with super item rewards
7. Base upgrade progression (dual tracks: mined ore + biomass)
8. Full worldgen: geological layers, caves, ores, POIs

---

## Running

```sh
# From repo root — use full path on Windows if .cargo/bin not in PATH:
C:/Users/digit/.cargo/bin/cargo build
C:/Users/digit/.cargo/bin/cargo test
C:/Users/digit/.cargo/bin/cargo run --bin verdant
```
