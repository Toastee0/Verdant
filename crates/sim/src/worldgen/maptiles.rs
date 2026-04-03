// worldgen/maptiles.rs — hand-authored map tile loading and stamping
//
// .cave files are drawn in mapedit.html and live in assets/maptiles/.
// They are embedded at compile time via include_str! — no runtime I/O.
//
// Format (produced by mapedit.html):
//   # name: <name>
//   # size: 128 128
//   # spawn: x y   (optional — walker spawn in tile-local coords)
//   <128 rows of 128 chars each>
//
// Cell chars:
//   A = Cell::AIR            carved space
//   R = Cell::rock()         solid rock
//   W = Cell::new_water()    liquid water
//   S = Cell::loose_soil()   packed soil / dirt
//   L = Cell::loose_soil()   loose soil (softer — same Cell for now)
//   M = Cell::hard_rock()    machine metal / base structure (MINERAL_HARD, inert)
//   P = Cell::plant_tile()   pre-placed cave moss
//   H = Cell::new()          hot rock (temp 200)
//   ? = keep geology         do not overwrite whatever worldgen placed here
//
// base.cave ships with M cells (remapped from L via assets/maptiles/remap_base.py).
// tutorial_cave.cave keeps L as loose soil.
//
// Placeholder tiles ship as all '?' — they compile and stamp nothing,
// so geology remains intact until the real art is dropped in.

use crate::cell::{Cell, TILE_LEAF, MINERAL_ROCK};
use crate::chunk::{Chunk, ChunkCoord, CHUNK_WIDTH, CHUNK_HEIGHT};

pub const TILE_SIZE: usize = 128;

// ── Tile placement ────────────────────────────────────────────────────────────
//
// Set these after finalising the drawn tiles.
// OFFSET is the (lx, ly) of the tile's top-left corner within the chunk.
// Chunk is 512×512, so valid offsets are 0..=(512 - 128) = 0..=384.

/// Base block — left tile. Starting area, machine entrance, first shelter.
pub const BASE_CHUNK:  (i32, i32)    = (0, 0);
pub const BASE_OFFSET: (usize, usize) = (128, 192); // left tile, centered in chunk

/// Tutorial cave — right tile, immediately right of base (base_x + 128).
pub const TUTORIAL_CAVE_CHUNK:  (i32, i32)    = (0, 0);
pub const TUTORIAL_CAVE_OFFSET: (usize, usize) = (256, 192); // same row as base

// ── Embedded tile data ────────────────────────────────────────────────────────

static TUTORIAL_CAVE_SRC: &str =
    include_str!("../../../../assets/maptiles/tutorial_cave.cave");

static BASE_SRC: &str =
    include_str!("../../../../assets/maptiles/base_cave.cave");

// ── Tile type ─────────────────────────────────────────────────────────────────

// Sentinel value stored in the tile grid meaning "leave geology alone here".
const KEEP: u8 = 255;

struct MapTile {
    /// Row-major: cells[y * TILE_SIZE + x]
    cells: Vec<u8>,
}

impl MapTile {
    fn parse(src: &str) -> Self {
        let mut cells = vec![KEEP; TILE_SIZE * TILE_SIZE];
        let mut row = 0usize;

        for line in src.lines() {
            if line.starts_with('#') || line.is_empty() {
                continue;
            }
            if row >= TILE_SIZE {
                break;
            }
            for (col, ch) in line.chars().enumerate() {
                if col >= TILE_SIZE {
                    break;
                }
                cells[row * TILE_SIZE + col] = match ch {
                    'A' => 0,
                    'R' => 1,
                    'W' => 2,
                    'S' => 3,
                    'L' => 4,
                    'M' => 5, // machine metal / hard rock
                    'P' => 6, // pre-placed plant (cave moss, species 1)
                    'H' => 7, // hot rock (geothermal / warm zone)
                    _   => KEEP, // '?' and anything unrecognised
                };
            }
            row += 1;
        }

        Self { cells }
    }

    fn to_cell(code: u8) -> Cell {
        match code {
            0 => Cell::AIR,
            1 => Cell::rock(),
            2 => Cell::new_water(),
            3 | 4 => Cell::loose_soil(),
            5 => Cell::hard_rock(), // M — machine metal / base structure
            6 => Cell::plant_tile(1, TILE_LEAF, 200, 150, 0, 0), // P — cave moss, mature
            7 => Cell::new(0, MINERAL_ROCK, 200, 0), // H — hot rock (temp 200)
            _ => Cell::rock(),
        }
    }
}

// ── Stamp ─────────────────────────────────────────────────────────────────────

