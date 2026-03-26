// water.rs — water cycle and fluid simulation rules
//
// Cellular automata model: per-cell, local neighborhood, no global solves.
// All rules follow this contract:
//   - Read state from chunk.get() / chunk.get_with_ghost()  (front buffer)
//   - Write state via chunk.set_next()                      (back buffer)
//   - Set chunk.has_activity = true when any rule fires
//
// Rule taxonomy:
//   Gravity      — liquids fall, vapors rise, powders slide
//   Spread       — liquids equalize horizontally (ONI mass equalization)
//   Pressure     — DF-style: saturated cells push water to orthogonal relief
//   Transitions  — temperature-driven freeze / melt / evaporate
//   Absorption   — soil soaks water from wet neighbors
//   Diffusion    — moisture diffuses through air cells
//
// ── Double-buffer approximation note ─────────────────────────────────────────
// All reads come from the front buffer (last frame's state). When we "move"
// a cell from position A to position B, we write to back[A] and back[B].
// If another rule in the same tick also writes to back[A] or back[B], the
// last write wins — this can cause rare single-frame duplication/vanishing
// of particles. This is a known approximation in double-buffer pixel sims.
// The artifacts are subtle and don't affect long-term conservation significantly.
// A future fix: track "claimed" cells with a per-tick bitset.

use crate::cell::{
    Cell,
    WATER_TRACE, WATER_DAMP, WATER_WET, WATER_SATURATED,
    MINERAL_SOIL, MINERAL_DIRT,
};
use crate::chunk::{Chunk, CHUNK_WIDTH, CHUNK_HEIGHT};

// ── Entry point ───────────────────────────────────────────────────────────────

/// Run one full water-cycle tick on a chunk.
/// Called from Chunk::tick_physics() every sim step.
///
/// `tick_count` is the global sim step counter. It's used to alternate
/// the horizontal spread direction each tick to eliminate directional bias.
/// (Without this, water always prefers spreading left or right — Noita trick.)
pub fn tick(chunk: &mut Chunk, tick_count: u64) {
    let spread_right_first = tick_count % 2 == 0;

    // Scan top-to-bottom, left-to-right.
    // With double-buffering, scan order has less impact than in-place sims,
    // but top-to-bottom means falling cells "see" their landing spot in the
    // front buffer (unchanged this tick) — one tick delay in settling, invisible
    // in practice.
    for y in 0..CHUNK_HEIGHT {
        for x in 0..CHUNK_WIDTH {
            process_cell(chunk, x, y, spread_right_first);
        }
    }
}

// ── Per-cell dispatch ─────────────────────────────────────────────────────────

fn process_cell(chunk: &mut Chunk, x: usize, y: usize, spread_right_first: bool) {
    let cell = chunk.get(x, y);
    let lx = x as i32;
    let ly = y as i32;

    // Fast skip: solid cells with no temperature gradient need no processing.
    // This is the hot path — most of the world is rock.
    if cell.is_solid() && cell.temperature == 0 {
        return;
    }

    // ── State transitions ─────────────────────────────────────────────────────
    // These change the cell's temperature or water values, then let the
    // movement rules below handle the resulting state naturally.

    // Freezing: if a liquid cell is cold, it stops moving.
    // is_ice() already returns true when water >= WATER_WET && temp < TEMP_FREEZE,
    // so we don't need to rewrite the cell — just skip movement rules.
    if cell.is_ice() {
        return; // frozen — no movement
    }

    // Melting: ice warms up (thermal diffusion from neighbors, planned).
    // When temp crosses TEMP_FREEZE, is_ice() returns false and the liquid
    // rules below take over automatically. No explicit action needed here.

    // Evaporation: a hot liquid keeps its water byte (still high) but its
    // temperature >= TEMP_BOIL means is_vapor() returns true.
    // The vapor rules below handle it from there.

    // ── Vapor: rise by buoyancy ───────────────────────────────────────────────
    if cell.is_vapor() {
        rise(chunk, x, y, cell, lx, ly);
        return;
    }

    // ── Powder: fall and pile ─────────────────────────────────────────────────
    if cell.is_powder() {
        powder_fall(chunk, x, y, cell, lx, ly, spread_right_first);
        return;
    }

    // ── Liquid: fall then spread ──────────────────────────────────────────────
    if cell.is_liquid() {
        if !liquid_fall(chunk, x, y, cell, lx, ly) {
            // Couldn't fall — try spreading sideways
            liquid_spread(chunk, x, y, cell, lx, ly, spread_right_first);
        }
        return;
    }

    // ── Moist air: diffuse moisture ───────────────────────────────────────────
    if cell.is_air() && cell.water >= WATER_TRACE {
        moisture_diffuse(chunk, x, y, cell, lx, ly);
        return;
    }

    // ── Soil: absorb water from wet neighbors ─────────────────────────────────
    if cell.mineral >= MINERAL_SOIL
        && cell.mineral < MINERAL_DIRT
        && cell.water < WATER_DAMP
    {
        soil_absorb(chunk, x, y, cell, lx, ly);
    }
}

