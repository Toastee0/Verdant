// chunk_manager.rs — loads, schedules, and evicts chunks
//
// The ChunkManager owns all loaded chunks and drives the two simulation
// frequencies defined in the GDD:
//
//   tick_high_frequency()  — called every frame
//     Runs water cycle + particle physics on Active and KeepAlive chunks.
//     Active   = within active_radius of the player (Chebyshev distance).
//     KeepAlive= outside player range but has ongoing biological/water activity.
//
//   tick_daily_pass()  — called once per in-game day (every ~30 real minutes)
//     Runs plant growth, creature AI, decomposition, biomass on ALL loaded chunks.
//     Also re-evaluates keep-alive status and evicts idle chunks.
//
// Discovery rule:
//   Chunks do NOT pre-generate. A chunk enters existence only when the player
//   enters its Chebyshev range. Undiscovered chunks are not even in the map —
//   they contribute rock-default ghost cells to their loaded neighbors.
//   "The world is frozen in geological time until you arrive."

use std::collections::HashMap;
use crate::boundary;
use crate::chunk::{Chunk, ChunkCoord, ChunkState, IDLE_DAYS_BEFORE_DORMANT};

pub struct ChunkManager {
    /// All currently loaded chunks. Key = chunk coordinate.
    ///
    /// In C this would be a hash table: HashMap<ChunkCoord, Chunk>.
    /// Rust's std HashMap uses the same concept. ChunkCoord must implement
    /// Hash + Eq, which we derived on the struct.
    chunks: HashMap<ChunkCoord, Chunk>,

    /// The chunk the player is currently standing in.
    player_chunk: ChunkCoord,

    /// Chebyshev radius around the player that stays Active.
    /// active_radius=1 → 3×3 = 9 chunks.
    /// active_radius=2 → 5×5 = 25 chunks.
    active_radius: i32,

    /// Monotonically increasing sim step counter.
    /// Passed to chunk.tick_physics() so rules can alternate behavior per tick
    /// (e.g., spread direction alternation to eliminate directional bias).
    tick_count: u64,
}

impl ChunkManager {
    pub fn new(active_radius: i32) -> ChunkManager {
        ChunkManager {
            chunks: HashMap::new(),
            player_chunk: ChunkCoord::new(0, 0),
            active_radius,
            tick_count: 0,
        }
    }

    // ── Player tracking ───────────────────────────────────────────────────────

    /// Call this each frame with the chunk the player is standing in.
    /// Generates any newly in-range chunks, then re-evaluates states.
    pub fn set_player_chunk(&mut self, coord: ChunkCoord) {
        self.player_chunk = coord;
        self.discover_active_zone();
        self.refresh_chunk_states();
    }

    /// Generate and load any chunks within active_radius that haven't been
    /// discovered yet. This is the only place new Chunk values are created.
    fn discover_active_zone(&mut self) {
        let r  = self.active_radius;
        let pc = self.player_chunk;

        for dy in -r..=r {
            for dx in -r..=r {
                let coord = pc.offset(dx, dy);
                // HashMap::entry() is the idiomatic insert-if-absent pattern.
                // In C: if (!map_contains(coord)) map_insert(coord, generate(coord));
                self.chunks.entry(coord).or_insert_with(|| generate_chunk(coord));
            }
        }
    }

    /// Recalculate Active / KeepAlive / Dormant for every loaded chunk.
    fn refresh_chunk_states(&mut self) {
        let player = self.player_chunk;
        let radius = self.active_radius;

        for (coord, chunk) in self.chunks.iter_mut() {
            chunk.state = if coord.chebyshev(player) <= radius {
                ChunkState::Active
            } else if chunk.has_activity {
                ChunkState::KeepAlive
            } else {
                ChunkState::Dormant
            };
        }
    }

    // ── High-frequency tick (every frame) ────────────────────────────────────

