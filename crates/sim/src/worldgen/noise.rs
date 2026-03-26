// worldgen/noise.rs — deterministic seeded noise generators
//
// All noise is deterministic: the same (wx, wy, seed) always produces the
// same output, regardless of generation order. This is critical — a player
// who walks east then west must see the same geology as one who walks west
// then east.
//
// Uses the `noise` crate's Fbm<Perlin> for all continuous noise passes.
// Separate frequency/octave configurations distinguish surface terrain,
// cave systems, ore pockets, and geothermal blobs.

use noise::{Fbm, MultiFractal, NoiseFn, Perlin};

/// Derive a deterministic seed for noise generation from chunk coords and a world seed.
/// Different generator passes use different `salt` values so their noise is uncorrelated.
///
/// In C: just hash the inputs. Same idea here — wrapping arithmetic hash.
pub fn chunk_seed(cx: i32, cy: i32, world_seed: u64) -> u64 {
    let cx = cx as u64;
    let cy = cy as u64;
    world_seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(cx.wrapping_mul(2654435761).wrapping_add(cy.wrapping_mul(1013904223)))
}

/// Create a Fbm<Perlin> noise generator from a seed. The seed determines
/// which Perlin permutation table is used — different seeds produce
/// completely different noise fields.
pub fn make_fbm(seed: u32, octaves: usize, frequency: f64) -> Fbm<Perlin> {
    Fbm::<Perlin>::new(seed)
        .set_octaves(octaves)
        .set_frequency(frequency)
}

// ── Pre-built noise generator set ─────────────────────────────────────────────
//
// Each worldgen pass uses a different noise configuration.
// All are deterministic from the world seed.

/// Collection of noise generators for all worldgen passes.
/// Created once per world seed, reused for every chunk.
pub struct WorldNoise {
    /// Surface heightmap: low frequency, 2 octaves.
    /// Input: (wx * 0.003, 0.0) → range ~[-1, 1] → scaled to ±60 cells.
    pub surface: Fbm<Perlin>,

    /// Layer boundary offsets: medium frequency, 2 octaves.
    /// Input: (wx * 0.005, wy * 0.005) → range ~[-1, 1] → scaled to ±40 cells.
    pub layers: Fbm<Perlin>,

    /// Cave carving: medium-high frequency, 2 octaves.
    /// Input: (wx * 0.008, wy * 0.008) → threshold comparison.
    pub caves: Fbm<Perlin>,

    /// Ore placement: high frequency, 2 octaves (small blobs).
    /// Input: (wx * 0.02, wy * 0.02) → threshold → ore type.
    pub ore: Fbm<Perlin>,

    /// Geothermal hotspots: low frequency, 2 octaves (large blobs).
    /// Input: (wx * 0.01, wy * 0.01) → added heat.
    pub geothermal: Fbm<Perlin>,
}

impl WorldNoise {
    pub fn new(world_seed: u64) -> WorldNoise {
        // Each pass gets a different seed derived from world_seed.
        // The salt offsets ensure uncorrelated noise fields.
        let base = (world_seed & 0xFFFF_FFFF) as u32;
        WorldNoise {
            surface:    make_fbm(base,            2, 0.003),
            layers:     make_fbm(base.wrapping_add(1), 2, 0.005),
            caves:      make_fbm(base.wrapping_add(2), 2, 0.008),
            ore:        make_fbm(base.wrapping_add(3), 2, 0.02),
            geothermal: make_fbm(base.wrapping_add(4), 2, 0.01),
        }
    }

    /// Surface height at a given world-x. Returns absolute world y where
    /// the air→rock transition happens.
    ///
    /// Near origin (|wx| < 200): flatten toward 0 so the machine pocket
    /// area is relatively flat — no dramatic hills right at start.
    pub fn surface_height(&self, wx: i32) -> i32 {
        let raw = self.surface.get([wx as f64, 0.0]);
        let variation = (raw * 60.0) as i32;

        // Flatten near origin: blend toward 0 as |wx| → 0.
        let dist_from_origin = (wx.abs() as f64 / 200.0).min(1.0);
        (variation as f64 * dist_from_origin) as i32
    }

    /// Layer boundary offset at a given world position. Returns a cell offset
    /// that shifts the nominal layer boundary up or down.
    pub fn layer_offset(&self, wx: i32, wy: i32) -> i32 {
        let raw = self.layers.get([wx as f64, wy as f64]);
        (raw * 40.0) as i32
    }

    /// Returns true if this cell should be carved as a cave.
    /// Deeper = more caves (lower threshold).
    ///
    /// `wy` is world y, `depth_below_surface` is wy - surface_y.
    /// Near the impact compression zone (close to origin, mid depth),
    /// caves are suppressed.
    pub fn is_cave(&self, wx: i32, wy: i32, depth_below_surface: i32, near_impact: bool) -> bool {
        if near_impact {
            // Impact compression zone: caves are crushed shut.
            return false;
        }

        let raw = self.caves.get([wx as f64, wy as f64]);
        let threshold = cave_threshold(depth_below_surface);
        raw > threshold
    }

    /// Geothermal noise value at a position. Used for temperature hotspots.
    pub fn geothermal_value(&self, wx: i32, wy: i32) -> f64 {
        self.geothermal.get([wx as f64, wy as f64])
    }

    /// Ore noise value at a position. Higher = denser ore concentration.
    pub fn ore_value(&self, wx: i32, wy: i32) -> f64 {
        self.ore.get([wx as f64, wy as f64])
    }
}

/// Cave threshold varies with depth below surface.
/// Surface: very few caves (0.75).
/// Mid depth: common caves (0.55).
/// Deep: large cave systems (0.45).
fn cave_threshold(depth_below_surface: i32) -> f64 {
    let depth_factor = (depth_below_surface as f64 / 400.0).clamp(0.0, 1.0);
    0.75 - depth_factor * 0.30
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunk_seed_deterministic() {
        let s1 = chunk_seed(5, 10, 42);
        let s2 = chunk_seed(5, 10, 42);
        assert_eq!(s1, s2, "same inputs must produce same seed");
    }

    #[test]
    fn chunk_seed_varies_with_coords() {
        let s1 = chunk_seed(0, 0, 42);
        let s2 = chunk_seed(1, 0, 42);
        let s3 = chunk_seed(0, 1, 42);
        assert_ne!(s1, s2, "different cx should differ");
        assert_ne!(s1, s3, "different cy should differ");
    }

    #[test]
    fn surface_flat_at_origin() {
        let noise = WorldNoise::new(42);
        let h = noise.surface_height(0);
        assert_eq!(h, 0, "surface should be flat at origin (wx=0)");
    }

    #[test]
    fn cave_threshold_decreases_with_depth() {
        let shallow = cave_threshold(50);
        let deep = cave_threshold(350);
        assert!(shallow > deep, "deeper should have lower threshold (more caves)");
    }
}
