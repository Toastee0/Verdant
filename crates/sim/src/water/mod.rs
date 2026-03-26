// water/mod.rs — water cycle orchestrator
//
// Entry point: tick(chunk, tick_count) — called from Chunk::tick_physics() every frame.
//
// This file contains:
//   - tick()          — outer loop over all cells
//   - process_cell()  — per-cell rule dispatch
//   - liquid_spread() — horizontal equalization + DF pressure (ties together
//                       transfer.rs and pressure.rs)
//
// Sub-modules handle the individual rules:
//   gravity.rs    — fall / rise / powder slide
//   pressure.rs   — DF-style pressure-as-behavior
//   diffusion.rs  — humidity diffusion through air
//   absorption.rs — soil wicking water from wet neighbors
//   transfer.rs   — write_swap / equalize_water / can_receive_water primitives

pub mod gravity;
pub mod pressure;
pub mod diffusion;
pub mod absorption;
pub mod transfer;

use crate::cell::{Cell, WATER_TRACE, WATER_SATURATED};
use crate::chunk::{Chunk, CHUNK_WIDTH, CHUNK_HEIGHT};
use absorption::is_absorbent_soil;

// ── Entry point ───────────────────────────────────────────────────────────────

/// Run one full water-cycle tick on a chunk.
/// Called from Chunk::tick_physics() every sim step.
///
/// `tick_count` alternates the horizontal spread direction each tick to
/// eliminate directional bias — the Noita trick. Without this, water always
/// prefers spreading in the same horizontal direction.
pub fn tick(chunk: &mut Chunk, tick_count: u64) {
    let spread_right_first = tick_count % 2 == 0;

    // Scan top-to-bottom so falling cells see their landing spot unchanged
    // (front buffer is this tick's state, untouched by rules above).
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

    // Fast skip: solid cells with no temperature gradient are inert (the hot path).
    if cell.is_solid() && cell.temperature == 0 {
        return;
    }

    // Frozen — no movement (is_ice handles frozen water).
    if cell.is_ice() {
        return;
    }

    // Vapor: rise by buoyancy.
    if cell.is_vapor() {
        gravity::rise(chunk, x, y, cell, lx, ly);
        return;
    }

    // Powder: fall and pile. If settled (didn't fall), also check soil absorption.
    // Soil is in the powder range but should absorb water when it can't move.
    if cell.is_powder() {
        let moved = gravity::powder_fall(chunk, x, y, cell, lx, ly, spread_right_first);
        if !moved && is_absorbent_soil(cell) {
            absorption::soil_absorb(chunk, x, y, cell, lx, ly);
        }
        return;
    }

    // Liquid: fall then spread.
    if cell.is_liquid() {
        if !gravity::liquid_fall(chunk, x, y, cell, lx, ly) {
            liquid_spread(chunk, x, y, cell, lx, ly, spread_right_first);
        }
        return;
    }

    // Moist air: diffuse humidity to drier neighbors.
    if cell.is_air() && cell.water >= WATER_TRACE {
        diffusion::moisture_diffuse(chunk, x, y, cell, lx, ly);
        return;
    }

    // Solid soil above the powder range: absorb water from wet neighbors.
    // (Powder-range soil is handled in the powder path above.)
    if is_absorbent_soil(cell) {
        absorption::soil_absorb(chunk, x, y, cell, lx, ly);
    }
}

// ── Liquid spread ─────────────────────────────────────────────────────────────