    /// Tick all Active and KeepAlive chunks: water cycle, particle physics.
    ///
    /// Sequence:
    ///   1. Collect ghost data for each chunk (immutable pass — reads neighbors)
    ///   2. Apply ghost data (mutates each chunk's ghost ring)
    ///   3. prepare_tick + tick_physics + swap for each chunk
    ///   4. Refresh states based on updated has_activity flags
    ///
    /// The two-phase ghost update is required by Rust's borrow rules: we can't
    /// hold a &Chunk (for reading neighbors) and a &mut Chunk (for writing ghost
    /// rings) at the same time. Collecting into a Vec first resolves this.
    /// In C you'd just do it in one pass — same semantics, no ownership issue.
    pub fn tick_high_frequency(&mut self) {
        // Step 1: which chunks need a tick?
        // Collect into a Vec so we can loop independently of the HashMap.
        let to_tick: Vec<ChunkCoord> = self.chunks
            .iter()
            .filter(|(_, c)| matches!(c.state, ChunkState::Active | ChunkState::KeepAlive))
            .map(|(k, _)| *k)
            .collect();

        // Step 2: collect all ghost data (immutable borrow of self.chunks)
        // We build a Vec of (coord, GhostData) before touching any chunk mutably.
        let ghost_updates: Vec<(ChunkCoord, boundary::GhostData)> = to_tick
            .iter()
            .map(|&coord| (coord, boundary::collect_ghost_data(coord, &self.chunks)))
            .collect();

        // Step 3: apply ghost data (now we can borrow mutably)
        for (coord, data) in ghost_updates {
            if let Some(chunk) = self.chunks.get_mut(&coord) {
                boundary::apply_ghost_data(chunk, data);
            }
        }

        // Step 4: prepare → tick → swap for each chunk.
        //
        // TODO: checkerboard scheduling for parallel execution.
        // Even-parity chunks (cx+cy) % 2 == 0 first, then odd-parity.
        // Chunks in the same parity class share no edges, so they can be
        // ticked in parallel with rayon::par_iter().
        let tick = self.tick_count;
        for &coord in &to_tick {
            if let Some(chunk) = self.chunks.get_mut(&coord) {
                chunk.prepare_tick();
                chunk.tick_physics(tick);
                chunk.swap();
            }
        }
        self.tick_count += 1;

        // Step 5: refresh states so has_activity changes take effect
        self.refresh_chunk_states();
    }

    // ── Daily pass (once per in-game day) ────────────────────────────────────

    /// Tick ALL loaded chunks for biology, ecology, and dormancy evaluation.
    ///
    /// Called once per in-game day (~30 real minutes) during the sleep
    /// intermission while the player rests at base. Can afford to be slow.
    ///
    /// This is also when distant ecosystems "catch up" — all KeepAlive chunks
    /// run their full biology pass even if they're off-screen.
    pub fn tick_daily_pass(&mut self) {
        for chunk in self.chunks.values_mut() {
            // Run per-day biology (plants, creatures, decomposition)
            chunk.tick_daily();

            // Rescan for activity to update keep-alive status.
            // Plants that finished growing or died may no longer be active.
            let active = chunk.scan_for_activity();
            chunk.has_activity = active;

            if active {
                chunk.idle_days = 0;
            } else {
                chunk.idle_days += 1;
            }
        }

        // Evict chunks that have been idle long enough and aren't near the player.
        // HashMap::retain() removes entries where the closure returns false.
        // In C: iterate the table, mark entries for deletion, then delete.
        let player = self.player_chunk;
        let radius = self.active_radius;
        self.chunks.retain(|coord, chunk| {
            let near_player = coord.chebyshev(player) <= radius;
            let should_keep = near_player || chunk.idle_days < IDLE_DAYS_BEFORE_DORMANT;
            if !should_keep {
                // TODO: serialize chunk to disk before evicting
                // worldgen::serialize(coord, &chunk);
            }
            should_keep
        });

        self.refresh_chunk_states();
    }

    // ── Chunk access ──────────────────────────────────────────────────────────

    pub fn get(&self, coord: ChunkCoord) -> Option<&Chunk> {
        self.chunks.get(&coord)
    }

    pub fn get_mut(&mut self, coord: ChunkCoord) -> Option<&mut Chunk> {
        self.chunks.get_mut(&coord)
    }

    pub fn loaded_count(&self) -> usize {
        self.chunks.len()
    }

    pub fn player_chunk(&self) -> ChunkCoord {
        self.player_chunk
    }

    /// Iterate over all loaded chunks. Used by the renderer to find visible chunks.
    pub fn iter_chunks(&self) -> impl Iterator<Item = (&ChunkCoord, &Chunk)> {
        self.chunks.iter()
    }