fn stamp(chunk: &mut Chunk, tile: &MapTile, lx_off: usize, ly_off: usize) {
    for ty in 0..TILE_SIZE {
        let ly = ly_off + ty;
        if ly >= CHUNK_HEIGHT {
            break;
        }
        for tx in 0..TILE_SIZE {
            let lx = lx_off + tx;
            if lx >= CHUNK_WIDTH {
                break;
            }
            let code = tile.cells[ty * TILE_SIZE + tx];
            if code != KEEP {
                chunk.set_front(lx, ly, MapTile::to_cell(code));
            }
        }
    }
}

// ── Screen list ───────────────────────────────────────────────────────────────

/// One authored screen — a 128×128 tile in world space.
/// The camera locks to `center` when the player is inside `[min_x, max_x) × [min_y, max_y)`.
pub struct CaveScreen {
    pub center_x: f32,
    pub center_y: f32,
    pub min_x: f32,
    pub max_x: f32,
    pub min_y: f32,
    pub max_y: f32,
}

/// All hand-authored screens in world order.
/// Extend this whenever a new .cave tile is added.
pub fn all_screens() -> Vec<CaveScreen> {
    fn screen(chunk: (i32, i32), offset: (usize, usize)) -> CaveScreen {
        let ox = chunk.0 * CHUNK_WIDTH as i32 + offset.0 as i32;
        let oy = chunk.1 * CHUNK_HEIGHT as i32 + offset.1 as i32;
        let s = TILE_SIZE as f32;
        CaveScreen {
            center_x: ox as f32 + s / 2.0,
            center_y: oy as f32 + s / 2.0,
            min_x: ox as f32,
            max_x: ox as f32 + s,
            min_y: oy as f32,
            max_y: oy as f32 + s,
        }
    }
    vec![
        screen(BASE_CHUNK, BASE_OFFSET),
        screen(TUTORIAL_CAVE_CHUNK, TUTORIAL_CAVE_OFFSET),
    ]
}

/// Parse the `# spawn: x y` header line from a .cave file.
/// Returns tile-local (lx, ly) or None if not present.
fn parse_spawn(src: &str) -> Option<(usize, usize)> {
    for line in src.lines() {
        if let Some(rest) = line.strip_prefix("# spawn:") {
            let mut parts = rest.split_whitespace();
            let x: usize = parts.next()?.parse().ok()?;
            let y: usize = parts.next()?.parse().ok()?;
            return Some((x, y));
        }
        // Stop at first non-comment line
        if !line.starts_with('#') && !line.is_empty() {
            break;
        }
    }
    None
}

/// Returns the walker spawn point in world cells for the tutorial cave,
/// or None if no spawn was flagged in the .cave file.
pub fn tutorial_cave_spawn() -> Option<(f32, f32)> {
    tile_spawn(TUTORIAL_CAVE_SRC, TUTORIAL_CAVE_CHUNK, TUTORIAL_CAVE_OFFSET)
}

/// Returns the walker spawn point in world cells for the base tile,
/// or None if no spawn was flagged in the .cave file.
pub fn base_cave_spawn() -> Option<(f32, f32)> {
    tile_spawn(BASE_SRC, BASE_CHUNK, BASE_OFFSET)
}

fn tile_spawn(src: &str, chunk: (i32, i32), offset: (usize, usize)) -> Option<(f32, f32)> {
    let (lx, ly) = parse_spawn(src)?;
    let wx = chunk.0 * crate::chunk::CHUNK_WIDTH as i32 + offset.0 as i32 + lx as i32;
    let wy = chunk.1 * crate::chunk::CHUNK_HEIGHT as i32 + offset.1 as i32 + ly as i32;
    Some((wx as f32, wy as f32))
}

/// Called at the end of `generate()` for every non-lava chunk.
/// Stamps any tiles whose chunk coord matches `coord`.
pub fn stamp_if_needed(chunk: &mut Chunk, coord: ChunkCoord) {
    if coord.cx == TUTORIAL_CAVE_CHUNK.0 && coord.cy == TUTORIAL_CAVE_CHUNK.1 {
        let tile = MapTile::parse(TUTORIAL_CAVE_SRC);
        stamp(chunk, &tile, TUTORIAL_CAVE_OFFSET.0, TUTORIAL_CAVE_OFFSET.1);
    }

    if coord.cx == BASE_CHUNK.0 && coord.cy == BASE_CHUNK.1 {
        let tile = MapTile::parse(BASE_SRC);
        stamp(chunk, &tile, BASE_OFFSET.0, BASE_OFFSET.1);
    }
}
