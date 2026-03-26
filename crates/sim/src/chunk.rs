// chunk.rs — the fundamental unit of the Verdant world
//
// The world is an infinite grid of chunks. Each chunk is 512×512 cells,
// double-buffered, with a 1-cell-wide ghost ring populated from neighbors
// before each physics tick.
//
// Memory per active chunk:
//   front buffer:  512 × 512 × 16 bytes = 4,194,304 bytes (~4 MB)
//   back buffer:   4 MB
//   ghost ring:    4 × 512 × 16 + 4 × 16 ≈ 33 KB
//   Total: ~8 MB per active chunk
//
// With 9 active chunks (3×3 around player) + 50 keep-alive: ~472 MB.
// On a 64 GB machine this is fine.

use crate::cell::Cell;

pub const CHUNK_WIDTH:  usize = 512;
pub const CHUNK_HEIGHT: usize = 512;
pub const CHUNK_AREA:   usize = CHUNK_WIDTH * CHUNK_HEIGHT; // 262,144 cells

// ── Chunk coordinates ─────────────────────────────────────────────────────────

/// Position of a chunk in chunk-space. (0,0) is the origin chunk.
/// World pixel coords: wx = cx * CHUNK_WIDTH + lx, wy = cy * CHUNK_HEIGHT + ly.
///
/// In C: typedef struct { int32_t cx, cy; } ChunkCoord;
/// We derive Hash so it can be used as a HashMap key.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct ChunkCoord {
    pub cx: i32,
    pub cy: i32,
}

impl ChunkCoord {
    #[inline]
    pub fn new(cx: i32, cy: i32) -> Self {
        ChunkCoord { cx, cy }
    }

    /// Return a new coord offset by (dx, dy) in chunk space.
    #[inline]
    pub fn offset(self, dx: i32, dy: i32) -> ChunkCoord {
        ChunkCoord { cx: self.cx + dx, cy: self.cy + dy }
    }

    /// Chebyshev distance in chunk space.
    /// (Chebyshev, not Manhattan, so "within N" describes a square region.)
    /// In C: max(abs(a.cx-b.cx), abs(a.cy-b.cy))
    #[inline]
    pub fn chebyshev(self, other: ChunkCoord) -> i32 {
        (self.cx - other.cx).abs().max((self.cy - other.cy).abs())
    }
}

// ── Chunk state ───────────────────────────────────────────────────────────────

/// Simulation lifecycle state of a chunk.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ChunkState {
    /// Near the player — physics ticked every sim step.
    Active,
    /// Off-screen but has ongoing activity (flowing water, living plants/creatures).
    /// Physics still runs; chunk stays loaded.
    KeepAlive,
    /// No activity. Serialized to disk and frozen until something wakes it.
    Dormant,
}

/// How many consecutive idle daily passes before an off-screen chunk goes Dormant.
pub const IDLE_DAYS_BEFORE_DORMANT: u32 = 3;

// ── Ghost ring ────────────────────────────────────────────────────────────────
//
// Before each tick, boundary.rs copies the outermost column/row of each
// neighboring chunk into this struct. Sim rules then call get_with_ghost()
// instead of get() for neighbor lookups — the ghost ring handles out-of-bounds
// transparently, so rules don't need to special-case chunk edges.
//
// Default when a neighbor is not loaded: rock (solid boundary).

/// 1-cell-wide border from each of the four cardinal neighbors + four corner cells.
pub struct GhostRing {
    /// x = -1, y = 0..CHUNK_HEIGHT  (right edge of left neighbor)
    pub left:         Vec<Cell>,
    /// x = CHUNK_WIDTH, y = 0..CHUNK_HEIGHT  (left edge of right neighbor)
    pub right:        Vec<Cell>,
    /// y = -1, x = 0..CHUNK_WIDTH  (bottom row of top neighbor)
    pub top:          Vec<Cell>,
    /// y = CHUNK_HEIGHT, x = 0..CHUNK_WIDTH  (top row of bottom neighbor)
    pub bottom:       Vec<Cell>,
    pub top_left:     Cell,  // (-1, -1)
    pub top_right:    Cell,  // (CHUNK_WIDTH, -1)
    pub bottom_left:  Cell,  // (-1, CHUNK_HEIGHT)
    pub bottom_right: Cell,  // (CHUNK_WIDTH, CHUNK_HEIGHT)
}

