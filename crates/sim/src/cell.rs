// cell.rs — Verdant cell encoding
//
// Each cell in the 512×512 world is a 16-byte struct. Memory is not a
// constraint (64GB RAM, 512×512 × 16B = 4MB per buffer × 2 = 8MB total),
// so the design prioritizes readability and expressiveness over packing.
//
// Conceptually the cell has three layers that update at different rates:
//   Physics  — every tick (water cycle, particle movement, heat)
//   Biology  — every few ticks (plant growth, energy)
//   Light    — every tick, GPU-friendly separate pass
//
// In C this would be:
//   typedef struct {
//       // Physics
//       uint8_t water, mineral, temperature, vector;
//       // Biology
//       uint8_t species, tile_type, growth, energy;
//       // Root reference (plant tiles only; ignored when species == 0)
//       int16_t root_row, root_col;
//       // Light
//       uint8_t light, sunlight;
//       uint16_t _pad;
//   } Cell;  // sizeof(Cell) == 16
//
// In Rust we use #[repr(C)] to guarantee this exact layout (no reordering,
// no hidden padding beyond the explicit _pad). The result is ABI-compatible
// with the C struct above.

// ── Tile type constants ───────────────────────────────────────────────────────
//
// These are the discrete plant tile types from the prototype.
// Stored in Cell::tile_type. Only meaningful when Cell::species > 0.
//
// In C: #define TILE_AIR 0  etc.
// In Rust: pub const TILE_AIR: u8 = 0  is identical — a named compile-time integer.

pub const TILE_AIR:    u8 = 0; // not a plant tile
pub const TILE_ROOT:   u8 = 1; // anchors the plant; absorbs water and minerals
pub const TILE_STEM:   u8 = 2; // structural; transports energy upward
pub const TILE_LEAF:   u8 = 3; // photosynthesizes; produces energy
pub const TILE_FLOWER: u8 = 4; // reproductive; seed dispersal

// ── Threshold constants ───────────────────────────────────────────────────────
//
// These define where on the 0-255 scale the sim rules change physical regime.
// They are tuning values — adjust without touching any other code.

// Temperature (0 = absolute cold, 128 = ambient/room temp, 255 = molten)
pub const TEMP_AMBIENT:   u8 = 128;
pub const TEMP_FREEZE:    u8 = 64;  // water freezes below this
pub const TEMP_BOIL:      u8 = 192; // water vaporizes above this
pub const TEMP_MELT_ROCK: u8 = 240; // mineral melts above this

// Water content (0 = bone dry, 255 = fully saturated)
pub const WATER_TRACE:     u8 = 20;
pub const WATER_DAMP:      u8 = 80;
pub const WATER_WET:       u8 = 150;
pub const WATER_SATURATED: u8 = 220;

// Mineral content (0 = vacuum, 255 = dense rock/ore)
pub const MINERAL_TRACE: u8 = 20;
pub const MINERAL_SOIL:  u8 = 80;
pub const MINERAL_DIRT:  u8 = 140;
pub const MINERAL_ROCK:  u8 = 200;
pub const MINERAL_HARD:  u8 = 240;

// ── Vector byte helpers ───────────────────────────────────────────────────────
//
// The vector byte encodes a 2D velocity as two signed 4-bit (i4) nibbles.
//   high nibble (bits 7:4): dx  — horizontal,  -8..+7, positive = right
//   low  nibble (bits 3:0): dy  — vertical,    -8..+7, positive = down
//
// To move a particle one step: next_x = x + cell.dx(), next_y = y + cell.dy()
// Still / no velocity: vector == 0x00

/// Encode a signed value (-8..=7) into a 4-bit two's-complement nibble.
/// In C: `(uint8_t)(v & 0xF)` after clamping.
#[inline]
pub fn encode_i4(v: i8) -> u8 {
    (v.clamp(-8, 7) as u8) & 0xF
}

