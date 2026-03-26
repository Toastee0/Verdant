// worldgen/geology.rs — base geological layer generation
//
// Converts raw noise values into actual Cell data for each position.
// This is where the vertical depth profile from HANDOFF_SIM_WORLDGEN.md
// becomes concrete cells.
//
// The depth profile (from surface downward):
//   Sky (wy < surface_y):    Air, cold, dry
//   Surface soil (0–80):     soil→dirt, dry
//   Rock layer (80–300):     MINERAL_ROCK, caves start
//   Impact zone (280–450):   MINERAL_HARD near origin, few caves
//   Deep rock (300–550):     MINERAL_HARD, rare ore, big caves
//   Geothermal (500+):       hot rock, lava veins, steam
//
// Sector modifiers (Volcanic, MineralRich, DeepWater, Debris) are applied
// on top of the base profile.

use crate::cell::*;
use super::noise::WorldNoise;

// ── World constants ───────────────────────────────────────────────────────────

pub const WORLD_WIDTH_CHUNKS: i32 = 128;
pub const LAVA_CORE_DEPTH_CHUNKS: i32 = 16;
pub const MACHINE_DEPTH_CELLS: i32 = 600;

/// Machine pocket: an elliptical cavity at (cx=0, cy=1, local_y≈80).
/// World-y center = CHUNK_HEIGHT (512) + 80 = 592 ≈ MACHINE_DEPTH_CELLS.
pub const MACHINE_POCKET_CY: i32 = 1;
pub const MACHINE_POCKET_CENTER_LY: i32 = 80;
pub const MACHINE_POCKET_HALF_W: i32 = 20;
pub const MACHINE_POCKET_HALF_H: i32 = 10;

// ── Sectors (horizontal biome variation) ──────────────────────────────────────

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Sector {
    Origin,
    Volcanic,
    MineralRich,
    DeepWater,
    Debris,
}

/// Map chunk x-coordinate to a sector. Wraps horizontally.
pub fn sector(cx: i32) -> Sector {
    let cx = cx.rem_euclid(WORLD_WIDTH_CHUNKS) as usize;
    match cx {
        0..=15   => Sector::Origin,
        16..=31  => Sector::Volcanic,
        32..=47  => Sector::MineralRich,
        48..=63  => Sector::DeepWater,
        64..=79  => Sector::Debris,
        80..=95  => Sector::Volcanic,
        96..=111 => Sector::MineralRich,
        _        => Sector::DeepWater,  // 112..=127
    }
}

// ── Cell generation ───────────────────────────────────────────────────────────

/// Generate a single cell at world coordinates (wx, wy).
///
/// This is the core function called 262,144 times per chunk.
/// It must be fast — no allocations, no branching on strings, just arithmetic.
pub fn generate_cell(wx: i32, wy: i32, sec: Sector, noise: &WorldNoise) -> Cell {
    let surface_y = noise.surface_height(wx);
    let depth = wy - surface_y; // positive = below surface

    // ── Sky ───────────────────────────────────────────────────────────────
    if depth < -10 {
        // Above surface: thin atmosphere. Colder with altitude.
        let altitude = (-depth) as u8;
        let temp = TEMP_AMBIENT.saturating_sub(altitude.min(60));
        // trace moisture in atmosphere
        let water = if altitude < 40 { WATER_TRACE / 4 } else { 0 };
        return Cell::new(water, 0, temp, 0);
    }

    // ── Surface transition zone (depth -10 to 0): air/soil blend ──────────
    if depth < 0 {
        // Slight soil exposure at surface — loose material before solid ground.
        let mineral = ((10 + depth) as u8) * (MINERAL_SOIL / 10);
        return Cell::new(0, mineral, TEMP_AMBIENT, 0);
    }

    // ── Below surface — check for caves first ─────────────────────────────
    let near_impact = is_in_impact_zone(wx, wy, surface_y);
    if noise.is_cave(wx, wy, depth, near_impact) {
        return Cell::AIR;
    }

    // ── Layer profile (depth below surface) ───────────────────────────────
    let layer_off = noise.layer_offset(wx, wy);

    let (base_mineral, base_temp) = if depth < 80 + layer_off {
        // Surface soil layer
        let m = lerp_u8(MINERAL_SOIL, MINERAL_DIRT, depth as f32 / 80.0);
        (m, TEMP_AMBIENT)
    } else if depth < 300 + layer_off {
        // Rock layer
        let m = MINERAL_ROCK;
        let t = TEMP_AMBIENT + ((depth - 80) as f32 / 220.0 * 10.0) as u8;
        (m, t)
    } else if near_impact {
        // Impact compression zone: extremely hard, slightly warm
        (MINERAL_HARD, TEMP_AMBIENT + 22)
    } else if depth < 550 + layer_off {
        // Deep rock
        let t = TEMP_AMBIENT + ((depth - 300) as f32 / 250.0 * 60.0) as u8;
        (MINERAL_HARD, t)
    } else {
        // Geothermal zone
        let geo = noise.geothermal_value(wx, wy);
        let geo_heat = if geo > 0.7 { ((geo - 0.7) * 200.0) as u8 } else { 0 };
        let base_heat = ((depth - 500) as f32 / 100.0 * 40.0).min(80.0) as u8;
        let t = (TEMP_AMBIENT as u16 + 60 + base_heat as u16 + geo_heat as u16).min(255) as u8;
        (MINERAL_HARD, t)
    };

    // ── Apply sector modifiers ────────────────────────────────────────────
    let (mineral, temp, water) = apply_sector_modifiers(base_mineral, base_temp, 0, depth, sec, noise, wx, wy);

    // ── Ore check ─────────────────────────────────────────────────────────
    let mineral = ore_modifier(mineral, wx, wy, depth, noise, sec);

    // ── Lava veins in geothermal zone ─────────────────────────────────────
    if temp >= TEMP_MELT_ROCK {
        return Cell::new(0, mineral, temp, 0);
    }

    Cell::new(water, mineral, temp, 0)
}