impl GhostRing {
    /// Allocate a ghost ring filled with solid rock — safe default boundary.
    fn new_rock() -> GhostRing {
        let rock = Cell::rock();
        GhostRing {
            left:         vec![rock; CHUNK_HEIGHT],
            right:        vec![rock; CHUNK_HEIGHT],
            top:          vec![rock; CHUNK_WIDTH],
            bottom:       vec![rock; CHUNK_WIDTH],
            top_left:     rock,
            top_right:    rock,
            bottom_left:  rock,
            bottom_right: rock,
        }
    }

    /// Read a ghost cell at out-of-bounds local coordinates.
    /// lx must be -1 or CHUNK_WIDTH; or ly must be -1 or CHUNK_HEIGHT.
    ///
    /// Called only when the coordinate is out of bounds — sim rules should use
    /// Chunk::get_with_ghost(), which dispatches here vs the front buffer.
    pub fn get(&self, lx: i32, ly: i32) -> Cell {
        let at_left   = lx == -1;
        let at_right  = lx == CHUNK_WIDTH as i32;
        let at_top    = ly == -1;
        let at_bottom = ly == CHUNK_HEIGHT as i32;

        match (at_left, at_right, at_top, at_bottom) {
            // Corners
            (true,  false, true,  false) => self.top_left,
            (false, true,  true,  false) => self.top_right,
            (true,  false, false, true)  => self.bottom_left,
            (false, true,  false, true)  => self.bottom_right,
            // Edges
            (true,  false, false, false) => self.left[ly as usize],
            (false, true,  false, false) => self.right[ly as usize],
            (false, false, true,  false) => self.top[lx as usize],
            (false, false, false, true)  => self.bottom[lx as usize],
            _ => Cell::AIR, // shouldn't happen; indicates a logic error in the caller
        }
    }
}

// ── Chunk ─────────────────────────────────────────────────────────────────────

/// A 512×512 tile of the world. The fundamental simulation unit.
pub struct Chunk {
    /// Current readable state. Always in sync with "what the world looks like now."
    front: Vec<Cell>,
    /// Next-frame write target. Filled during tick_physics() / tick_daily().
    back:  Vec<Cell>,
    /// Ghost ring from neighbors. Refreshed by boundary.rs before each tick.
    pub ghost: GhostRing,

    pub coord: ChunkCoord,
    pub state: ChunkState,

    /// Set by tick_physics() if any rule fires this frame. Reset by prepare_tick().
    /// The chunk manager uses this for keep-alive detection.
    pub has_activity: bool,

    /// Consecutive daily passes with no detected activity. When this reaches
    /// IDLE_DAYS_BEFORE_DORMANT, the chunk manager evicts the chunk.
    pub idle_days: u32,
}

impl Chunk {
    /// Allocate a new chunk. All cells start as Cell::AIR (all-zero).
    /// The ghost ring starts as solid rock (safe boundary default).
    pub fn new(coord: ChunkCoord) -> Chunk {
        Chunk {
            front: vec![Cell::AIR; CHUNK_AREA],
            back:  vec![Cell::AIR; CHUNK_AREA],
            ghost: GhostRing::new_rock(),
            coord,
            state:        ChunkState::Active,
            has_activity: false,
            idle_days:    0,
        }
    }

    // ── Index helpers ─────────────────────────────────────────────────────────

    /// Flat index from (x, y). In C: idx = y * CHUNK_WIDTH + x.
    /// debug_assert panics in debug builds; compiles away in release.
    #[inline]
    fn idx(x: usize, y: usize) -> usize {
        debug_assert!(x < CHUNK_WIDTH && y < CHUNK_HEIGHT,
            "chunk cell ({x},{y}) out of bounds");
        y * CHUNK_WIDTH + x
    }