/// Decode a 4-bit nibble back to signed (-8..=7).
/// Sign-extends bit 3: values 8-15 become -8 to -1.
#[inline]
pub fn decode_i4(nibble: u8) -> i8 {
    let n = (nibble & 0xF) as i8;
    if n >= 8 { n - 16 } else { n }
}

/// Pack dx and dy into a single vector byte.
#[inline]
pub fn make_vector(dx: i8, dy: i8) -> u8 {
    (encode_i4(dx) << 4) | encode_i4(dy)
}

// ── Cell struct ───────────────────────────────────────────────────────────────

/// A single simulation cell. 16 bytes. C-compatible layout via #[repr(C)].
///
/// All-zero is a valid, meaningful state: vacuum/air on a cold dead planet.
///   water=0    → bone dry
///   mineral=0  → no solid matter (pure air/vacuum)
///   temp=0     → very cold (planet starts dead and frozen)
///   species=0  → not organic
///   light=0    → dark
///
// #[repr(C)] tells Rust: lay out fields in declaration order, same as C would.
// Without it, Rust is free to reorder fields for alignment — fine for pure Rust
// but would break any C FFI or manual byte-offset calculations.
#[repr(C)]
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct Cell {

    // ── Physics layer (bytes 0–3) ─────────────────────────────────────────────
    // Continuous quantities — behavior emerges from their ratios, not type IDs.

    /// Water / moisture content. 0=bone dry, 255=fully saturated.
    /// For air cells: atmospheric moisture.
    /// For soil cells: held water (drives plant root uptake).
    pub water: u8,

    /// Mineral density / concentration. 0=vacuum, 255=dense hard rock.
    /// Compaction spectrum: trace dust → soil → dirt → rock → hard rock.
    pub mineral: u8,

    /// Thermal state. 0=frozen, 128=ambient, 255=molten.
    /// Drives: ice melt, water evaporation, convection, lava flow.
    pub temperature: u8,

    /// Velocity, packed as two i4 nibbles. See make_vector() / dx() / dy().
    /// high nibble = dx (-8..+7), low nibble = dy (-8..+7).
    pub vector: u8,

    // ── Biology layer (bytes 4–7) ─────────────────────────────────────────────
    // Discrete plant/organism data. Only meaningful when species > 0.

    /// Species identifier. 0 = inorganic (most cells).
    /// 1-255 = species ID; maps to a species blueprint in assets/data/species/.
    pub species: u8,

    /// Plant tile type. One of the TILE_* constants above.
    /// Only meaningful when species > 0.
    pub tile_type: u8,

    /// Growth stage (0=seed/sprout, 255=fully mature).
    /// Also used as general vitality for non-plant organisms.
    pub growth: u8,

    /// Stored energy (0=depleted/dying, 255=thriving).
    /// Produced by LEAF tiles via photosynthesis, consumed by growth.
    pub energy: u8,

    // ── Root reference (bytes 8–11) ───────────────────────────────────────────
    // For plant tiles: absolute grid coordinates of this plant's ROOT tile.
    // This matches the prototype's (rr, rc) fields — lets any plant tile
    // look up its root in O(1) without tree traversal.
    //
    // When species == 0, these fields are meaningless and should be ignored.
    // Value (0, 0) when species == 0 (all-zero default).

    /// Absolute row index of this plant's root tile (0..511).
    pub root_row: i16,

    /// Absolute column index of this plant's root tile (0..511).
    pub root_col: i16,

    // ── Light layer (bytes 12–15) ─────────────────────────────────────────────

    /// Computed light level at this cell (0=dark, 255=full brightness).
    /// Written by the lighting pass each frame; read by the renderer.
    pub light: u8,

    /// Direct sunlight reaching this cell (0=none, 255=full sun).
    /// Separate from `light` so artificial light and sunlight can be combined.
    pub sunlight: u8,

    /// Explicit padding to make sizeof(Cell) == 16 exactly.
    /// In C you'd write `uint16_t _pad;` for the same reason.
    pub _pad: u16,
}

