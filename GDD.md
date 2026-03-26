# Verdant — Game Design Document

**Author:** Adrian Neill
**Status:** Pre-production
**Lineage:** Terrarium prototype (React/JS, proven water cycle) → Verdant (Rust)

---

## Elevator pitch

An ancient machine crashes into a dead planet and buries itself deep in the impact crater.
From underground, it begins: generating pods to explore, tanks to terraform, and you to
pilot them. Mine materials, seed life from the deepest caves up to the surface.

No enemies. The planet starts dead — you make it breathe.

---

## Reference DNA

| Game | What Verdant takes | What Verdant leaves |
|---|---|---|
| **Solar Jetman** | 16-angle thrust physics, tow cable spring mechanics, fuel as range constraint | Combat, enemies |
| **Scorched Earth** | Ballistic arc tools, variable payloads (dirt, napalm analog), terrain modification via projectile | PvP, destruction-as-goal |
| **Noita** | Pixel simulation, falling sand water cycle, CPU sim + GPU render split, chunk architecture | Roguelike death loop, combat focus |
| **Lemmings** | Emergent ecosystem behavior, agents following simple rules producing complex results | Puzzle framing, player-directed agents |
| **Terraria** | 2D side-scroll world, mining loop, base-building, progression through depth | Combat, boss gates, power fantasy |
| **Metroid** | Ability-gated exploration (areas visible but unreachable), backtracking with new capabilities, environmental storytelling with zero dialogue, isolated atmosphere, base as sanctuary, map connectivity surprises | Combat focus, boss gates, linear critical path. Verdant's gates are **ecological** — need a water cycle, need a plant species, need soil enrichment — not combat-based |
| **Dwarf Fortress** (water sim) | Pressure-as-behavior not pressure-as-state. No per-cell pressure field. Diagonal gaps break pressure chains. Orthogonal-only propagation. | DF's 1-7 depth levels (Verdant uses 0-255 for finer granularity) |
| **Oxygen Not Included** (gas sim) | Single substance per cell stored as amount. Density-based stratification. Cellular automata, local rules only. Gradual equalization over multiple ticks. | Fixed density hierarchy (Verdant derives density from water+mineral continuously) |

---

## Core aesthetic

**Palette progression:** start grey-black (dead world) → warm browns (geology active) →
blue (water cycle running) → green (first plants) → full color biosphere.

The world doesn't look like a game world. It looks like a dead planet that slowly,
visibly comes alive because of specific things you did.

---

## World architecture

The world is an infinite 2D side-scrolling grid of **cells**. Each cell is 16 bytes.
Cells group into **chunks** of 512×512. Chunks generate only when the player first
enters their range — the world is frozen in geological time until you arrive.

Physics simulation runs on CPU (Noita architecture). Rendering runs on GPU.
The GPU never writes to simulation state.

**Chunk states:**
- **Active** — near the player, ticked every frame
- **KeepAlive** — off-screen but has flowing water / living biology, keeps simulating
- **Dormant** — no activity, serialized to disk and frozen

---

## Cell encoding

No discrete material type. Physical behavior emerges from the ratios of four continuous values:

```
water       u8   0=bone dry, 255=fully saturated
mineral     u8   0=vacuum, 255=dense hard rock
temperature u8   0=frozen, 128=ambient, 255=molten
vector      u8   velocity: high nibble=dx, low nibble=dy (both signed 4-bit, -8..+7)
```

Derived states:
```
high water + low mineral + high temp  → steam / vapor
high water + low mineral + mid temp   → liquid water
high water + low mineral + low temp   → ice
low water  + low mineral              → air / vacuum
high mineral + low water              → dry rock
high mineral + medium water           → wet soil / mud
high mineral + high temp              → lava
```

All-zero cell = vacuum on a cold dead planet. A freshly allocated buffer is a valid empty world.

---

## Fluid simulation

Three cellular-automaton subsystems, all local rules, no global solves:

### Water (DF-inspired)
- `water` byte = amount of water in cell
- Gravity: liquid falls through less-dense cells
- Spread: ONI mass equalization — transfer half the difference per tick toward less-full neighbors. Creates level surfaces over multiple ticks (communicating vessels).
- Pressure: DF-style. Saturated cells under a saturated column search orthogonally for relief and push one unit toward it. U-tube behavior emerges naturally. Diagonal gaps break pressure chains — primary player-facing pressure control.

