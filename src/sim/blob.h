#pragma once
#include "defs.h"

// Full flood fill of all CELL_AIR regions. Call once after terrain_generate.
// Populates blob_id[WORLD_W*WORLD_H] and blobs[MAX_BLOBS]; sets *blob_count.
void blob_init(const Cell *cells, Blob *blobs, uint16_t *blob_id, int *blob_count);

// Mark the blob owning cell (x,y) — and its 4 neighbours — dirty.
// Call whenever any cell changes type (dig, place, explosion, erosion).
void blob_mark_dirty(Blob *blobs, const uint16_t *blob_id, int x, int y);

// If any blob is dirty, re-flood-fills the entire world.
// Call once per frame before tick_water.
void blob_update(const Cell *cells, Blob *blobs, uint16_t *blob_id, int *blob_count);