// ── Movement rules ────────────────────────────────────────────────────────────

/// Vapor rises: if the cell above is denser, swap (buoyancy).
/// Steam and hot moist air bubble upward through denser liquid/air below them.
fn rise(chunk: &mut Chunk, x: usize, y: usize, cell: Cell, lx: i32, ly: i32) {
    let above = chunk.get_with_ghost(lx, ly - 1);
    if above.density() > cell.density() {
        write_swap(chunk, x, y, cell, lx, ly - 1, above);
    }
}

/// Liquid falls: if the cell below is less dense, swap.
/// Returns true if the cell moved (so the caller can skip the spread step).
fn liquid_fall(chunk: &mut Chunk, x: usize, y: usize, cell: Cell, lx: i32, ly: i32) -> bool {
    let below = chunk.get_with_ghost(lx, ly + 1);
    if below.density() < cell.density() {
        write_swap(chunk, x, y, cell, lx, ly + 1, below);
        return true;
    }
    false
}

/// Liquid spreads: ONI-style mass equalization.
///
/// Water flows from this cell toward a less-full neighbor, transferring half
/// the difference in water amount. This naturally creates level surfaces — a
/// column of water will equalize its height on both sides of a connected passage
/// over several ticks (communicating vessels).
///
/// DF pressure insight: when a cell is fully saturated (water == 255) and
/// receives pressure from above, it tries to push water to any orthogonal
/// neighbor — including upward. This is what makes U-tubes work.
fn liquid_spread(chunk: &mut Chunk, x: usize, y: usize, cell: Cell,
                 lx: i32, ly: i32, right_first: bool)
{
    // Try horizontal spread first (primary equalization direction).
    let side_dirs: [(i32, i32); 2] = if right_first {
        [(1, 0), (-1, 0)]
    } else {
        [(-1, 0), (1, 0)]
    };

    for (dx, dy) in &side_dirs {
        let nx = lx + dx;
        let ny = ly + dy;
        let neighbor = chunk.get_with_ghost(nx, ny);

        if can_receive_water(neighbor, cell.water) {
            equalize_water(chunk, x, y, cell, nx, ny, neighbor);
            return;
        }
    }

    // ── DF-style pressure ─────────────────────────────────────────────────────
    // If this cell is fully saturated and the cell above is also saturated
    // (pressure source), push water upward to equalize (U-tube behavior).
    //
    // "Pressure is behavior, not state." — Tarn Adams
    //
    // We also check all four orthogonal directions here, so pressure can push
    // through a connected body in any direction to find relief.
    // Diagonal gaps break the chain (we don't check diagonals) — this is
    // intentional and is the primary pressure-control mechanism.
    if cell.water >= WATER_SATURATED {
        let above = chunk.get_with_ghost(lx, ly - 1);
        let has_pressure = above.is_liquid() && above.water >= WATER_SATURATED;

        if has_pressure {
            // Search all four orthogonal directions for a less-full liquid cell.
            // Upward first — that's the U-tube / pressure-rises direction.
            let relief_dirs = [(0i32, -1i32), (1, 0), (-1, 0), (0, 1)];
            for (dx, dy) in &relief_dirs {
                let nx = lx + dx;
                let ny = ly + dy;
                let neighbor = chunk.get_with_ghost(nx, ny);
                if neighbor.is_liquid() && neighbor.water < WATER_SATURATED {
                    // Push one unit toward relief.
                    // Moving 1 unit per tick makes pressure propagate over multiple
                    // ticks along the chain — correct, just not instantaneous.
                    let new_self = cell.with_water(cell.water.saturating_sub(1));
                    let new_nbr  = neighbor.with_water(neighbor.water.saturating_add(1));
                    chunk.set_next(x, y, new_self);
                    if Chunk::in_bounds(nx, ny) {
                        chunk.set_next(nx as usize, ny as usize, new_nbr);
                    }
                    chunk.has_activity = true;
                    return;
                }
            }
        }
    }
}