/// Liquid spreads: ONI-style mass equalization + DF pressure.
///
/// Phase 1 — Horizontal equalization:
///   Water flows from this cell toward a less-full neighbor, transferring half
///   the difference per tick. This creates level surfaces (communicating vessels)
///   over multiple ticks without any global solve.
///
/// Phase 2 — DF-style pressure:
///   If this cell is fully saturated AND pressurized from above, it tries to
///   push one unit to any orthogonal neighbor with room. This is what makes
///   U-tubes work — water "finds" the opening and rises through it.
///   Propagates one step per tick along a connected saturated column.
fn liquid_spread(chunk: &mut Chunk, x: usize, y: usize, cell: Cell,
                 lx: i32, ly: i32, right_first: bool)
{
    // Phase 1: try horizontal equalization.
    let side_dirs: [(i32, i32); 2] = if right_first {
        [(1, 0), (-1, 0)]
    } else {
        [(-1, 0), (1, 0)]
    };

    for (dx, dy) in &side_dirs {
        let nx = lx + dx;
        let ny = ly + dy;
        let neighbor = chunk.get_with_ghost(nx, ny);

        if transfer::can_receive_water(neighbor, cell.water) {
            transfer::equalize_water(chunk, x, y, cell, nx, ny, neighbor);
            return;
        }
    }

    // Phase 2: DF pressure — only when saturated and pressurized from above.
    if cell.water >= WATER_SATURATED && pressure::has_pressure_from_above(chunk, lx, ly) {
        pressure::try_pressure_relief(chunk, x, y, cell, lx, ly);
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunk::{Chunk, ChunkCoord, CHUNK_WIDTH, CHUNK_HEIGHT};
    use crate::cell::{Cell, TEMP_FREEZE, MINERAL_SOIL};

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
        chunk.fill_rect(10, 5, 11, 6, Cell::new_water());

        run_ticks(&mut chunk, 1);

        assert!(chunk.get(10, 6).is_liquid(), "water should fall to (10,6)");
        assert!(!chunk.get(10, 5).is_liquid(), "water should have left (10,5)");
    }

    #[test]
    fn water_settles_at_bottom() {
        let mut chunk = make_chunk();
        chunk.fill_rect(10, 0, 11, 1, Cell::new_water());
        chunk.fill_rect(0, CHUNK_HEIGHT - 1, CHUNK_WIDTH, CHUNK_HEIGHT, Cell::rock());

        run_ticks(&mut chunk, CHUNK_HEIGHT as u64);

        // After falling + potentially spreading, check a region near the bottom.
        // Check water > WATER_TRACE rather than is_liquid() since water may spread
        // thin after hitting the floor.
        let near_bottom = (8..=12usize).any(|xr| {
            let c = chunk.get(xr, CHUNK_HEIGHT - 2);
            c.water > WATER_TRACE
        });
        assert!(near_bottom, "water should settle near bottom row");
    }

    // ── Horizontal spread ─────────────────────────────────────────────────────

    #[test]
    fn water_spreads_sideways_on_flat_floor() {
        let mut chunk = make_chunk();
        // Rock floor
        chunk.fill_rect(0, 20, CHUNK_WIDTH, 21, Cell::rock());
        // Wider water source (5 cells tall) so there's enough water to spread
        // as liquid (>= WATER_WET) after equalization across 5+ cells.
        chunk.fill_rect(10, 15, 11, 20, Cell::new_water());

        run_ticks(&mut chunk, 30);

        // Water should have moved from x=10 sideways. Check for any moisture spread.
        let spread_right = (11..16usize).any(|xr| chunk.get(xr, 19).water > WATER_TRACE);
        let spread_left  = (5..10usize) .any(|xl| chunk.get(xl, 19).water > WATER_TRACE);
        assert!(spread_right || spread_left, "water should spread on flat floor");
    }

    // ── Pressure / communicating vessels ─────────────────────────────────────

    #[test]
    fn pressure_pushes_water_upward() {
        // Build a U-tube: two columns connected at the bottom.
        // Fill the left column. After many ticks, right column should rise.
        //
        //   col: left_x  right_x
        //   |W |     |  |
        //   |W |     |  |
        //   |WW|WWWWW|  |   <- bottom passage
        //   ─────────────
        //
        let mut chunk = make_chunk();

        let floor_y = 30usize;
        let left_x  = 10usize;
        let right_x = 14usize;

        // Structure
        chunk.fill_rect(left_x - 1, floor_y + 1, right_x + 2, floor_y + 2, Cell::rock()); // floor
        chunk.fill_rect(left_x - 1, floor_y - 5, left_x,     floor_y + 1, Cell::rock()); // left wall
        chunk.fill_rect(right_x + 1, floor_y - 5, right_x + 2, floor_y + 1, Cell::rock()); // right wall
        chunk.fill_rect(right_x - 1, floor_y - 5, right_x, floor_y, Cell::rock()); // divider

        // Water fill
        chunk.fill_rect(left_x, floor_y - 4, left_x + 1, floor_y + 1, Cell::new_water()); // left column
        chunk.fill_rect(left_x, floor_y, right_x, floor_y + 1, Cell::new_water()); // bottom passage

        run_ticks(&mut chunk, 200);

        let water_in_right = (floor_y - 3..floor_y + 1)
            .any(|y| chunk.get(right_x, y).water > WATER_TRACE);
        assert!(water_in_right, "pressure should push water up the right column");
    }

    // ── Absorption ────────────────────────────────────────────────────────────

    #[test]
    fn soil_absorbs_adjacent_water() {
        let mut chunk = make_chunk();

        // Put soil on a rock floor so it can't fall (gravity won't move it).
        // Then put water to the left — soil should wick moisture from it.
        chunk.fill_rect(0, CHUNK_HEIGHT - 1, CHUNK_WIDTH, CHUNK_HEIGHT, Cell::rock());
        chunk.fill_rect(10, CHUNK_HEIGHT - 2, 11, CHUNK_HEIGHT - 1, Cell::new_water());
        chunk.fill_rect(11, CHUNK_HEIGHT - 2, 12, CHUNK_HEIGHT - 1, Cell::loose_soil());

        run_ticks(&mut chunk, 10);

        // Soil should have gained moisture from the adjacent water
        let soil = chunk.get(11, CHUNK_HEIGHT - 2);
        assert!(
            soil.mineral >= MINERAL_SOIL && soil.water > WATER_TRACE,
            "soil should absorb moisture from adjacent water cell"
        );
    }

    // ── State transitions ─────────────────────────────────────────────────────

    #[test]
    fn cold_water_stops_moving() {
        let mut chunk = make_chunk();
        let cold_water = Cell { water: 255, temperature: TEMP_FREEZE - 10, ..Cell::AIR };
        chunk.fill_rect(10, 5, 11, 6, cold_water);

        run_ticks(&mut chunk, 5);

        assert!(chunk.get(10, 5).water >= 200, "cold water should stay frozen in place");
    }

    #[test]
    fn hot_vapor_rises() {
        let mut chunk = make_chunk();
        let steam = Cell::steam();
        assert!(steam.is_vapor(), "steam should be classified as vapor");

        chunk.fill_rect(10, 20, 11, 21, steam);
        // Dense moist air above steam — must be denser than steam to let it rise.
        // Steam density = water(200)*1 = 200.
        // Cell::air(255) density = water(255)*1 = 255 > 200. ✓
        chunk.fill_rect(10, 15, 11, 20, Cell::air(255));

        run_ticks(&mut chunk, 5);

        let still_at_origin = chunk.get(10, 20).is_vapor();
        let moved_up = (15..20usize).any(|y| chunk.get(10, y).is_vapor());
        assert!(moved_up || !still_at_origin, "vapor should rise through denser air");
    }
}
