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
//  1. Geological base layer (geology.rs)
//       Layered noise selects: hard rock / rock / packed dirt / loose soil / air
//       Depth from the surface drives the mix (deeper = harder rock, more ore).
//       Impact compression zone near origin (280-450 cells deep).
//
//  2. Cave carving (noise.rs threshold)
//       Perlin noise caves. Frequency increases at depth.
//       Suppressed in impact compression zone.
//
//  3. Ore placement (geology.rs)
//       Depth-gated ore deposits. Different bands for iron/fuel/deep ore.
//       MineralRich sectors get lower thresholds.
//
//  4. Sector variation (geology.rs)
//       Horizontal biome bands: Origin, Volcanic, MineralRich, DeepWater, Debris.
//
//  5. Special cases:
//       - Machine pocket at (cx=0, cy=1): elliptical cavity
//       - Lava core (cy >= 16): solid max-heat sentinel
//
// ── Module structure ──────────────────────────────────────────────────────────
//
//   worldgen/
//     mod.rs        — this file; top-level generate() entry point
//     noise.rs      — Fbm<Perlin> noise generators, WorldNoise
//     geology.rs    — cell generation, sector logic, machine pocket

pub mod noise;
pub mod geology;

use crate::chunk::{Chunk, ChunkCoord, CHUNK_WIDTH, CHUNK_HEIGHT};
use noise::WorldNoise;
use geology::{sector, generate_cell, carve_machine_pocket, generate_lava_core,
              LAVA_CORE_DEPTH_CHUNKS, MACHINE_POCKET_CY};

/// The default world seed. Eventually this will come from save file / user input.
const WORLD_SEED: u64 = 12345;

/// Lazily-initialized noise generators. Using a function that creates on demand.
/// In a real setup this would be stored in the ChunkManager, but for now we
/// recreate it per generate() call. The noise crate's Perlin is cheap to create
/// (just a permutation table copy), so this is fine for worldgen which runs
/// once per chunk discovery.
fn world_noise() -> WorldNoise {
    WorldNoise::new(WORLD_SEED)
}

/// Generate a chunk at the given coordinates.
///
/// This is the single entry point called by chunk_manager when a chunk
/// is first discovered. The result is deterministic for a given coord.
pub fn generate(coord: ChunkCoord) -> Chunk {
    let noise = world_noise();
    let mut chunk = Chunk::new(coord);

    // Special case: lava core (below the simulated world)
    if coord.cy >= LAVA_CORE_DEPTH_CHUNKS {
        generate_lava_core(&mut chunk);
        return chunk;
    }

    // Special case: machine pocket chunk
    if coord.cx == 0 && coord.cy == MACHINE_POCKET_CY {
        carve_machine_pocket(&mut chunk, &noise);
        return chunk;
    }

    // Normal generation: iterate every cell in the chunk
    let sec = sector(coord.cx);
    let wx0 = coord.cx * CHUNK_WIDTH as i32;
    let wy0 = coord.cy * CHUNK_HEIGHT as i32;

    for ly in 0..CHUNK_HEIGHT {
        for lx in 0..CHUNK_WIDTH {
            let wx = wx0 + lx as i32;
            let wy = wy0 + ly as i32;
            let cell = generate_cell(wx, wy, sec, &noise);
            chunk.set_front(lx, ly, cell);
        }
    }

    chunk
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cell::*;

    #[test]
    fn generate_origin_chunk_not_all_air() {
        // The origin chunk at (0,0) includes sky + surface. Should have some rock.
        let chunk = generate(ChunkCoord::new(0, 0));
        let has_rock = (0..CHUNK_HEIGHT).any(|y| {
            (0..CHUNK_WIDTH).any(|x| chunk.get(x, y).mineral >= MINERAL_ROCK)
        });
        // Origin chunk cy=0 starts at wy=0. Surface is at ~y=0.
        // The lower portion of this chunk should have geology.
        assert!(has_rock, "origin chunk should contain some rock");
    }

    #[test]
    fn generate_sky_chunk_is_mostly_air() {
        // A chunk well above surface
        let chunk = generate(ChunkCoord::new(0, -2));
        let air_count = (0..CHUNK_HEIGHT)
            .flat_map(|y| (0..CHUNK_WIDTH).map(move |x| (x, y)))
            .filter(|&(x, y)| {
                let c = chunk.get(x, y);
                c.mineral < MINERAL_TRACE
            })
            .count();
        let total = CHUNK_WIDTH * CHUNK_HEIGHT;
        let air_ratio = air_count as f64 / total as f64;
        assert!(air_ratio > 0.90,
            "sky chunk should be mostly air, got {:.1}% air", air_ratio * 100.0);
    }

    #[test]
    fn generate_deep_chunk_is_solid() {
        // A chunk at mid-depth, away from origin
        let chunk = generate(ChunkCoord::new(10, 3));
        let solid_count = (0..CHUNK_HEIGHT)
            .flat_map(|y| (0..CHUNK_WIDTH).map(move |x| (x, y)))
            .filter(|&(x, y)| chunk.get(x, y).mineral >= MINERAL_ROCK)
            .count();
        let total = CHUNK_WIDTH * CHUNK_HEIGHT;
        let solid_ratio = solid_count as f64 / total as f64;
        // Deep chunk should be mostly solid (with some caves)
        assert!(solid_ratio > 0.50,
            "deep chunk should be mostly solid, got {:.1}% solid", solid_ratio * 100.0);
    }

    #[test]
    fn generate_lava_core_is_hot() {
        let chunk = generate(ChunkCoord::new(5, LAVA_CORE_DEPTH_CHUNKS));
        let cell = chunk.get(256, 256);
        assert_eq!(cell.mineral, 255, "lava core should be max mineral");
        assert_eq!(cell.temperature, 255, "lava core should be max temperature");
    }

    #[test]
    fn machine_pocket_has_air() {
        let chunk = generate(ChunkCoord::new(0, MACHINE_POCKET_CY));
        // Center of the pocket should be air
        let center = chunk.get(256, 80);
        assert!(center.is_air(), "machine pocket center should be air, got mineral={}", center.mineral);
    }

    #[test]
    fn generation_is_deterministic() {
        let c1 = generate(ChunkCoord::new(5, 2));
        let c2 = generate(ChunkCoord::new(5, 2));
        for y in 0..CHUNK_HEIGHT {
            for x in 0..CHUNK_WIDTH {
                assert_eq!(c1.get(x, y), c2.get(x, y),
                    "generation must be deterministic at ({x}, {y})");
            }
        }
    }
}

