// water/pressure.rs — DF-style pressure-as-behavior
//
// Dwarf Fortress insight: "Pressure is behavior, not state."
// There is no pressure field. Instead, a saturated cell that is being pushed
// from above SEARCHES for orthogonal relief and moves one unit toward it.
//
// U-tube principle: fill the left arm of a U-tube, and over many ticks the
// water propagates along the bottom and rises up the right arm. This emerges
// naturally from the rule below — no special-casing needed.
//
// Key constraint: only ORTHOGONAL directions count.
// Diagonal gaps break the pressure chain — this is intentional and is the
// primary pressure-control mechanism in level design (a single diagonal gap
// vents a pressurized column).

use crate::cell::Cell;
use crate::chunk::Chunk;
use crate::cell::WATER_SATURATED;

/// Try to push one unit of water from a saturated cell to an orthogonal
/// neighbor that has room.
///
/// Called when `cell` is saturated AND the cell above is also saturated
/// (i.e., there is a column of water pushing down).
///
/// Checks all four orthogonal directions, upward first (that's the U-tube /
/// pressure-rises direction). Writes to the back buffer and returns true on
/// success.
pub fn try_pressure_relief(chunk: &mut Chunk, x: usize, y: usize, cell: Cell,
                           lx: i32, ly: i32) -> bool
{
    // Relief directions: up first, then sides, then down.
    // Up is first because that's the physically expected outcome (water rises
    // in the unpressurized arm of a U-tube).
    let dirs = [(0i32, -1i32), (1, 0), (-1, 0), (0, 1)];

    for (dx, dy) in &dirs {
        let nx = lx + dx;
        let ny = ly + dy;
        let neighbor = chunk.get_with_ghost(nx, ny);

        // Push only into an existing liquid cell that has room.
        // We don't create new water cells here — pressure moves existing water.
        if neighbor.is_liquid() && neighbor.water < WATER_SATURATED {
            let new_self = cell.with_water(cell.water.saturating_sub(1));
            let new_nbr  = neighbor.with_water(neighbor.water.saturating_add(1));
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

/// True if the cell above `(lx, ly)` is applying pressure on this cell.
///
/// A cell is "pressurized from above" when the cell directly above it is
/// a saturated liquid — meaning there is a column of water pushing down.
pub fn has_pressure_from_above(chunk: &Chunk, lx: i32, ly: i32) -> bool {
    let above = chunk.get_with_ghost(lx, ly - 1);
    above.is_liquid() && above.water >= WATER_SATURATED
}