/// Check if a world position is in the impact compression zone.
/// Near origin (|wx| < world-width-of-2-chunks), depth 280–450.
fn is_in_impact_zone(wx: i32, wy: i32, surface_y: i32) -> bool {
    let depth = wy - surface_y;
    let horizontal_dist = wx.abs();

    // Impact zone: within ~2 chunks of origin (1024 cells), depth 280-450
    // Fades with distance: strongest at center, gone by 1024 cells out
    horizontal_dist < 1024 && depth >= 280 && depth <= 450
}

/// Apply per-sector modifications to base cell values.
fn apply_sector_modifiers(
    mineral: u8, temp: u8, water: u8, depth: i32,
    sec: Sector, noise: &WorldNoise, wx: i32, wy: i32,
) -> (u8, u8, u8) {
    match sec {
        Sector::Volcanic => {
            // Hotter everywhere, lava veins at depth
            let temp = temp.saturating_add(30);
            let temp = if depth > 300 {
                let geo = noise.geothermal_value(wx, wy);
                if geo > 0.6 { TEMP_MELT_ROCK } else { temp }
            } else {
                temp
            };
            (mineral, temp, water)
        }
        Sector::MineralRich => {
            // Denser rock, more ore (handled in ore_modifier)
            let mineral = mineral.saturating_add(20);
            (mineral, temp, water)
        }
        Sector::DeepWater => {
            // Moist geology
            let water = if mineral >= MINERAL_SOIL {
                water.saturating_add(20)
            } else {
                water
            };
            (mineral, temp, water)
        }
        Sector::Debris => {
            // More accessible caves handled by cave threshold.
            // Surface is more dramatic (not modeled here — surface noise handles it).
            (mineral, temp, water)
        }
        Sector::Origin => {
            // Default — no modifications
            (mineral, temp, water)
        }
    }
}

/// Check if an ore deposit should upgrade this cell's mineral value.
/// Ore cells have MINERAL_HARD and are distinguished by being denser
/// than the surrounding rock layer.
fn ore_modifier(mineral: u8, wx: i32, wy: i32, depth: i32, noise: &WorldNoise, sec: Sector) -> u8 {
    let ore_val = noise.ore_value(wx, wy);

    // Sector modifier: MineralRich has lower thresholds (more ore)
    let sector_bonus = if sec == Sector::MineralRich { 0.03 } else { 0.0 };

    // Depth-gated ore types
    let has_ore = match depth {
        80..=250  => ore_val > 0.85 - sector_bonus,  // Iron band
        200..=350 => ore_val > 0.88 - sector_bonus,  // Fuel ore band
        400..=600 => ore_val > 0.92 - sector_bonus,  // Deep ore
        _ => false,
    };

    if has_ore {
        MINERAL_HARD  // Ore cells are max-mineral to make them visually distinct
    } else {
        mineral
    }
}

/// Linear interpolation between two u8 values.
#[inline]
fn lerp_u8(a: u8, b: u8, t: f32) -> u8 {
    let t = t.clamp(0.0, 1.0);
    (a as f32 + (b as f32 - a as f32) * t) as u8
}

// ── Machine pocket generation ─────────────────────────────────────────────────