// sizeof check — compile-time assert that Cell is exactly 16 bytes.
// If you add fields and forget to update _pad, this will fail to compile.
//
// In C: `_Static_assert(sizeof(Cell) == 16, "Cell size mismatch");`
// In Rust: const assertion using a zero-size type trick.
const _SIZE_CHECK: () = {
    // std::mem::size_of is const-evaluable in Rust.
    assert!(std::mem::size_of::<Cell>() == 16, "Cell must be exactly 16 bytes");
};

// ── Cell methods ──────────────────────────────────────────────────────────────

impl Cell {
    /// All-zero cell. Represents vacuum/air on a cold, dead planet.
    /// A freshly calloc'd grid buffer is a valid empty world.
    pub const AIR: Cell = Cell {
        water: 0, mineral: 0, temperature: 0, vector: 0,
        species: 0, tile_type: TILE_AIR, growth: 0, energy: 0,
        root_row: 0, root_col: 0,
        light: 0, sunlight: 0, _pad: 0,
    };

    // ── Generic constructor ───────────────────────────────────────────────────

    /// Create a cell with specific physics layer values. Biology and light
    /// layers default to inorganic/dark (zero).
    ///
    /// In C: `(Cell){ .water=w, .mineral=m, .temperature=t, .vector=v }` with
    /// the remaining fields zero-initialized.
    /// In Rust: `Cell { water, mineral, temperature, vector, ..Cell::AIR }` —
    /// the `..Cell::AIR` spread copies all other fields from AIR (all zero).
    pub fn new(water: u8, mineral: u8, temperature: u8, vector: u8) -> Cell {
        Cell { water, mineral, temperature, vector, ..Cell::AIR }
    }

    // ── Field mutators ────────────────────────────────────────────────────────
    //
    // These return a modified copy. Cell is Copy (like a plain integer), so
    // returning a new value is idiomatic — no &mut needed.
    // `..self` is Rust struct update syntax: copies all fields not listed.
    // In C you'd write: Cell tmp = *self; tmp.water = v; return tmp;

    #[inline] pub fn with_water      (self, water: u8)       -> Cell { Cell { water,       ..self } }
    #[inline] pub fn with_mineral    (self, mineral: u8)     -> Cell { Cell { mineral,     ..self } }
    #[inline] pub fn with_temperature(self, temperature: u8) -> Cell { Cell { temperature, ..self } }
    #[inline] pub fn with_vector     (self, vector: u8)      -> Cell { Cell { vector,      ..self } }

    // ── Preset constructors ───────────────────────────────────────────────────

    /// Atmospheric air with a moisture level, at ambient temperature.
    pub fn air(moisture: u8) -> Cell {
        Cell { water: moisture, temperature: TEMP_AMBIENT, ..Cell::AIR }
    }

    /// Liquid water at ambient temperature.
    pub fn new_water() -> Cell {
        Cell { water: 255, temperature: TEMP_AMBIENT, ..Cell::AIR }
    }

    /// Steam / water vapor.
    pub fn steam() -> Cell {
        Cell { water: 200, temperature: TEMP_BOIL + 20, ..Cell::AIR }
    }

    /// Ice — high water, below-freeze temperature.
    pub fn ice() -> Cell {
        Cell { water: 240, mineral: 10, temperature: TEMP_FREEZE - 20, ..Cell::AIR }
    }

    /// Loose dust / fine silt.
    pub fn dust() -> Cell {
        Cell { mineral: MINERAL_TRACE + 10, temperature: TEMP_AMBIENT, ..Cell::AIR }
    }

    /// Loose soil — medium mineral, trace moisture.
    pub fn loose_soil() -> Cell {
        Cell { water: WATER_TRACE, mineral: MINERAL_SOIL + 10, temperature: TEMP_AMBIENT, ..Cell::AIR }
    }

    /// Packed dirt.
    pub fn packed_dirt() -> Cell {
        Cell { water: WATER_DAMP, mineral: MINERAL_DIRT, temperature: TEMP_AMBIENT, ..Cell::AIR }
    }

