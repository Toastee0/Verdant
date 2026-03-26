// cell_color.rs — CPU-side Cell → RGBA conversion
//
// Derives pixel color from continuous cell values (water, mineral, temperature,
// species, tile_type, light, growth). No material-type enum lookup — color
// emerges from ratios, matching the GDD philosophy.
//
// This runs on CPU so we can iterate on the palette without recompiling shaders.
// The hot path is cell_to_rgba() called 262,144 times per chunk per frame.
// Keep it branchless where possible; the compiler will auto-vectorize the
// inner loop if we avoid complex control flow.
//
// Palette progression: dead grey → warm brown → blue (water) → green (life).

use verdant_sim::cell::{
    Cell, TEMP_FREEZE, TEMP_BOIL, TEMP_MELT_ROCK,
    MINERAL_TRACE, MINERAL_SOIL, MINERAL_DIRT,
    WATER_TRACE, WATER_DAMP, WATER_WET,
    TILE_ROOT, TILE_STEM, TILE_LEAF, TILE_FLOWER,
};

/// Convert a Cell to an RGBA pixel (4 bytes: R, G, B, A).
///
/// This is the core aesthetic function. Every visual impression the player has
/// flows through here. Tuning these blends IS tuning the game's look.
#[inline]
pub fn cell_to_rgba(cell: &Cell) -> [u8; 4] {
    // Normalize to 0.0–1.0 for blending math.
    let w = cell.water as f32 / 255.0;
    let m = cell.mineral as f32 / 255.0;
    let _t = cell.temperature as f32 / 255.0;
    let light = cell.light as f32 / 255.0;
    let sunlight = cell.sunlight as f32 / 255.0;

    // ── Base color from mineral/water ratio ───────────────────────────────

    // Start with dark void (dead planet background).
    let mut r: f32 = 0.03;
    let mut g: f32 = 0.03;
    let mut b: f32 = 0.05;

    // Mineral tints toward soil brown → rock grey as mineral increases.
    // Low mineral (soil): warm brown (0.35, 0.28, 0.22)
    // High mineral (rock): cool grey (0.50, 0.50, 0.55)
    if cell.mineral >= MINERAL_TRACE {
        let rock_blend = clamp01((m - 0.3) / 0.7);
        let mr = lerp(0.35, 0.50, rock_blend);
        let mg = lerp(0.28, 0.50, rock_blend);
        let mb = lerp(0.22, 0.55, rock_blend);
        r = lerp(r, mr, m);
        g = lerp(g, mg, m);
        b = lerp(b, mb, m);
    }

    // Water: blue tint, stronger when mineral is low (pure liquid vs wet soil).
    if cell.water >= WATER_TRACE {
        let water_vis = w * (1.0 - m * 0.8); // water barely shows through solid rock
        r = lerp(r, 0.08, water_vis);
        g = lerp(g, 0.35, water_vis);
        b = lerp(b, 0.85, water_vis);
    }

    // Wet soil: darken mineral color when water + mineral are both present.
    // (Wet earth looks darker than dry earth — universal visual truth.)
    if cell.water >= WATER_DAMP && cell.mineral >= MINERAL_SOIL {
        let wet_factor = clamp01(w * 0.3);
        r *= 1.0 - wet_factor;
        g *= 1.0 - wet_factor;
        b *= 1.0 - wet_factor * 0.5; // blue darkens less (keeps slight moisture hint)
    }

    // ── Temperature effects ───────────────────────────────────────────────

    // Ice: pale blue-white override when cold + wet.
    if cell.temperature < TEMP_FREEZE && cell.water >= WATER_WET {
        let ice_blend = clamp01((TEMP_FREEZE as f32 - cell.temperature as f32) / 40.0);
        r = lerp(r, 0.75, ice_blend);
        g = lerp(g, 0.85, ice_blend);
        b = lerp(b, 0.95, ice_blend);
    }

    // Steam / vapor: pale blue-white when hot + wet + low mineral.
    if cell.temperature >= TEMP_BOIL && cell.water >= WATER_WET && cell.mineral < MINERAL_SOIL {
        let steam_blend = clamp01(
            (cell.temperature as f32 - TEMP_BOIL as f32) / 63.0 * w
        );
        r = lerp(r, 0.85, steam_blend);
        g = lerp(g, 0.90, steam_blend);
        b = lerp(b, 1.00, steam_blend);
    }

    // Lava: orange-red glow when mineral is hot enough to melt.
    if cell.temperature >= TEMP_MELT_ROCK && cell.mineral >= MINERAL_DIRT {
        let lava_blend = clamp01(
            (cell.temperature as f32 - TEMP_MELT_ROCK as f32) / 15.0 * m
        );
        r = lerp(r, 1.00, lava_blend);
        g = lerp(g, 0.35, lava_blend);
        b = lerp(b, 0.05, lava_blend);
    }

    // General heat glow: warm tint for hot cells (conveys heat visually).
    if cell.temperature > 180 {
        let heat = clamp01((cell.temperature as f32 - 180.0) / 75.0);
        r = lerp(r, r + 0.15, heat);
        g = lerp(g, g * 0.9, heat);
    }

    // ── Biology layer ─────────────────────────────────────────────────────

    if cell.species > 0 {
        let vitality = cell.energy as f32 / 255.0;
        let maturity = cell.growth as f32 / 255.0;

        match cell.tile_type {
            TILE_ROOT => {
                // Roots: dark brown, deepens with maturity.
                let root_blend = 0.65 + maturity * 0.2;
                r = lerp(r, 0.30, root_blend);
                g = lerp(g, 0.18, root_blend);
                b = lerp(b, 0.10, root_blend);
            }
            TILE_STEM => {
                // Stems: medium green-brown, lighter than roots.
                let stem_blend = 0.6 + maturity * 0.2;
                r = lerp(r, 0.22, stem_blend);
                g = lerp(g, 0.38, stem_blend);
                b = lerp(b, 0.12, stem_blend);
            }
            TILE_LEAF => {
                // Leaves: vivid green. Brightens with energy (photosynthesis).
                let leaf_blend = 0.7 + vitality * 0.2;
                r = lerp(r, 0.08 + vitality * 0.05, leaf_blend);
                g = lerp(g, 0.50 + vitality * 0.25, leaf_blend);
                b = lerp(b, 0.08, leaf_blend);
            }
            TILE_FLOWER => {
                // Flowers: species-dependent hue. Use species ID to pick a color.
                // Wrap species through a simple palette so different species look distinct.
                let (fr, fg, fb) = flower_color(cell.species);
                let flower_blend = 0.75 + vitality * 0.15;
                r = lerp(r, fr, flower_blend);
                g = lerp(g, fg, flower_blend);
                b = lerp(b, fb, flower_blend);
            }
            _ => {
                // Generic organic cell (species>0 but no specific tile type).
                r = lerp(r, 0.15, 0.5);
                g = lerp(g, 0.55, 0.5);
                b = lerp(b, 0.12, 0.5);
            }
        }

        // Dying plants desaturate toward brown-grey.
        if cell.energy < 30 {
            let death = 1.0 - (cell.energy as f32 / 30.0);
            let grey = (r + g + b) / 3.0;
            r = lerp(r, grey * 0.8, death * 0.6);
            g = lerp(g, grey * 0.7, death * 0.6);
            b = lerp(b, grey * 0.6, death * 0.6);
        }
    }

    // ── Lighting modulation ───────────────────────────────────────────────
    // Combine ambient light and sunlight. Minimum ambient so pitch-dark cells
    // are still barely visible (player can see cave walls).
    let combined_light = clamp01(light * 0.7 + sunlight * 0.5);
    // Until the lighting pass is implemented, light/sunlight are 0.
    // Use a fallback so the world isn't invisible.
    let effective_light = if combined_light < 0.01 { 1.0 } else { combined_light };

    r *= effective_light;
    g *= effective_light;
    b *= effective_light;

    // ── Output ────────────────────────────────────────────────────────────
    [
        (r.clamp(0.0, 1.0) * 255.0) as u8,
        (g.clamp(0.0, 1.0) * 255.0) as u8,
        (b.clamp(0.0, 1.0) * 255.0) as u8,
        255, // fully opaque
    ]
}

