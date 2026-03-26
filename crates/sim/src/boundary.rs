// boundary.rs — ghost ring population for cross-chunk simulation
//
// Before each physics tick, every active/keep-alive chunk needs its ghost ring
// filled with the outermost row/column of each cardinal neighbor. This lets
// sim rules call get_with_ghost() at chunk edges without special-casing
// boundary conditions — transparent to the rule author.
//
// The two-phase approach (collect then apply) exists to satisfy Rust's borrow
// checker. We can't borrow `chunks` mutably (to write ghost rings) and
// immutably (to read neighbor cells) at the same time. So:
//   Phase 1 — collect: read neighbor edges into a temporary GhostData struct
//   Phase 2 — apply:   write GhostData into the target chunk's ghost ring
//
// In C you'd just read and write freely since there's no borrow checker.
// The two-phase split has the same semantics and essentially zero overhead.

use std::collections::HashMap;
use crate::cell::Cell;
use crate::chunk::{Chunk, ChunkCoord, CHUNK_WIDTH, CHUNK_HEIGHT};

/// The four edge strips + four corners needed to fill one chunk's ghost ring.
/// Collected in Phase 1 and applied in Phase 2.
pub struct GhostData {
    /// CHUNK_HEIGHT cells: right edge of the left neighbor (x = CHUNK_WIDTH-1).
    pub left:         Vec<Cell>,
    /// CHUNK_HEIGHT cells: left edge of the right neighbor (x = 0).
    pub right:        Vec<Cell>,
    /// CHUNK_WIDTH cells: bottom row of the top neighbor (y = CHUNK_HEIGHT-1).
    pub top:          Vec<Cell>,
    /// CHUNK_WIDTH cells: top row of the bottom neighbor (y = 0).
    pub bottom:       Vec<Cell>,
    pub top_left:     Cell,
    pub top_right:    Cell,
    pub bottom_left:  Cell,
    pub bottom_right: Cell,
}

/// Default cell used when a neighboring chunk is not loaded (undiscovered territory).
/// Rock is geologically safe — undiscovered regions are assumed to be solid ground.
fn unloaded_boundary() -> Cell {
    Cell::rock()
}

/// Phase 1 — Collect ghost data for the chunk at `coord` by reading its neighbors.
///
/// `chunks` must be borrowed immutably. Any neighbor not present in the map
/// (undiscovered or dormant) contributes `unloaded_boundary()` cells.
pub fn collect_ghost_data(coord: ChunkCoord, chunks: &HashMap<ChunkCoord, Chunk>) -> GhostData {
    let left_coord        = coord.offset(-1,  0);
    let right_coord       = coord.offset( 1,  0);
    let top_coord         = coord.offset( 0, -1);
    let bottom_coord      = coord.offset( 0,  1);
    let top_left_coord    = coord.offset(-1, -1);
    let top_right_coord   = coord.offset( 1, -1);
    let bottom_left_coord = coord.offset(-1,  1);
    let bottom_right_coord= coord.offset( 1,  1);

    // Left edge: rightmost column of left neighbor.
    let left = match chunks.get(&left_coord) {
        Some(n) => (0..CHUNK_HEIGHT).map(|y| n.get(CHUNK_WIDTH - 1, y)).collect(),
        None    => vec![unloaded_boundary(); CHUNK_HEIGHT],
    };

    // Right edge: leftmost column of right neighbor.
    let right = match chunks.get(&right_coord) {
        Some(n) => (0..CHUNK_HEIGHT).map(|y| n.get(0, y)).collect(),
        None    => vec![unloaded_boundary(); CHUNK_HEIGHT],
    };

    // Top edge: bottom row of top neighbor.
    let top = match chunks.get(&top_coord) {
        Some(n) => (0..CHUNK_WIDTH).map(|x| n.get(x, CHUNK_HEIGHT - 1)).collect(),
        None    => vec![unloaded_boundary(); CHUNK_WIDTH],
    };

    // Bottom edge: top row of bottom neighbor.
    let bottom = match chunks.get(&bottom_coord) {
        Some(n) => (0..CHUNK_WIDTH).map(|x| n.get(x, 0)).collect(),
        None    => vec![unloaded_boundary(); CHUNK_WIDTH],
    };

    // Corners — from diagonal neighbors.
    // Each corner needs only 1 cell: the opposite corner of the diagonal neighbor.
    let top_left = chunks.get(&top_left_coord)
        .map(|n| n.get(CHUNK_WIDTH - 1, CHUNK_HEIGHT - 1))
        .unwrap_or_else(unloaded_boundary);

    let top_right = chunks.get(&top_right_coord)
        .map(|n| n.get(0, CHUNK_HEIGHT - 1))
        .unwrap_or_else(unloaded_boundary);

    let bottom_left = chunks.get(&bottom_left_coord)
        .map(|n| n.get(CHUNK_WIDTH - 1, 0))
        .unwrap_or_else(unloaded_boundary);

    let bottom_right = chunks.get(&bottom_right_coord)
        .map(|n| n.get(0, 0))
        .unwrap_or_else(unloaded_boundary);

    GhostData { left, right, top, bottom, top_left, top_right, bottom_left, bottom_right }
}

