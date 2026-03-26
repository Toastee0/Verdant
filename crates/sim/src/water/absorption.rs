// water/absorption.rs — capillary absorption of water into soil
//
// Soil cells slowly wick water from adjacent wet cells.
// This drives seepage through permeable ground layers — the mechanism by which
// rain percolates down to plant root zones and replenishes underground aquifers.
//
// Rate: 1 unit per tick, from the first wet neighbor found.
// Threshold: neighbor must have water > WATER_WET to donate (prevents dry soil
// from stealing the last drops from barely-moist neighbors).

use crate::cell::{Cell, WATER_DAMP, WATER_WET, MINERAL_SOIL, MINERAL_DIRT};
use crate::chunk::Chunk;

/// True if a cell qualifies for soil absorption (soil-like mineral range, not saturated).
///
/// This is the same range checked in the process_cell dispatch in mod.rs — extracted
/// here so gravity.rs can call it after a settled powder fails to fall.
#[inline]
pub fn is_absorbent_soil(cell: Cell) -> bool {
    cell.mineral >= MINERAL_SOIL
        && cell.mineral < MINERAL_DIRT
        && cell.water < WATER_DAMP
}

/// Capillary absorption: pull one unit of water from the first sufficiently wet neighbor.
///
/// Returns true if absorption fired (caller can skip further rules for this tick).
pub fn soil_absorb(chunk: &mut Chunk, x: usize, y: usize, cell: Cell,
                   lx: i32, ly: i32) -> bool
{
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
            return true;
        }
    }

    false
}