/// Convert a full chunk slice (512×512 Cells) into an RGBA pixel buffer.
/// Returns a Vec<u8> of length 512 * 512 * 4 = 1,048,576 bytes.
///
/// This is the hot upload path — called once per visible chunk per frame.
pub fn cells_to_rgba(cells: &[Cell]) -> Vec<u8> {
    debug_assert_eq!(cells.len(), 512 * 512, "expected exactly one chunk of cells");
    let mut pixels = vec![0u8; cells.len() * 4];
    for (i, cell) in cells.iter().enumerate() {
        let rgba = cell_to_rgba(cell);
        let base = i * 4;
        pixels[base    ] = rgba[0];
        pixels[base + 1] = rgba[1];
        pixels[base + 2] = rgba[2];
        pixels[base + 3] = rgba[3];
    }
    pixels
}

// ── Helpers ───────────────────────────────────────────────────────────────────

#[inline(always)]
fn lerp(a: f32, b: f32, t: f32) -> f32 {
    a + (b - a) * t
}

#[inline(always)]
fn clamp01(v: f32) -> f32 {
    v.clamp(0.0, 1.0)
}

/// Pick a flower color based on species ID. Cycles through a small
/// hand-picked palette so different species look distinct.
fn flower_color(species: u8) -> (f32, f32, f32) {
    // 6-color palette cycling by species ID. Each species gets a
    // consistent flower hue.
    match species % 6 {
        0 => (0.90, 0.20, 0.30), // red
        1 => (0.95, 0.75, 0.20), // yellow
        2 => (0.85, 0.40, 0.80), // pink/magenta
        3 => (0.95, 0.55, 0.15), // orange
        4 => (0.60, 0.30, 0.85), // purple
        5 => (0.90, 0.90, 0.90), // white
        _ => (0.90, 0.20, 0.30), // fallback (unreachable)
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn air_is_dark() {
        let rgba = cell_to_rgba(&Cell::AIR);
        // Dead planet air should be very dark.
        assert!(rgba[0] < 20 && rgba[1] < 20 && rgba[2] < 20,
            "air should be near-black, got {:?}", rgba);
        assert_eq!(rgba[3], 255, "alpha should be fully opaque");
    }

    #[test]
    fn water_is_blue() {
        let rgba = cell_to_rgba(&Cell::new_water());
        // Water should have highest blue channel.
        assert!(rgba[2] > rgba[0] && rgba[2] > rgba[1],
            "water should be blue-dominant, got {:?}", rgba);
    }

    #[test]
    fn rock_is_grey() {
        let rgba = cell_to_rgba(&Cell::rock());
        // Rock should be grey-ish (r ≈ g ≈ b, all moderate).
        let diff_rg = (rgba[0] as i16 - rgba[1] as i16).unsigned_abs();
        assert!(diff_rg < 30, "rock should be near-grey, got {:?}", rgba);
        assert!(rgba[0] > 80, "rock should be moderate brightness, got {:?}", rgba);
    }

    #[test]
    fn lava_is_hot() {
        let rgba = cell_to_rgba(&Cell::lava());
        // Lava should be red/orange dominant.
        assert!(rgba[0] > rgba[1] && rgba[0] > rgba[2],
            "lava should be red-dominant, got {:?}", rgba);
    }

    #[test]
    fn plant_leaf_is_green() {
        let leaf = Cell::plant_tile(1, TILE_LEAF, 200, 200, 0, 0);
        let rgba = cell_to_rgba(&leaf);
        assert!(rgba[1] > rgba[0] && rgba[1] > rgba[2],
            "leaf should be green-dominant, got {:?}", rgba);
    }

    #[test]
    fn cells_to_rgba_correct_length() {
        let cells = vec![Cell::AIR; 512 * 512];
        let pixels = cells_to_rgba(&cells);
        assert_eq!(pixels.len(), 512 * 512 * 4);
    }
}
