#pragma once
#include "defs.h"

// Full flood fill of all CELL_AIR regions. Call once after terrain_generate.
// Populates blob_id[] and blobs[]; sets *blob_count.
// Sets bounding box, water_sum, volume, sealed, gas_pressure on each blob.
void blob_init(const Cell *cells, Blob *blobs, uint16_t *blob_id, int *blob_count);

// Mark the blob owning (x,y) and its 4 neighbours dirty.
// Call whenever any cell changes type (dig, place, explosion, erosion).
void blob_mark_dirty(Blob *blobs, const uint16_t *blob_id, int x, int y);

// If any blob is dirty, re-runs blob_init. Call once per frame before blob_pressure_tick.
void blob_update(const Cell *cells, Blob *blobs, uint16_t *blob_id, int *blob_count);

// Column-scan pressure tick: for each active blob, compute column profiles,
// find interfaces with adjacent blobs, transfer water from high to low pressure.
// Water is removed from the top of the high-pressure blob (pop-from-top) and
// placed at the interface cell on the receiving side for CA settling.
void blob_pressure_tick(Cell *cells, Blob *blobs, const uint16_t *blob_id, int blob_count);
