// water/diffusion.rs — lateral moisture diffusion through air
//
// Humid air cells slowly share moisture with drier air neighbors.
// This drives atmospheric humidity gradients — the slow half of the water cycle.
//
// Rate: 1/8 of the gradient per tick, minimum 1 unit, only when difference >= 4.
// In practice this creates a slow "fog" that spreads from wet surfaces into dry air.
// It is NOT the fast spreading of liquid water — that's in gravity.rs / mod.rs.

use crate::cell::Cell;
use crate::chunk::Chunk;

/// Moisture diffusion: share humidity with the first drier air neighbor found.
///
/// Checks all four cardinal directions. On the first drier neighbor found,
/// transfers a small fraction (1/8 of difference, min 1). Returns after one
/// transfer per cell per tick — slow and gradual by design.
pub fn moisture_diffuse(chunk: &mut Chunk, x: usize, y: usize, cell: Cell,
                        lx: i32, ly: i32)
{
    for (dx, dy) in &[(1i32, 0i32), (-1, 0), (0, 1), (0, -1)] {
        let nx = lx + dx;
        let ny = ly + dy;
        let neighbor = chunk.get_with_ghost(nx, ny);

        // Only diffuse into drier air — not into liquids or solids.
        if !neighbor.is_air() { continue; }
        let diff = cell.water.saturating_sub(neighbor.water);
        if diff < 4 { continue; } // gradient too small to matter

        let transfer  = (diff / 8).max(1);
        let new_self  = cell.with_water(cell.water.saturating_sub(transfer));
        let new_nbr   = neighbor.with_water(neighbor.water.saturating_add(transfer));

        chunk.set_next(x, y, new_self);
        if Chunk::in_bounds(nx, ny) {
            chunk.set_next(nx as usize, ny as usize, new_nbr);
        }
        chunk.has_activity = true;
        return; // one transfer per cell per tick
    }
}