    /// Solid rock.
    pub fn rock() -> Cell {
        Cell { mineral: MINERAL_ROCK + 10, temperature: TEMP_AMBIENT, ..Cell::AIR }
    }

    /// Hard rock — dense, nearly impermeable.
    pub fn hard_rock() -> Cell {
        Cell { mineral: MINERAL_HARD, temperature: TEMP_AMBIENT, ..Cell::AIR }
    }

    /// Mud — high water, medium-high mineral.
    pub fn mud() -> Cell {
        Cell { water: WATER_WET, mineral: MINERAL_SOIL + 40, temperature: TEMP_AMBIENT, ..Cell::AIR }
    }

    /// Molten rock / lava.
    pub fn lava() -> Cell {
        Cell { mineral: MINERAL_ROCK, temperature: TEMP_MELT_ROCK, ..Cell::AIR }
    }

    // ── Plant constructors ────────────────────────────────────────────────────

    /// A plant tile. `tile_type` is one of TILE_ROOT / TILE_STEM / TILE_LEAF / TILE_FLOWER.
    /// `root_row` and `root_col` are the absolute grid coords of the plant's root.
    pub fn plant_tile(species: u8, tile_type: u8, growth: u8, energy: u8,
                      root_row: i16, root_col: i16) -> Cell {
        Cell {
            species, tile_type, growth, energy,
            root_row, root_col,
            temperature: TEMP_AMBIENT,
            ..Cell::AIR
        }
    }

    // ── Velocity helpers ──────────────────────────────────────────────────────

    /// Horizontal velocity component (-8..=7). Positive = right.
    #[inline]
    pub fn dx(self) -> i8 { decode_i4(self.vector >> 4) }

    /// Vertical velocity component (-8..=7). Positive = down.
    #[inline]
    pub fn dy(self) -> i8 { decode_i4(self.vector & 0xF) }

    /// Return a copy with velocity set.
    #[inline]
    pub fn with_velocity(self, dx: i8, dy: i8) -> Cell {
        Cell { vector: make_vector(dx, dy), ..self }
    }

    // ── Derived state queries ─────────────────────────────────────────────────
    //
    // Physical regime is derived from value thresholds, not from a type tag.
    // These are helper predicates the sim rules call frequently.

    /// True if this cell is essentially empty air (negligible water and mineral).
    #[inline]
    pub fn is_air(self) -> bool {
        self.water < WATER_TRACE && self.mineral < MINERAL_TRACE
    }

    /// True if cell behaves as liquid water (high water, low mineral, not frozen/boiling).
    #[inline]
    pub fn is_liquid(self) -> bool {
        self.water >= WATER_WET
            && self.mineral < MINERAL_TRACE
            && self.temperature >= TEMP_FREEZE
            && self.temperature < TEMP_BOIL
    }

    /// True if cell is water vapor / steam.
    #[inline]
    pub fn is_vapor(self) -> bool {
        self.water >= WATER_WET
            && self.mineral < MINERAL_TRACE
            && self.temperature >= TEMP_BOIL
    }

    /// True if frozen water / ice.
    #[inline]
    pub fn is_ice(self) -> bool {
        self.water >= WATER_WET
            && self.mineral < MINERAL_SOIL
            && self.temperature < TEMP_FREEZE
    }

    /// True if cell is a solid (high mineral, low water, below melting point).
    #[inline]
    pub fn is_solid(self) -> bool {
        self.mineral >= MINERAL_DIRT
            && self.water < WATER_DAMP
            && self.temperature < TEMP_MELT_ROCK
    }

    /// True if molten (high mineral at extreme heat).
    #[inline]
    pub fn is_molten(self) -> bool {
        self.mineral >= MINERAL_ROCK && self.temperature >= TEMP_MELT_ROCK
    }