    /// True if (lx, ly) is inside this chunk. Use signed ints to avoid underflow
    /// when computing neighbour offsets like lx - 1.
    #[inline]
    pub fn in_bounds(lx: i32, ly: i32) -> bool {
        lx >= 0 && ly >= 0
            && (lx as usize) < CHUNK_WIDTH
            && (ly as usize) < CHUNK_HEIGHT
    }

    // ── Cell access ───────────────────────────────────────────────────────────

    /// Read a cell from the front (current) buffer.
    #[inline]
    pub fn get(&self, x: usize, y: usize) -> Cell {
        self.front[Self::idx(x, y)]
    }

    /// Read a cell using signed coordinates. If (lx, ly) is outside the chunk,
    /// returns the corresponding ghost cell from the neighboring chunk.
    ///
    /// Sim rules should use this for all neighbor lookups — it handles chunk
    /// edges transparently, the same way a C sim would use pointer arithmetic
    /// into a larger padded buffer.
    #[inline]
    pub fn get_with_ghost(&self, lx: i32, ly: i32) -> Cell {
        if Self::in_bounds(lx, ly) {
            self.front[Self::idx(lx as usize, ly as usize)]
        } else {
            self.ghost.get(lx, ly)
        }
    }

    /// Write a cell into the back (next-frame) buffer.
    /// Only valid to call between prepare_tick() and swap().
    #[inline]
    pub fn set_next(&mut self, x: usize, y: usize, cell: Cell) {
        self.back[Self::idx(x, y)] = cell;
    }

    // ── Tick lifecycle ────────────────────────────────────────────────────────
    //
    // Each high-frequency tick follows this sequence (managed by ChunkManager):
    //   1. boundary::update_ghost_ring() — copy neighbor edges into ghost ring
    //   2. prepare_tick()               — copy front → back; reset has_activity
    //   3. tick_physics()               — rules read front, write back
    //   4. swap()                       — make back the new front

    /// Copy front → back. Must be called at the start of each tick.
    ///
    /// This ensures cells that no rule touches carry their current state forward
    /// unchanged. Without this, untouched cells would become whatever was left
    /// in the back buffer from the previous tick.
    ///
    /// In C: memcpy(back, front, sizeof(Cell) * CHUNK_AREA)
    /// Rust's copy_from_slice compiles to the same memcpy.
    pub fn prepare_tick(&mut self) {
        self.back.copy_from_slice(&self.front);
        self.has_activity = false;
    }

    /// Swap front and back buffers. O(1) — swaps the Vec's internal pointer,
    /// not the data. In C terms: swap the two pointers.
    ///
    /// In Rust, Vec<T> is a (ptr, len, cap) triple on the stack. swap() exchanges
    /// those triples — no data moves.
    #[inline]
    pub fn swap(&mut self) {
        std::mem::swap(&mut self.front, &mut self.back);
    }

    // ── Simulation passes ─────────────────────────────────────────────────────

    /// Run per-frame physics rules: water cycle, particle movement.
    ///
    /// `tick_count` is the global sim step counter passed in by ChunkManager.
    /// It's used by water.rs to alternate spread direction each tick.
    ///
    /// Rules read from front via get()/get_with_ghost(), write to back via set_next().
    /// Sets has_activity = true if any rule fires (used for keep-alive detection).
    pub fn tick_physics(&mut self, tick_count: u64) {
        // Water cycle: gravity, spread, pressure, absorption, state transitions.
        crate::water::tick(self, tick_count);

        // TODO: particle physics (vector-based movement, collision)

        // has_activity is set by water::tick if any rule fired.
        // If nothing fired, do a quick scan as fallback (catches non-water activity).
        if !self.has_activity {
            for i in 0..CHUNK_AREA {
                if self.front[i].is_active() {
                    self.has_activity = true;
                    break;
                }
            }
        }
    }

    /// Run per-day biology rules: plant growth, creature AI, decomposition.
    ///
    /// Called once per in-game day during the sleep intermission. Can be slow —
    /// it runs during a transition screen while the player is "resting at base."
    /// All keep-alive chunks get this pass even if off-screen.
    pub fn tick_daily(&mut self) {
        // TODO: call plants::tick_daily(self)
        // TODO: call creatures::tick_daily(self)
        // TODO: call ecology::decompose(self)
        // TODO: call ecology::enrich_soil(self)
    }