### Gas / air (ONI-inspired)
- Air cells: `water` byte = moisture/humidity
- Density = mineral×3 + water. Dense humid air sinks through dry air. Hot vapor rises.
- Moisture diffuses laterally from humid cells to drier neighbors at 1/8 gradient per tick.

### Temperature
- Drives state transitions only (freeze/melt/boil/lava). Does not affect density directly.
- Thermal diffusion planned: hot cells slowly heat neighbors, driving convection.

---

## The two vehicles

### Tank (ground)

Slow, precise, powerful. Your geological and ecological workhorse.
Stays on or near the ground. Uses drill + ballistic arm.

**Movement:** wheeled, limited slope climbing. Can be towed by pod over impassable terrain.

**Tools:**
- **Drill** — mines rock, extracts ore, opens passages
- **Ballistic arm** — fires projectile payloads on arc trajectories (Scorched Earth feel)
- **Tow cable** — retrieves objects, pulls debris, anchors to terrain

### Pod (flight)

Fast, fragile, long-range. Your scout and logistics vehicle.
16-angle thrust (Solar Jetman). Always capable of full flight — gates are resource and
environment, not mechanical ability.

**Movement:** free flight in all directions. Fuel limits range from base per launch.

**Controls (NES Solar Jetman, adapted for gamepad):**
- D-pad left/right: rotate facing through 16 angles (smooth, continuous while held)
- D-pad up/down: shield (also cuts tow cable — see below)
- Right trigger: thrust (analog, fires in current facing direction)
- Left trigger: fire defensive shot

**Tow cable — automatic, no button:**
Attaches when pod enters range of a towable object. Spring physics (k=0.000488).
Cut tow by: (1) flying to a drop zone and slowing down, or (2) activating shield.
Shield always cuts tow, even without the shield upgrade. You cannot carry cargo and
defend simultaneously. This is an intentional design constraint.

**Tools:**
- **Tow cable** — auto-attach, lifts cargo from ground to transport
- **Scanner** — reveals surface then subsurface geology as upgrades unlock
- **Defensive weapon** — small shot, starts available, not upgradeable (part of base kit)
- **Payload drop** — later upgrade; dispense seeds/water from altitude

---

## Tank upgrade tree

Two resource tracks: **ore** (mechanical) and **biomass** (biological).
You cannot fully upgrade the tank through mining alone. Building a living ecosystem
unlocks the most powerful tools.

### Drill (ore track)
```
Drill I    — breaks soil and packed dirt (mineral < 140)         starter
Drill II   — breaks rock (mineral < 200)                         ore ×10
Drill III  — breaks hard rock (mineral < 240)                    ore ×30 + POI salvage
```

### Ballistic arm — projectile progression (Metroid beam analog)

The arm fires packets of cell-state change. Every projectile interacts directly with
the pixel simulation — no separate damage system.

```
TIER 1 — Water Squirt (starter, CrashedPod salvage)
  Range: 3 cells. Payload: water += 40 on impact.
  Use: start a trickle, barely softens soil. "The blaster equivalent."

TIER 2 — Water Cannon (ore)
  Range: 8 cells. Payload: water += 120. Leaves a wet trail in air.
  Use: real soil softening, water delivery to dry zones.

TIER 3 — Pressure Shot (ore)
  Same water payload but high velocity (large vector byte).
  Punches through loose soil/powder cells rather than splashing on surface.
  "Passes through certain materials" — Metroid Long Beam analog.

TIER 3 — Freeze Round (ore)
  Payload: water += 100, temperature = below TEMP_FREEZE.
  Cell drops below freeze threshold on impact → becomes ice.
  Use: seal leaks, bridge gaps, freeze mud flows in place.

TIER 4 — Mud Lob (ore + biomass)
  Heavy arc trajectory (Scorched Earth lob).
  Payload: water = 200, mineral = 120. Lands as wet soil, dries over time.
  PRIMARY USE: ceiling dissolution. Fire water blobs upward at a rock ceiling.
    Shot 1: ceiling cell water rises to ~40 (still solid — is_solid() requires water < 80)
    Shot 2: ceiling cell water rises to ~80 — is_solid() fails — cell becomes powder — falls
  Two shots to drop a ceiling section. Creates a rhythm.
  "The Varia Suit moment" — first time the player reshapes terrain downward.

TIER 4 — Acid Seep (biomass — fungal species required)
  Payload: water + corrosion flag. Corroded cells lose mineral -= 2 per tick until collapse.
  Use: slow targeted mining without impact radius. Quiet, surgical.

TIER 4 — Seed Slurry (biomass — any plant × 50 biomass harvested)
  Payload: water + embedded seeds. On impact with soil: plant roots immediately.
  Use: inaccessible planting zones, seed delivery to caves the tank can't reach.
```