    /// True if loose powder / granular material (will fall and spread).
    #[inline]
    pub fn is_powder(self) -> bool {
        self.mineral >= MINERAL_TRACE
            && self.mineral < MINERAL_DIRT
            && self.water < WATER_WET
            && self.temperature < TEMP_BOIL
    }

    /// True if this is a living plant tile.
    #[inline]
    pub fn is_plant(self) -> bool {
        self.species > 0 && self.tile_type != TILE_AIR
    }

    /// Effective density for displacement logic. Heavier cells sink through lighter.
    /// Integer arithmetic only — mineral dominates (rock >> water >> air).
    #[inline]
    pub fn density(self) -> u32 {
        (self.mineral as u32) * 3 + (self.water as u32)
    }

    /// True if this cell has activity worth keeping a chunk alive for.
    /// The chunk manager uses this during the daily pass to decide keep-alive vs dormancy.
    ///
    /// A cell is "active" if it:
    ///   - has velocity (moving particle)
    ///   - is a fluid that will try to move next tick (liquid or vapor)
    ///   - is a living organism with energy remaining
    #[inline]
    pub fn is_active(self) -> bool {
        self.vector != 0                          // particle is moving
            || self.is_liquid()                   // water will try to flow
            || self.is_vapor()                    // steam will try to rise
            || (self.species > 0 && self.energy > 0) // living plant or creature
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cell_is_16_bytes() {
        assert_eq!(std::mem::size_of::<Cell>(), 16);
    }

    #[test]
    fn air_is_all_zero() {
        // All-zero must be a valid empty-world cell so calloc() initializes correctly.
        assert_eq!(Cell::AIR, Cell::default());
        assert!(Cell::AIR.is_air());
    }

    #[test]
    fn preset_states_classify_correctly() {
        assert!(Cell::new_water().is_liquid(),  "water should be liquid");
        assert!(Cell::steam().is_vapor(),       "steam should be vapor");
        assert!(Cell::ice().is_ice(),           "ice should be ice");
        assert!(Cell::rock().is_solid(),        "rock should be solid");
        assert!(Cell::hard_rock().is_solid(),   "hard rock should be solid");
        assert!(Cell::lava().is_molten(),       "lava should be molten");
        assert!(Cell::dust().is_powder(),       "dust should be powder");
        assert!(Cell::loose_soil().is_powder(), "loose soil should be powder");
    }

    #[test]
    fn density_ordering() {
        assert!(Cell::hard_rock().density() > Cell::new_water().density());
        assert!(Cell::new_water().density() > Cell::air(0).density());
    }

    #[test]
    fn plant_tile_fields() {
        let root = Cell::plant_tile(1, TILE_ROOT, 0, 200, 30, 40);
        assert!(root.is_plant());
        assert_eq!(root.species, 1);
        assert_eq!(root.tile_type, TILE_ROOT);
        assert_eq!(root.root_row, 30);
        assert_eq!(root.root_col, 40);
        assert_eq!(root.energy, 200);
    }

    #[test]
    fn i4_round_trip() {
        for v in -8i8..=7 {
            assert_eq!(decode_i4(encode_i4(v)), v, "i4 round-trip failed for v={v}");
        }
    }

    #[test]
    fn velocity_round_trip() {
        let c = Cell::AIR.with_velocity(-3, 5);
        assert_eq!(c.dx(), -3);
        assert_eq!(c.dy(), 5);
        // Still cell has zero vector byte
        assert_eq!(Cell::AIR.with_velocity(0, 0).vector, 0);
    }

    #[test]
    fn struct_update_syntax_does_not_corrupt() {
        // The `..Cell::AIR` spread syntax (like C99 designated initializers)
        // must not accidentally zero out explicitly set fields.
        let c = Cell { water: 99, mineral: 42, ..Cell::AIR };
        assert_eq!(c.water, 99);
        assert_eq!(c.mineral, 42);
        assert_eq!(c.temperature, 0); // AIR default
        assert_eq!(c.species, 0);
    }
}