/// Phase 2 — Apply collected ghost data to a chunk's ghost ring.
/// `chunk` must be borrowed mutably (separate from the immutable pass in Phase 1).
pub fn apply_ghost_data(chunk: &mut Chunk, data: GhostData) {
    chunk.ghost.left         = data.left;
    chunk.ghost.right        = data.right;
    chunk.ghost.top          = data.top;
    chunk.ghost.bottom       = data.bottom;
    chunk.ghost.top_left     = data.top_left;
    chunk.ghost.top_right    = data.top_right;
    chunk.ghost.bottom_left  = data.bottom_left;
    chunk.ghost.bottom_right = data.bottom_right;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;


    fn make_chunk(coord: ChunkCoord, fill: Cell) -> Chunk {
        let mut c = Chunk::new(coord);
        c.fill_rect(0, 0, CHUNK_WIDTH, CHUNK_HEIGHT, fill);
        c
    }

    #[test]
    fn unloaded_neighbor_gives_rock() {
        let chunks: HashMap<ChunkCoord, Chunk> = HashMap::new();
        let data = collect_ghost_data(ChunkCoord::new(0, 0), &chunks);
        // All neighbors absent → all ghost cells should be rock
        assert!(data.left[0].is_solid());
        assert!(data.top[0].is_solid());
        assert!(data.top_left.is_solid());
    }

    #[test]
    fn loaded_neighbor_edge_is_copied() {
        let mut chunks: HashMap<ChunkCoord, Chunk> = HashMap::new();
        // Left neighbor filled with water
        chunks.insert(ChunkCoord::new(-1, 0), make_chunk(ChunkCoord::new(-1, 0), Cell::new_water()));
        chunks.insert(ChunkCoord::new(0,  0), make_chunk(ChunkCoord::new( 0, 0), Cell::AIR));

        let data = collect_ghost_data(ChunkCoord::new(0, 0), &chunks);

        // Left ghost should reflect the water in the neighbor's rightmost column
        assert!(data.left[0].is_liquid(), "left ghost should be water");
        // Right, top, bottom still unloaded → rock
        assert!(data.right[0].is_solid());
        assert!(data.top[0].is_solid());
    }

    #[test]
    fn apply_round_trip() {
        let mut chunks: HashMap<ChunkCoord, Chunk> = HashMap::new();
        chunks.insert(ChunkCoord::new(-1, 0), make_chunk(ChunkCoord::new(-1, 0), Cell::new_water()));
        let coord = ChunkCoord::new(0, 0);
        chunks.insert(coord, Chunk::new(coord));

        let data = collect_ghost_data(coord, &chunks);

        // Remove target, apply to it mutably
        let mut target = chunks.remove(&coord).unwrap();
        apply_ghost_data(&mut target, data);

        // Ghost ring should now reflect the left neighbor's water edge
        let ghost_cell = target.get_with_ghost(-1, 0);
        assert!(ghost_cell.is_liquid(), "ghost cell after apply should be water");
    }
}
