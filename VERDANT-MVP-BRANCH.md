# VERDANT — MVP Branch Specification

## Purpose

Prove the core pixel simulation produces emergent behavior. Everything else is layered on top of a sim that either works or doesn't. This branch answers that question.

---

## Tech Stack

- **Language:** Rust
- **Renderer:** wgpu
- **Architecture:** Pixel simulation grid with a separate entity layer on top

---

## Scope — What's IN

### Pixel Simulation Grid

The world is a 2D grid of pixels. Each pixel has:

- **Type:** dirt, rock, gas (O2, CO2, CO), liquid (water), plant
- **Temperature:** per-pixel float
- **Properties per type:**
  - `blocks_movement` — rock: yes, plant: no, gas: no, liquid: no, dirt: yes
  - `blocks_airflow` — rock: yes, plant: yes, gas: no, liquid: yes, dirt: yes
  - `pressure_breakable` — plant: yes (checked 2 tiles deep), everything else: no or extreme threshold
- **Basic diffusion:** gas and temperature spread to adjacent pixels per tick
- **Combustion:** fire pixel consumes O2 tiles, emits CO2/CO, dies when O2 depleted
- **Condensation:** gas-phase water hitting temperature threshold converts to liquid pixel
- **Evaporation:** liquid water above temperature threshold converts to gas pixel
- **Plant growth:** plant pixels spread into adjacent dirt pixels when water and temperature conditions are met

### Pressure System (ONI-style)

- Gas exerts pressure per tile
- Pressure break check: only evaluates up to 2 tiles deep into a wall
- 3+ tiles of any pressure-breakable type: break check never reaches inner tile, effectively holds (but still diffuses slowly)
- Overpressure on outer tile → tile destroyed → cascade inward
- Non-breakable types (rock, metal) never checked for break

### Plant Walls

- Walkable: yes (entities pass through)
- Blocks airflow: yes
- Pressure breakable: yes, checked 2 deep
- 1 tile thick: permeable, gas moves freely
- 2 tiles thick: pressure differential checked, may blow
- 3+ tiles thick: holds under normal conditions (break check doesn't reach inner tile)
- Plants are soft infrastructure — free, growable, self-repairing, fragile

### Entity Layer (NOT in the pixel sim)

Entities exist on a render/logic layer above the pixel grid. They read pixel state and write pixel state but are never part of pressure/diffusion calculations.

#### Walker (Player Agent)

- Player controls one walker at a time
- Suit power: depletes over time, tethers player to base/tank range
- Actions: move, flag resource nodes for drone harvesting, interact with tanks
- Collision reads the pixel grid directly (solid pixel = collision)
- Death on: suit power depletion, toxic atmosphere, hostile creature contact
- On death: base builds a new walker (expensive, time-consuming), world continues without interruption

#### Doozers (Drones)

- Spawned by base and tanks
- Operate within a fuel-range bubble around their parent structure
- Autonomous behaviors: mine flagged pixels, haul resources, farm (in farm mode)
- Farm mode doozers: tend plant tiles, harvest output, process into refined materials, stack for player pickup
- Build cosmetic infrastructure in render layer (scaffolding, tiny buildings) that grows over repeat visits
- Affect sim grid (remove ore pixels, place processed pixels) but are not sim grid entities

#### Creatures — One Herbivore, One Predator

- **Age:** born → juvenile → prime → old (float, continuous)
- **Energy:** gained from eating, depleted over time
- **Speed:** driven by age and energy. Young: moderate. Prime + fed: fast. Old or hungry: slow.
- **Breeding:** only within a life-stage band AND energy above threshold
- **Predation:** no hunting AI. Predator eats herbivore on contact. Speed differential determines who gets caught. Slow things die.
- **Death:** entity removed, nutrient pixel deposited on grid at death location
- **Population regulation:** entirely emergent from speed/energy/breeding rules

### Infrastructure

#### Base

- Starting structure, placed at mission start
- Extracts resources from adjacent pixels
- Builds walkers (expensive — requires rare pixel types)
- Spawns doozers within its range bubble
- Passively terraforms: pixels adjacent to base slowly convert toward habitable
- The green spreads outward from base over time

#### Tank (One Type)

- Found in world, requires resources to activate/unstick
- Can be emplaced at a location to create a drone operations node
- Modes: **mine** (drones extract flagged resources), **farm** (doozers build farm infrastructure, tend plants, produce processed materials)
- Creates a drone fuel-range bubble around itself
- Requires periodic maintenance visits from walker or maintenance probe
- Node degrades without visits (doozers slow, eventually stop)

#### Resource Hauling

- Doozers carry materials to edge of their range bubble
- Next bubble's doozers pick up and carry further (relay chain)
- Player can haul batches directly via pod/tank for bulk transport

### The Tutorial Cave (Single Level)

One pre-built cave near the base. Near-perfect geometry for establishing a rain cycle:

- Pond (pre-placed liquid water pixels)
- Grow shelves (ledges suitable for plant placement)
- Ceiling at correct height for condensation
- Semi-open entrance (player decides how much to seal with plant walls)
- Purpose: player grows plant walls across opening → moisture retained → evaporation + condensation cycle starts → rain → plants spread on grow shelves → green fills cave
- This cave is also the **dev tuning room** for the rain/condensation system. If it doesn't work here, the sim is broken.

---

## Scope — What's OUT (Future Layers)

Do not implement any of the following in this branch:

- Overlays (temperature, gas, pressure, moisture) — future unlock system
- Map / minimap — future tech unlock
- Bionetwork tendrils (background layer living network)
- Parallax backgrounds
- Multiple biomes
- Doozer aesthetic progression (mushroom houses, treehouses)
- Tank recycling/redeployment
- Maintenance probe units
- Multiple walker types
- Mechanical doors / airlocks
- Power grid system
- Surface missions
- Multiple creature species beyond one herbivore + one predator
- Map decay / greyscale stale data

---

## Success Criteria

The MVP is proven when:

1. **Rain cycle emerges** — player seals cave with plant walls, water evaporates, condenses on ceiling, drips back down. No scripting.
2. **Green spreads** — plants propagate through connected suitable pixels from base outward without player manually placing each one.
3. **Food chain self-regulates** — herbivore population rises and falls based on food availability. Predator population tracks herbivore population with a lag. No population caps in code.
4. **Pressure breaks work** — seal a room, let gas build, watch plant wall blow out at 1-2 tile thickness. 3+ holds.
5. **Doozers produce** — set tank to farm mode, leave, return, find processed materials stacked for pickup and tiny scaffold structures in render layer.
6. **Walker death is expensive, not terminal** — walker dies, world keeps running, base eventually produces new walker, player resumes with everything behind them still functioning.

---

## Architecture Notes

- **One source of truth:** the pixel grid IS the collision map, the pathfinding map, and the physics state. No separate layers to sync.
- **Entity layer is read/write overlay:** entities query pixel state and modify pixels but are never in the sim calculation loop. Hundreds of doozers should not affect sim tick performance.
- **Pixel types are property tables:** each type defines `blocks_movement`, `blocks_airflow`, `pressure_breakable`, `temperature_conductivity`, etc. New types are data, not code.
- **Sim tick and entity tick are separate loops:** pixel sim runs its diffusion/chemistry pass, then entities act on the results. Clean separation.

---

## Design Philosophy

> You are Destiny from Stargate Universe. You don't settle. You seed. The civilization happens behind you.

The player is a surveyor, not a miner. Doozers are the hands. The walker is the eyes. The base is the seed. The game is watching a dead rock learn to breathe.

Nothing is scripted. Everything is reactive. The designer's job is picking the right five rules per system and letting them collide.