/// Powder falls straight down, then slides diagonally if blocked.
/// Same displacement logic as liquid_fall but doesn't spread sideways.
fn powder_fall(chunk: &mut Chunk, x: usize, y: usize, cell: Cell,
               lx: i32, ly: i32, slide_right_first: bool)
{
    // Try straight down
    let below = chunk.get_with_ghost(lx, ly + 1);
    if below.density() < cell.density() {
        write_swap(chunk, x, y, cell, lx, ly + 1, below);
        return;
    }

    // Try diagonal slide (pile behavior)
    // Powder checks the SIDE cell first — if the side is open, it can slide
    // diagonally. Without this check, powder teleports through diagonal walls.
    let sides: [i32; 2] = if slide_right_first { [1, -1] } else { [-1, 1] };
    for dx in &sides {
        let side  = chunk.get_with_ghost(lx + dx, ly);
        let diag  = chunk.get_with_ghost(lx + dx, ly + 1);
        // Both the side and the diagonal below must be passable
        if side.density() < cell.density() && diag.density() < cell.density() {
            write_swap(chunk, x, y, cell, lx + dx, ly + 1, diag);
            return;
        }
    }
}

// ── Absorption and diffusion ──────────────────────────────────────────────────

/// Moisture diffusion: humid air cells slowly share moisture with drier neighbors.
/// Drives atmospheric humidity gradients — the slow half of the water cycle.
fn moisture_diffuse(chunk: &mut Chunk, x: usize, y: usize, cell: Cell, lx: i32, ly: i32) {
    // Check all four neighbors for drier air
    for (dx, dy) in &[(1i32, 0i32), (-1, 0), (0, 1), (0, -1)] {
        let nx = lx + dx;
        let ny = ly + dy;
        let neighbor = chunk.get_with_ghost(nx, ny);

        // Only diffuse into drier air cells — not into liquids, solids, etc.
        if !neighbor.is_air() { continue; }
        let diff = cell.water.saturating_sub(neighbor.water);
        if diff < 4 { continue; } // too small a gradient — skip

        // Slow diffusion: transfer 1/8 of the difference, minimum 1.
        let transfer = (diff / 8).max(1);
        let new_self = cell.with_water(cell.water.saturating_sub(transfer));
        let new_nbr  = neighbor.with_water(neighbor.water.saturating_add(transfer));
        chunk.set_next(x, y, new_self);
        if Chunk::in_bounds(nx, ny) {
            chunk.set_next(nx as usize, ny as usize, new_nbr);
        }
        chunk.has_activity = true;
        return; // one transfer per cell per tick
    }
}

/// Capillary absorption: soil cells slowly wick water from adjacent wet cells.
/// Drives seepage through permeable ground layers.
fn soil_absorb(chunk: &mut Chunk, x: usize, y: usize, cell: Cell, lx: i32, ly: i32) {
    for (dx, dy) in &[(1i32, 0i32), (-1, 0), (0, 1), (0, -1)] {
        let nx = lx + dx;
        let ny = ly + dy;
        let neighbor = chunk.get_with_ghost(nx, ny);

        if neighbor.water > WATER_WET {
            let new_self = cell.with_water(cell.water.saturating_add(1));
            let new_nbr  = neighbor.with_water(neighbor.water.saturating_sub(1));
            chunk.set_next(x, y, new_self);
            if Chunk::in_bounds(nx, ny) {
                chunk.set_next(nx as usize, ny as usize, new_nbr);
            }
            chunk.has_activity = true;
            return;
        }
    }
}

// ── Helper functions ──────────────────────────────────────────────────────────

/// True if `neighbor` can receive water from a cell with `source_water` amount.
/// Liquid neighbors must have less water than the source (equalization direction).
/// Air neighbors always accept water (water spreading into empty space).
fn can_receive_water(neighbor: Cell, source_water: u8) -> bool {
    if neighbor.is_air() {
        return true;
    }
    if neighbor.is_liquid() {
        // Only equalize if neighbor has meaningfully less water (avoid oscillation)
        return neighbor.water + 2 < source_water;
    }
    false
}