### Other tank upgrades
```
Water Tank II/III    — doubles/triples water cannon capacity        ore track
Tow Cable II         — extend reach to 8 cells                     ore track
Soil Enhancer        — tank lays enriched soil tiles behind it      biomass: moss species
Mycorrhizal Drill    — biological agent softens rock in a radius    biomass: fungal species
                       before drilling. Drill III equivalent range
                       achieved at half the ore cost if fungi active.
Root Sonar           — detect cave geometry by sensing root tip     biomass: established
                       pressure differentials. Short range scan.    root network nearby
```

---

## Pod upgrade tree

Pod is always flight-capable. Upgrades extend what's reachable and what can be carried.

### Fuel (range gate)
```
Fuel Tank I   — baseline. ~3-4 chunk radius from base.             starter
Fuel Tank II  — extended range, deeper exploration viable          ore ×15
Fuel Tank III — long-range, can reach far POIs in one flight       ore ×40
Biopropellant — 30% efficiency gain from fermented plant matter    biomass: fermentation
                                                                   chain at base
```

### Tow (load gate)
```
Tow I    — small ore chunks, seeds, light salvage                  starter
Tow II   — machinery fragments from CrashedPod POIs               ore ×10
Tow III  — bulky cargo. Can tow the tank over impassable terrain.  ore ×25
           This is a major unlock — tank gains access to any
           terrain the pod can reach.
Tow IV   — living cargo: transplant root balls between biomes,     biomass ×200
           carry sealed water containers, move ecosystem components
```

### Environmental traversal (resilience gates)

The pod can always attempt these — upgrades make them cheap instead of punishing.

```
Waterfall traversal
  Without upgrade: waterfall (high-velocity downward water cells) pushes pod off
  course, burns triple fuel fighting the current.
  Hull Plating (ore): water impact no longer drains fuel. Cross waterfalls freely.
  Thrust II (ore): enough counter-force to punch through fast.

Geothermal vent
  Rising hot vapor cells create upward force.
  Without upgrade: dangerous, burns hull.
  Thermal Shielding (ore): safe to enter. Can exploit updraft for free altitude gain.
  "The world helps you once you understand it."

Narrow passages
  Compact Frame (ore): reduces pod collision hitbox. Access tight cave systems.

Acid mist
  Above certain lava/chemical pools, corrosive vapor.
  Acid Hull (biomass + ore): resist corrosion. Access deep geothermal zones.
```

### Scanner (knowledge gate)

The pod reveals the world. Scanner upgrades determine how much you can see and how deep.

```
Three knowledge states per cell:
  Unknown  — never overflown. Renders as void / black.
  Scanned  — pod has overflown it. Renders as false-color geological overlay.
             Not full fidelity — shape and composition, not exact values.
  Visited  — player/tank physically present. Full rendering.

PASSIVE — Surface scan (always active)
  Pod light illuminates surface terrain as you fly over. Standard lighting pass.

TIER 1 — Shallow Scan (ore)
  Depth: ~5 cells. Reveals: air pockets, cave geometry, rough rock type.
  False-color: white = cavity, grey = rock. No ore highlight yet.
  "You can see there's a void down there. Can't tell how big."

TIER 2 — Ore Scanner (ore)
  Depth: ~15 cells. Adds: warm orange highlight on dense mineral cells (ore veins).
  "The mountain tells you where the iron is."

TIER 3 — Hydrological Scanner (biomass gate — active water cycle in 3+ chunks)
  Depth: ~30 cells. Adds: cool blue for underground water masses.
  Primary use: finding sealed AncientCistern POIs from altitude.
  "A large blue mass with no surface feature above it."
  Discovery moment: you see it before you know what it is.

TIER 4 — Bio Scanner (biomass gate — root network spanning 2+ chunks)
  Depth: ~15 cells (biology attenuates signal — shorter range than hydro).
  Adds: green pulse on living cells, root network extent, creature clusters.
  Uses the plant network as a sensor mesh — roots feel vibration.
  "The forest tells you what's underneath it."
```

