# For the render agent — scanner overlay system

Design decision just made in a conversation with Adrian. This needs to be designed
into the renderer from the start, not bolted on later.

---

## The three knowledge states

Every cell in the world exists in one of three states from the player's perspective:

```
Unknown   — never overflown, never visited. Full fog of war. Render as black / void.
Scanned   — pod has flown over it; subsurface revealed to scan depth.
            Render as a false-color geological overlay, not full cell fidelity.
Visited   — player or tank has physically been here. Full cell rendering, true colors.
```

The scanner does NOT give full cell data. It gives a geological summary — shape of cavities,
presence of ore, presence of water. The player sees a simplified overlay, not the real image.
That distinction is intentional and important for progression.

---

## What the scanner reveals per tier (for false-color treatment)

```
TIER 0 — Surface only (always active, pod light)
  Normal lighting pass. Nothing special needed beyond the base renderer.

TIER 1 — Shallow (depth ~5 cells)
  Reveals: air pockets, cave geometry, rough rock type
  False-color: white = cavity/air, mid-grey = rock, no ore highlight yet

TIER 2 — Ore scanner (depth ~15 cells)
  Reveals: high-density mineral cells (ore veins)
  False-color adds: warm orange highlight on cells with mineral > MINERAL_HARD (240)

TIER 3 — Hydrological (depth ~30 cells)
  Reveals: water pockets, aquifer shape, underground flow paths
  False-color adds: cool blue for cells with water > WATER_WET (150)
  This is how sealed AncientCistern POIs get found — large blue mass with no surface feature

TIER 4 — Bio scan (depth ~15 cells, shorter — biology attenuates)
  Reveals: living cells, root network extent, creature population zones
  False-color adds: green pulse on cells where species > 0
```

Scan depth is a renderer + game-state concern. The sim doesn't need to know which tier
the player has — it just exposes cell data. The renderer reads the player's current scanner
tier from game state and decides how deep to render the overlay.

---

## Visual treatment

**Unknown zones**: pure black. Not dark grey, not starfield — void. The planet is dead
until you arrive. Emptiness should feel like emptiness.

**Scanned zones**: desaturated, slightly transparent false-color overlay. Suggested approach:

```wgsl
// Pseudocode for scanned cell treatment in fragment shader
if cell_state == SCANNED {
    let base = geological_false_color(cell.mineral, cell.water, cell.species);
    // Desaturate and reduce alpha — ghosted, readable but clearly not "real"
    let grey = dot(base.rgb, vec3(0.299, 0.587, 0.114));
    let desaturated = mix(vec3(grey), base.rgb, 0.3);
    return vec4(desaturated, 0.6); // 60% opacity over the void beneath
}
```

**Visited zones**: full color rendering as described in HANDOFF_RENDER.md.

**Scan boundary**: the edge where scanned data ends and fog begins should be a clean
hard line, not a gradient. The scanner has a defined depth; there is no "partial knowledge."
A 1-pixel dark border at the scan boundary would make it feel like a technical readout.

---

## Where scan state lives

The sim crate will need to track this eventually, but for now it's renderer-side:

Option A (simple): The renderer keeps a `HashMap<ChunkCoord, ScanDepth>` that records
the deepest scan pass over each chunk. When rendering, anything below the scan depth
and not physically visited gets the false-color treatment.

Option B (full): Add a `scan_depth: u8` field to each Cell (would need to modify the
Cell struct in sim — talk to the sim agent before doing this). 0 = unknown,
1-255 = scan depth reached.

**Recommendation**: Start with Option A (renderer-side). It's zero-sim-impact and lets
you iterate on the visual treatment without touching the cell layout. If per-cell scan
granularity becomes important (e.g. partial cave reveals, directional scan shadow), revisit.

---

## The discovery moment this has to support

When the player first gets the hydrological scanner and flies over an AncientCistern POI:

1. Terrain looks normal on the surface — nothing special
2. Pod scanner runs — a large BLUE mass appears 20 cells down, under solid rock
3. No surface feature explains it. The player sees: there is water down there.
4. They have to decide: is it worth drilling to?

That moment — recognizing an anomaly from altitude — is the payoff for building the scanner.
The false-color rendering has to make that blue mass legible and surprising. It should feel
like an X-ray film, not a game UI.

---

## Fog of war interaction

The scan boundary and the fog of war are two separate systems:

- **Fog of war** = has the chunk been discovered (player entered its Chebyshev range)?
  If not discovered: chunk doesn't exist in sim, render as void.
- **Scan state** = within a discovered chunk, how much subsurface is known?
  If discovered but unscanned: surface visible (from flyover lighting), subsurface = void.
  If scanned: subsurface visible at false-color fidelity to scan depth.
  If visited: full rendering.

The ChunkManager already handles discovery. A chunk only enters existence when the player
enters its active_radius. Render void for any coordinate not in ChunkManager::iter_chunks().

---

## No action needed immediately

The scanner is an upgrade that won't be in the game for a while. But the rendering
architecture should account for it:

1. Don't hard-code "render everything in full color" — leave a hook for per-cell
   knowledge state
2. The false-color shader path can be a stub (always returns full color for now)
3. Just make sure the fog-of-war / void rendering is clean from day one — that's
   the foundation the scanner overlay builds on

---

Good luck. The sim agent is in `crates/sim/` if you need to coordinate on cell layout changes.
Check `CLAUDE.md` for the full project context and aesthetic goals.