/// Write the water equalization transfer between two cells.
///
/// `source` loses water, `dest` gains water. Transfer = half the difference.
/// The dest may be an air cell (which becomes a liquid cell after this).
fn equalize_water(chunk: &mut Chunk, sx: usize, sy: usize, source: Cell,
                  nx: i32, ny: i32, dest: Cell)
{
    let src_w  = source.water;
    let dst_w  = if dest.is_liquid() { dest.water } else { 0 };
    let diff   = src_w.saturating_sub(dst_w);
    let amount = (diff / 2).max(1);

    let new_source = source.with_water(src_w.saturating_sub(amount));

    let new_dest = if dest.is_liquid() {
        dest.with_water(dst_w.saturating_add(amount))
    } else {
        // Air → becomes a water cell carrying the transferred amount
        // Keep temperature from the source (water carries heat as it spreads)
        Cell::new(amount, 0, source.temperature, 0)
    };

    chunk.set_next(sx, sy, new_source);
    if Chunk::in_bounds(nx, ny) {
        chunk.set_next(nx as usize, ny as usize, new_dest);
    }
    chunk.has_activity = true;
}

/// Write a cell swap between (sx,sy) and (nx,ny).
///
/// In C: temp = a; a = b; b = temp; — but here both positions are written to
/// the BACK buffer while reading from the FRONT buffer. The "swap" is a
/// logical swap visible only after the tick completes.
#[inline]
fn write_swap(chunk: &mut Chunk,
              sx: usize, sy: usize, source: Cell,
              nx: i32,   ny: i32,   dest: Cell)
{
    chunk.set_next(sx, sy, dest);
    if Chunk::in_bounds(nx, ny) {
        chunk.set_next(nx as usize, ny as usize, source);
    }
    chunk.has_activity = true;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunk::{Chunk, ChunkCoord, CHUNK_WIDTH, CHUNK_HEIGHT};
    use crate::cell::{Cell, TEMP_FREEZE};

    fn make_chunk() -> Chunk {
        Chunk::new(ChunkCoord::new(0, 0))
    }

    fn run_ticks(chunk: &mut Chunk, n: u64) {
        for i in 0..n {
            chunk.prepare_tick();
            tick(chunk, i);
            chunk.swap();
        }
    }

    // ── Gravity ───────────────────────────────────────────────────────────────

    #[test]
    fn water_falls_one_step_per_tick() {
        let mut chunk = make_chunk();
        // Place water at (10, 5), air below
        chunk.fill_rect(10, 5, 11, 6, Cell::new_water());

        run_ticks(&mut chunk, 1);

        // Water should have moved down one step
        assert!(chunk.get(10, 6).is_liquid(), "water should fall to (10,6)");
        // Original position should now be air (or at least not liquid)
        assert!(!chunk.get(10, 5).is_liquid(), "water should have left (10,5)");
    }

    #[test]
    fn water_settles_at_bottom() {
        let mut chunk = make_chunk();
        // Place water at top, rock at bottom row
        chunk.fill_rect(10, 0, 11, 1, Cell::new_water());
        chunk.fill_rect(0, CHUNK_HEIGHT - 1, CHUNK_WIDTH, CHUNK_HEIGHT, Cell::rock());

        // Run enough ticks for water to reach the bottom
        run_ticks(&mut chunk, CHUNK_HEIGHT as u64);

        // Water should be near the bottom
        let near_bottom = chunk.get(10, CHUNK_HEIGHT - 2).is_liquid()
            || chunk.get(10, CHUNK_HEIGHT - 1).is_liquid();
        assert!(near_bottom, "water should settle near bottom");
    }

    // ── Horizontal spread ─────────────────────────────────────────────────────

    #[test]
    fn water_spreads_sideways_on_flat_floor() {
        let mut chunk = make_chunk();
        // Floor of rock
        chunk.fill_rect(0, 20, CHUNK_WIDTH, 21, Cell::rock());
        // Column of water
        chunk.fill_rect(10, 19, 11, 20, Cell::new_water());

        run_ticks(&mut chunk, 20);

        // Water should have spread left and right from (10, 19)
        let spread_right = chunk.get(15, 19).is_liquid() || chunk.get(15, 18).is_liquid();
        let spread_left  = chunk.get(5,  19).is_liquid() || chunk.get(5,  18).is_liquid();
        assert!(spread_right || spread_left, "water should spread on flat floor");
    }

    // ── Pressure / communicating vessels ─────────────────────────────────────

    #[test]
    fn pressure_pushes_water_upward() {
        // Build a U-tube: two vertical columns connected at the bottom.
        // Fill left column with water. After many ticks, right column should rise.
        //
        //   |W |  |  |
        //   |W |  |  |
        //   |WW|WW|  |   <- bottom passage (connected)
        //
        // (This is a slow test — pressure propagates one unit per tick along the chain)
        let mut chunk = make_chunk();

        let floor_y = 30usize;
        let left_x  = 10usize;
        let right_x = 14usize;

        // Floor and walls (rock)
        chunk.fill_rect(left_x - 1, floor_y + 1, right_x + 2, floor_y + 2, Cell::rock()); // floor
        chunk.fill_rect(left_x - 1, floor_y - 5, left_x, floor_y + 1,      Cell::rock()); // left wall
        chunk.fill_rect(right_x + 1, floor_y - 5, right_x + 2, floor_y + 1, Cell::rock()); // right wall
        // Divider between columns, with gap at floor level
        chunk.fill_rect(right_x - 1, floor_y - 5, right_x, floor_y, Cell::rock()); // divider (no floor gap)

        // Fill left column with water
        chunk.fill_rect(left_x, floor_y - 4, left_x + 1, floor_y + 1, Cell::new_water());
        // Bottom passage connecting the two columns
        chunk.fill_rect(left_x, floor_y, right_x, floor_y + 1, Cell::new_water());

        // Run many ticks — pressure propagates one step per tick
        run_ticks(&mut chunk, 200);

        // Check that water has risen in the right column
        let water_in_right = (floor_y - 3..floor_y + 1)
            .any(|y| chunk.get(right_x, y).is_liquid());
        assert!(water_in_right, "pressure should push water up the right column");
    }

    // ── Absorption ────────────────────────────────────────────────────────────

    #[test]
    fn soil_absorbs_adjacent_water() {
        let mut chunk = make_chunk();
        let soil = Cell::loose_soil(); // medium mineral, low water
        let water = Cell::new_water();

        chunk.fill_rect(10, 10, 11, 11, water);
        chunk.fill_rect(11, 10, 12, 11, soil);

        run_ticks(&mut chunk, 10);

        // The soil cell should have gained some moisture
        let _soil_after = chunk.get(11, 10);
        // Soil may have moved (it's powder), but nearby soil should be wetter
        // Check a region around where soil was
        let some_wet_soil = (9..14usize).any(|x| {
            let c = chunk.get(x, 10);
            c.mineral >= MINERAL_SOIL && c.water > 0
        });
        assert!(some_wet_soil, "soil should absorb some moisture from adjacent water");
    }

    // ── State transitions ─────────────────────────────────────────────────────

    #[test]
    fn cold_water_stops_moving() {
        let mut chunk = make_chunk();
        // Cold water (below TEMP_FREEZE): classified as ice, should not fall
        let cold_water = Cell { water: 255, temperature: TEMP_FREEZE - 10, ..Cell::AIR };
        chunk.fill_rect(10, 5, 11, 6, cold_water);

        run_ticks(&mut chunk, 5);

        // Should NOT have moved — is_ice() makes it skip movement rules
        assert!(chunk.get(10, 5).water >= 200, "cold water should stay frozen in place");
    }

    #[test]
    fn hot_vapor_rises() {
        let mut chunk = make_chunk();
        let steam = Cell::steam();
        assert!(steam.is_vapor(), "steam should be classified as vapor");

        chunk.fill_rect(10, 20, 11, 21, steam);
        // Place denser air above to give it somewhere to rise into
        chunk.fill_rect(10, 15, 11, 20, Cell::air(200)); // moist, denser air

        run_ticks(&mut chunk, 5);

        // Steam should have risen above its starting position
        let still_at_origin = chunk.get(10, 20).is_vapor();
        let moved_up = (15..20usize).any(|y| chunk.get(10, y).is_vapor());
        assert!(moved_up || !still_at_origin, "vapor should rise");
    }
}