---

## Ecological conflict

There are no enemies. Conflict comes from the ecosystem itself.

- **Invasive plants** — fast-spreading species crowd out the ecosystem you're cultivating.
  Need weeding, pruning, containment. Tank ballistics are your gardening tools.
- **Pest insects** — consume plants, spread disease, destabilize food chains.
  Population management, not extermination — they are part of the system.
- **Imbalance** — over-water, over-plant, neglect decomposition: algal bloom, soil
  exhaustion, pest boom. The system tips.

Population dynamics: Lotka-Volterra predator-prey. "Hostile" species follow survival rules.
The player is an ecosystem engineer.

---

## Early game sequence

1. **Start underground.** The base is buried. Pod requires fuel you don't have.
2. **Tank rolls out** across a flat patch beside the base. Low power, limited range.
3. **Rock wall blocks progress.** The tank mines it — extracting iron and fuel cells.
4. **Haul ~10 units** of iron and fuel back to base.
5. **Pod fuels up** for its first launch. Game opens up.
6. **CrashedPod wreckage** (CrashedPod POI) is found near the origin. Previous attempts
   by the same ancient machine. Scavenging gives the first equipment upgrades.
   You bootstrap using the machine's own failed history.

The wall is a literal and metaphorical gate: you can see past it but can't reach it
until you've earned the fuel. Discovery IS the trigger.

---

## Progression arc (macro)

```
Phase 1 — Geology
  Tank-only. Mine ore. Fuel the pod. Discover the surface.
  World is grey and dead. No water cycle yet.

Phase 2 — Hydrology
  Pod operational. Find water sources (AncientCistern, underground aquifer).
  Establish water cycle: surface pools, waterfalls, moisture in air.
  World gains blue. First soft soils appear.

Phase 3 — Biology
  First plant species seeded. Root networks begin.
  Ecological gates start unlocking (Mycorrhizal Drill, Bio Scanner, etc.)
  World gains green in patches.

Phase 4 — Ecosystem
  Multiple species established. Predator-prey dynamics active.
  Invasives and pests become a management problem.
  Full biosphere palette. Surface is alive.

Phase 5 — ???
  The machine's original purpose. What was it trying to do?
  (Story TBD.)
```

---

## Points of Interest (POI system)

One POI per chunk maximum. Placed by seeded probability roll during worldgen.
POIs tell the story of the machine's previous attempts — the planet's history is written in garbage.

| POI | Description | Depth | Rarity |
|---|---|---|---|
| CrashedPod | Wreckage of a previous pod. Contains upgrade fragments. | Surface | Common near origin |
| OldWaterPump | Broken machinery. Residual moisture. Repairable for permanent water source. | Mid | Uncommon |
| OvergrownField | Sealed chamber with established plant colony from prior attempt. Pre-built ecosystem (moss, lichen, cave fern). | Mid-shallow | Rare |
| AncientCistern | Sealed cavity with preserved water. Breaching releases large water volume. Found via Hydrological Scanner. | Deep | Rare |
| ImpactDebris | Ore-rich crash fragments. May contain salvageable components. | Shallow | Common near origin |

---

## Key systems build order

```
1. Pixel-sim water cycle          ← DONE (water/ module, 31/31 tests passing)
2. Solar Jetman pod physics       (16 thrust angles, spring tow k=0.000488)
3. Scorched Earth ballistic arm   (arc, payload cell-state injection on impact)
4. Body-plan plant growth         (ROOT/STEM/LEAF/FLOWER tile advancement)
5. Lotka-Volterra creature pops   (predator-prey population dynamics)
6. Card event system              (super item rewards)
7. Base upgrade progression       (dual tracks: ore + biomass)
8. Full worldgen                  (geological layers, caves, ores, POI stencils)
9. Scanner system                 (per-chunk scan depth, false-color overlay)
```

---

## Running

```sh
# From D:\verdant
C:/Users/digit/.cargo/bin/cargo build
C:/Users/digit/.cargo/bin/cargo test
C:/Users/digit/.cargo/bin/cargo run --bin verdant
```
