// water/transfer.rs — universal cell-to-cell fluid transfer primitives
//
// These are the lowest-level operations used by all water movement rules.
// Every rule that moves water uses write_swap() or equalize_water().
//
// In C you'd typically inline these or put them in a water_util.c — here
// they live in their own file to keep each physics concern isolated.

use crate::cell::Cell;
use crate::chunk::Chunk;

/// True if `neighbor` can receive water from a cell with `source_water` amount.
///
/// - Air always accepts water (becomes a new water cell).
/// - A liquid neighbor only accepts if it has meaningfully less water than the
///   source — otherwise equalization would oscillate back and forth.
pub fn can_receive_water(neighbor: Cell, source_water: u8) -> bool {
    if neighbor.is_air() {
        return true;
    }
    if neighbor.is_liquid() {
        // Use saturating_add to avoid overflow when neighbor.water is near 255.
        return neighbor.water.saturating_add(2) < source_water;
    }
    false
}

/// Transfer water from `source` cell toward `dest` cell using ONI-style
/// mass equalization: move half the difference per tick.
///
/// If `dest` is air it becomes a new water cell carrying the transferred amount.
/// The source loses the same amount.
///
/// Both writes go to the BACK buffer. The front buffer is unchanged this tick.
pub fn equalize_water(chunk: &mut Chunk,
                      sx: usize, sy: usize, source: Cell,
                      nx: i32,   ny: i32,   dest: Cell)
{
    let src_w = source.water;
    let dst_w = if dest.is_liquid() { dest.water } else { 0 };
    let diff   = src_w.saturating_sub(dst_w);
    let amount = (diff / 2).max(1);

    let new_source = source.with_water(src_w.saturating_sub(amount));

    let new_dest = if dest.is_liquid() {
        dest.with_water(dst_w.saturating_add(amount))
    } else {
        // Air → becomes a water cell. Carry temperature from the source so
        // heat spreads with the water (warm rain stays warm).
        Cell::new(amount, 0, source.temperature, 0)
    };

    chunk.set_next(sx, sy, new_source);
    if Chunk::in_bounds(nx, ny) {
        chunk.set_next(nx as usize, ny as usize, new_dest);
    }
    chunk.has_activity = true;
}

/// Logical cell swap between (sx,sy) and (nx,ny), written to the BACK buffer.
///
/// "Swap" here means: source cell moves to the neighbor position, neighbor
/// cell moves to the source position. Neither write touches the front buffer —
/// both positions are read this tick, written for next tick.
///
/// In C: temp=a; a=b; b=temp; — but both reads are from front, both writes
/// go to back. The logical swap is only visible after swap().
#[inline]
pub fn write_swap(chunk: &mut Chunk,
                  sx: usize, sy: usize, source: Cell,
                  nx: i32,   ny: i32,   dest: Cell)
{
    chunk.set_next(sx, sy, dest);
    if Chunk::in_bounds(nx, ny) {
        chunk.set_next(nx as usize, ny as usize, source);
    }
    chunk.has_activity = true;
}
