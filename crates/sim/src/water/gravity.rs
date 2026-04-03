// water/gravity.rs — gravity-driven movement: fall, rise, and diagonal slide
//
// Three rules live here:
//   rise()         — vapors float upward through denser material (buoyancy)
//   liquid_fall()  — liquids sink through less-dense material
//   powder_fall()  — granular material piles up (straight down, then diagonal)
//
// All three are displacement rules: they swap the moving cell with what's in the
// target position. The actual swap is written to the BACK buffer via write_swap().
//
// Density comparison: cell.density() = mineral*3 + water. Rock >> water >> air.
// Heavier cells displace lighter cells downward; lighter cells rise through heavier.

use crate::cell::Cell;
use crate::chunk::Chunk;
use super::transfer::write_swap;

/// Vapor rises: if the cell above is denser, swap (buoyancy).
///
/// Steam and hot moist air bubble upward through denser material below them.
/// The rule fires when `above.density() > cell.density()` — strict greater-than
/// so equal-density cells do not swap (no perpetual oscillation).
pub fn rise(chunk: &mut Chunk, x: usize, y: usize, cell: Cell, lx: i32, ly: i32) {
    let above = chunk.get_with_ghost(lx, ly - 1);
    if above.density() > cell.density() {
        write_swap(chunk, x, y, cell, lx, ly - 1, above);
    }
}

/// Liquid falls: if the cell below is less dense, swap.
///
/// Returns true if the cell moved — the caller uses this to skip the
/// horizontal spread step (no point spreading if you just fell).
pub fn liquid_fall(chunk: &mut Chunk, x: usize, y: usize, cell: Cell,
                   lx: i32, ly: i32) -> bool
{
    let below = chunk.get_with_ghost(lx, ly + 1);
    if below.density() < cell.density() {
        write_swap(chunk, x, y, cell, lx, ly + 1, below);
        return true;
    }
    false
}

/// Powder falls straight down, then slides diagonally if blocked.
///
/// Diagonal slide (pile behavior): the side cell AND the diagonal-below cell
/// must both be passable — without this check, powder can "teleport" through
/// a diagonal wall corner.
///
/// Returns true if the cell moved. Used by the caller to decide whether to
/// attempt soil absorption (settled powder can absorb, falling powder skips it).
pub fn powder_fall(chunk: &mut Chunk, x: usize, y: usize, cell: Cell,
                   lx: i32, ly: i32, slide_right_first: bool) -> bool
{
    // Try straight down.
    // Powder displaces air and less-dense non-liquid material, but NOT liquid —
    // dry soil is less dense than water (mineral*3+water: 275 vs 255 raw, but
    // real soil doesn't dissolve into water on contact).  Blocking liquid
    // displacement prevents the "standing waves" artifact where cascading soil
    // churns a pool indefinitely.  Soil rests on water's surface instead.
    let below = chunk.get_with_ghost(lx, ly + 1);
    if below.density() < cell.density() && !below.is_liquid() {
        write_swap(chunk, x, y, cell, lx, ly + 1, below);
        return true;
    }

    // Try diagonal slide — alternates direction each tick (bias elimination)
    let sides: [i32; 2] = if slide_right_first { [1, -1] } else { [-1, 1] };
    for dx in &sides {
        let side = chunk.get_with_ghost(lx + dx, ly);
        let diag = chunk.get_with_ghost(lx + dx, ly + 1);
        if side.density() < cell.density() && !side.is_liquid()
            && diag.density() < cell.density() && !diag.is_liquid()
        {
            write_swap(chunk, x, y, cell, lx + dx, ly + 1, diag);
            return true;
        }
    }

    false // settled — did not move
}
