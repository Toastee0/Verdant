// worldgen/mod.rs — procedural world generation
//
// Chunks generate on first discovery (when the player enters Chebyshev range).
// Undiscovered chunks don't exist in memory — they're frozen in geological time.
// "The world is frozen until you arrive."
//
// Generation is deterministic: the same chunk coord always produces the same
// output, regardless of visit order. Seed = f(chunk_coord, world_seed).
//
// ── Generation pipeline ───────────────────────────────────────────────────────
//
//  1. Geological base layer
//       Layered noise selects: hard rock / rock / packed dirt / loose soil / air
//       Depth from the surface drives the mix (deeper = harder rock, more ore).
//       The impact crater at (0,0) is factored in — shattered rock, glass, debris.
//
//  2. Cave carving
//       Worm-algorithm caves through the geological base.
//       Cave frequency increases at depth; some caves are flooded (proto-water).
//
//  3. Ore placement
//       Ore deposits seeded by noise + depth rules.
//       Different ore types at different depth bands.
//
//  4. Points of interest (POI)
//       Placed after base generation. See poi.rs for the full system.
//       Examples: old pumping stations, overgrown previous-attempt fields,
//       debris from earlier machine impacts.
//
// ── POI system overview ───────────────────────────────────────────────────────
//
// A POI is a pre-authored feature stencil placed into the generated chunk.
// They tell the story of the ancient machine's previous attempts.
// All POI types are data-driven: defined as JSON templates in assets/data/pois/.
//
// POI placement rules (per chunk, evaluated during generation):
//   - Depth band: each POI has a min/max depth range in which it can appear.
//   - Rarity: a seeded probability roll using (chunk_coord + world_seed + poi_id).
//   - Exclusion: a chunk can have at most one POI (highest-priority roll wins).
//
// POI types (from GDD narrative — "the planet's history is written in garbage"):
//
//   OldWaterPump
//     Broken machinery embedded in rock. Residual moisture nearby.
//     May still function if repaired — grants a permanent underground water source.
//     Equivalent to an Aquifer Tap super item, but discovered rather than built.
//     Depth band: mid-depth. Rarity: uncommon.
//
//   OvergrownField
//     A sealed chamber where a previous attempt succeeded locally.
//     Contains an established colony of an early-game plant species
//     (moss, cave lichen, cave fern). Pre-built ecosystem the player finds intact.
//     Lets the player skip bootstrapping that species — harvest seeds/biomass,
//     or study what a working garden looks like.
//     Depth band: mid-to-shallow. Rarity: rare.
//
//   ImpactDebris
//     Scattered fragments from previous machine crashes. Ore-rich.
//     May contain salvageable components. Surface-to-shallow.
//     Rarity: common near origin, uncommon elsewhere.
//
//   AncientCistern
//     A large sealed rock cavity containing preserved water from a previous cycle.
//     When breached, releases a significant water volume into the local water cycle.
//     Deep only. Rarity: rare.
//
// (More POI types will be added as the narrative develops.)
//
// ── Module structure (to be implemented) ─────────────────────────────────────
//
//   worldgen/
//     mod.rs        — this file; top-level generate() entry point
//     noise.rs      — integer noise functions (no floats)
//     geology.rs    — base layer + cave carving + ore placement
//     poi.rs        — POI template loading, placement, stencil application

use crate::chunk::{Chunk, ChunkCoord};

/// Generate a chunk at the given coordinates.
///
/// This is the single entry point called by chunk_manager when a chunk
/// is first discovered. The result is deterministic for a given coord.
///
/// Currently a stub — returns an empty air chunk.
/// TODO: implement full geological generation pipeline.
pub fn generate(coord: ChunkCoord) -> Chunk {
    // TODO: derive world seed from a global config or save file
    // TODO: call geology::generate_base(coord, seed)
    // TODO: call geology::carve_caves(coord, seed, &mut chunk)
    // TODO: call geology::place_ores(coord, seed, &mut chunk)
    // TODO: call poi::maybe_place(coord, seed, &mut chunk)
    Chunk::new(coord)
}