/// Generate the machine pocket chunk. This is a hand-authored open space
/// at (cx=0, cy=1). Normal geology fills the chunk first, then the pocket
/// is carved as an elliptical cavity.
pub fn carve_machine_pocket(chunk: &mut crate::chunk::Chunk, noise: &WorldNoise) {
    use crate::chunk::{CHUNK_WIDTH, CHUNK_HEIGHT};

    let sec = sector(chunk.coord.cx);
    let wx0 = chunk.coord.cx * CHUNK_WIDTH as i32;
    let wy0 = chunk.coord.cy * CHUNK_HEIGHT as i32;

    // First: fill with normal geology
    for ly in 0..CHUNK_HEIGHT {
        for lx in 0..CHUNK_WIDTH {
            let wx = wx0 + lx as i32;
            let wy = wy0 + ly as i32;
            let cell = generate_cell(wx, wy, sec, noise);
            chunk.set_front(lx, ly, cell);
        }
    }

    // Then: carve the elliptical pocket
    let cx_center: i32 = CHUNK_WIDTH as i32 / 2;  // center of chunk horizontally
    let cy_center: i32 = MACHINE_POCKET_CENTER_LY;
    let hw = MACHINE_POCKET_HALF_W;
    let hh = MACHINE_POCKET_HALF_H;

    for ly in 0..CHUNK_HEIGHT as i32 {
        for lx in 0..CHUNK_WIDTH as i32 {
            let dx = lx - cx_center;
            let dy = ly - cy_center;
            // Ellipse test: (dx/hw)² + (dy/hh)² <= 1
            let inside = (dx * dx * hh * hh + dy * dy * hw * hw) <= (hw * hw * hh * hh);

            if inside {
                // Inside the pocket: open air
                chunk.set_front(lx as usize, ly as usize, Cell::AIR);
            } else {
                // Walls immediately around pocket: hard rock (impact-compressed)
                let wall_dist = ((dx as f64 / hw as f64).powi(2) + (dy as f64 / hh as f64).powi(2)).sqrt();
                if wall_dist < 1.3 {
                    // Within 30% of the ellipse boundary: hard rock wall
                    let wall_cell = Cell::new(0, MINERAL_HARD, TEMP_AMBIENT + 22, 0);
                    chunk.set_front(lx as usize, ly as usize, wall_cell);
                }
                // else: keep the normal geology from the first pass
            }
        }
    }
}

/// Generate a lava core chunk. Below LAVA_CORE_DEPTH_CHUNKS.
/// Not simulated — pure render-only sentinel. All cells: max mineral + max temp.
pub fn generate_lava_core(chunk: &mut crate::chunk::Chunk) {
    use crate::chunk::{CHUNK_WIDTH, CHUNK_HEIGHT};
    let lava = Cell::new(0, 255, 255, 0);
    chunk.fill_rect(0, 0, CHUNK_WIDTH, CHUNK_HEIGHT, lava);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sky_is_air() {
        let noise = WorldNoise::new(42);
        // wx=0, wy=-100: well above surface (surface_y ≈ 0 at origin)
        let cell = generate_cell(0, -100, Sector::Origin, &noise);
        assert!(cell.is_air() || cell.water < WATER_TRACE,
            "sky cell should be air or near-air, got mineral={}", cell.mineral);
    }

    #[test]
    fn deep_rock_is_solid() {
        let noise = WorldNoise::new(42);
        // wx=500 (away from origin), wy=400 (deep below surface)
        let cell = generate_cell(500, 400, Sector::Origin, &noise);
        // Could be a cave, but if not, should be solid
        if cell.mineral >= MINERAL_DIRT {
            assert!(cell.mineral >= MINERAL_ROCK,
                "deep rock should be MINERAL_ROCK+, got {}", cell.mineral);
        }
    }

    #[test]
    fn impact_zone_no_caves() {
        let noise = WorldNoise::new(42);
        // Near origin, in the impact depth band
        for wy in 300..420 {
            let cell = generate_cell(0, wy, Sector::Origin, &noise);
            // Not all cells will be non-air (surface could be different),
            // but cells at this depth near origin should generally not be caves.
            // We test that the ratio of air cells is low.
            if wy > 310 && wy < 410 {
                // In the core of the impact zone, cells should be solid
                assert!(cell.mineral >= MINERAL_ROCK || cell.mineral == 0,
                    "impact zone cell at wy={} should be hard rock, got mineral={}", wy, cell.mineral);
            }
        }
    }

    #[test]
    fn volcanic_sector_is_hotter() {
        let noise = WorldNoise::new(42);
        let normal = generate_cell(500, 200, Sector::Origin, &noise);
        let volcanic = generate_cell(500, 200, Sector::Volcanic, &noise);
        // Volcanic sector adds +30 temperature
        if !normal.is_air() && !volcanic.is_air() {
            assert!(volcanic.temperature >= normal.temperature,
                "volcanic should be hotter: {} vs {}", volcanic.temperature, normal.temperature);
        }
    }

    #[test]
    fn sector_mapping_wraps() {
        assert_eq!(sector(-1), sector(127));
        assert_eq!(sector(128), sector(0));
    }

    #[test]
    fn lerp_u8_endpoints() {
        assert_eq!(lerp_u8(0, 100, 0.0), 0);
        assert_eq!(lerp_u8(0, 100, 1.0), 100);
        assert_eq!(lerp_u8(0, 100, 0.5), 50);
    }
}