    /// Scan every cell and return true if any is active.
    /// Called during the daily pass by the chunk manager for keep-alive evaluation.
    /// Less frequent than tick_physics, so a full scan is acceptable.
    pub fn scan_for_activity(&self) -> bool {
        self.front.iter().any(|c| c.is_active())
    }

    // ── Bulk helpers ──────────────────────────────────────────────────────────

    /// Fill a rect of the FRONT buffer directly. Used by worldgen and tests.
    /// (Writes to front, not back — intended for setup outside the tick loop.)
    pub fn fill_rect(&mut self, x0: usize, y0: usize, x1: usize, y1: usize, cell: Cell) {
        let x1 = x1.min(CHUNK_WIDTH);
        let y1 = y1.min(CHUNK_HEIGHT);
        for y in y0..y1 {
            for x in x0..x1 {
                self.front[Self::idx(x, y)] = cell;
            }
        }
    }

    /// Write a single cell directly into the FRONT buffer.
    /// Used by worldgen for per-cell generation (more efficient than fill_rect
    /// when setting individual cells in a generation loop).
    #[inline]
    pub fn set_front(&mut self, x: usize, y: usize, cell: Cell) {
        self.front[Self::idx(x, y)] = cell;
    }

    /// Read-only view of the front buffer as a flat byte slice.
    /// Used by the renderer to upload chunk data as a GPU texture.
    /// Each Cell is 16 bytes (#[repr(C)]), so this is safe to reinterpret.
    #[inline]
    pub fn front_slice(&self) -> &[Cell] {
        &self.front
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_chunk_all_air() {
        let c = Chunk::new(ChunkCoord::new(0, 0));
        assert!(c.get(0, 0).is_air());
        assert!(c.get(511, 511).is_air());
    }

    #[test]
    fn double_buffer_mechanics() {
        let mut c = Chunk::new(ChunkCoord::new(0, 0));
        c.prepare_tick();
        c.set_next(10, 20, Cell::new_water());
        // Before swap: front still shows old value (air)
        assert!(c.get(10, 20).is_air());
        c.swap();
        // After swap: written value is now live
        assert!(c.get(10, 20).is_liquid());
    }

    #[test]
    fn prepare_tick_carries_state_forward() {
        let mut c = Chunk::new(ChunkCoord::new(0, 0));
        c.fill_rect(0, 0, 10, 10, Cell::rock());
        c.prepare_tick();
        c.swap();
        // Rock should survive unchanged through a tick with no rules applied
        assert!(c.get(5, 5).is_solid());
    }

    #[test]
    fn ghost_ring_defaults_to_rock() {
        let c = Chunk::new(ChunkCoord::new(0, 0));
        // Unloaded neighbor → ghost defaults to rock (solid boundary)
        let cell = c.get_with_ghost(-1, 0);
        assert!(cell.is_solid(), "ghost cell should default to solid rock");
        let cell = c.get_with_ghost(0, -1);
        assert!(cell.is_solid());
    }

    #[test]
    fn in_bounds() {
        assert!( Chunk::in_bounds(0,   0));
        assert!( Chunk::in_bounds(511, 511));
        assert!(!Chunk::in_bounds(-1,  0));
        assert!(!Chunk::in_bounds(512, 0));
        assert!(!Chunk::in_bounds(0,  -1));
    }

    #[test]
    fn chebyshev_distance() {
        let a = ChunkCoord::new(0, 0);
        let b = ChunkCoord::new(2, 3);
        assert_eq!(a.chebyshev(b), 3); // max(2,3)
        assert_eq!(a.chebyshev(a), 0);
    }

    #[test]
    fn scan_activity_detects_water() {
        let mut c = Chunk::new(ChunkCoord::new(0, 0));
        assert!(!c.scan_for_activity()); // empty → no activity
        c.fill_rect(100, 100, 110, 110, Cell::new_water());
        assert!(c.scan_for_activity()); // water is liquid → active
    }
}