    /// Look up a single cell by world-cell coordinates.
    ///
    /// Returns None if the chunk containing (wx, wy) isn't currently loaded.
    /// Callers that need a safe fallback for unloaded chunks should treat None
    /// as solid (see walker::is_solid).
    ///
    /// div_euclid / rem_euclid handle negative coordinates correctly — e.g.,
    /// wx=-1 lands in chunk cx=-1, local lx=511 (not cx=0, lx=-1 which would panic).
    /// In C you'd write a manual floor-divide: cx = (wx < 0) ? (wx - W + 1) / W : wx / W
    pub fn get_cell_world(&self, wx: i32, wy: i32) -> Option<crate::cell::Cell> {
        use crate::chunk::{CHUNK_WIDTH, CHUNK_HEIGHT};
        let cx = wx.div_euclid(CHUNK_WIDTH as i32);
        let cy = wy.div_euclid(CHUNK_HEIGHT as i32);
        let lx = wx.rem_euclid(CHUNK_WIDTH as i32) as usize;
        let ly = wy.rem_euclid(CHUNK_HEIGHT as i32) as usize;
        self.chunks.get(&ChunkCoord::new(cx, cy)).map(|c| c.get(lx, ly))
    }
}

// ── Procedural chunk generation ───────────────────────────────────────────────
//
// Called the first time a chunk enters active range ("discovery").
// Delegates to the worldgen module for full geological generation:
//   - Layered noise for rock/soil/ore placement
//   - Cave system carving (Perlin threshold)
//   - Ore deposit seeding (depth-gated)
//   - Sector biome variation (Volcanic, MineralRich, DeepWater, Debris)
//   - Machine pocket (elliptical cavity at cx=0, cy=1)
//   - Lava core sentinel (cy >= 16)
//
// The seed for deterministic generation is derived from the coord so the
// same chunk always generates identically regardless of visit order.
fn generate_chunk(coord: ChunkCoord) -> Chunk {
    crate::worldgen::generate(coord)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_chunks_loaded_on_creation() {
        let mgr = ChunkManager::new(1);
        assert_eq!(mgr.loaded_count(), 0);
    }

    #[test]
    fn player_position_loads_3x3_zone() {
        let mut mgr = ChunkManager::new(1); // radius 1 → 3×3
        mgr.set_player_chunk(ChunkCoord::new(0, 0));
        assert_eq!(mgr.loaded_count(), 9);
    }

    #[test]
    fn chunks_in_active_radius_are_active() {
        let mut mgr = ChunkManager::new(1);
        mgr.set_player_chunk(ChunkCoord::new(0, 0));
        let chunk = mgr.get(ChunkCoord::new(0, 0)).unwrap();
        assert_eq!(chunk.state, ChunkState::Active);
    }

    #[test]
    fn chunks_outside_radius_start_dormant() {
        let mut mgr = ChunkManager::new(1);
        mgr.set_player_chunk(ChunkCoord::new(0, 0));
        // Move player far away — old chunks should transition to dormant
        // (they have no activity since they're empty air)
        mgr.set_player_chunk(ChunkCoord::new(100, 0));
        // Original chunk at (0,0) should now be Dormant
        if let Some(chunk) = mgr.get(ChunkCoord::new(0, 0)) {
            assert_ne!(chunk.state, ChunkState::Active);
        }
        // Chunk near new player position should be Active
        let new_chunk = mgr.get(ChunkCoord::new(100, 0)).unwrap();
        assert_eq!(new_chunk.state, ChunkState::Active);
    }

    #[test]
    fn high_freq_tick_runs_without_panic() {
        let mut mgr = ChunkManager::new(1);
        mgr.set_player_chunk(ChunkCoord::new(0, 0));
        // Should complete without panicking
        mgr.tick_high_frequency();
        mgr.tick_high_frequency();
    }

    #[test]
    fn daily_pass_evicts_idle_chunks() {
        let mut mgr = ChunkManager::new(1);
        mgr.set_player_chunk(ChunkCoord::new(0, 0));
        // Move player away so chunks at (0,0) are outside active radius
        mgr.set_player_chunk(ChunkCoord::new(100, 0));
        let initial_count = mgr.loaded_count();
        // Run enough daily passes to exceed IDLE_DAYS_BEFORE_DORMANT
        for _ in 0..=IDLE_DAYS_BEFORE_DORMANT {
            mgr.tick_daily_pass();
        }
        // Idle chunks far from player should have been evicted
        assert!(mgr.loaded_count() < initial_count,
            "idle chunks should be evicted after {} days", IDLE_DAYS_BEFORE_DORMANT);
    }
}
