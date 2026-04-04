#pragma once
#include "defs.h"

// Returns the y of the first solid cell at or below (wx, start_y).
// Returns start_y if wx is out of bounds, WORLD_H if no solid found.
int ground_y_at(const uint8_t *w, int wx, int start_y);

// AABB collision test against the world grid.
// CELL_STONE and CELL_DIRT are always solid.
// CELL_PLATFORM is solid only when include_platform=1 (downward player collision).
// Out-of-bounds cells are always solid.
// Returns 1 if the box overlaps any solid cell, 0 otherwise.
int box_solid_ex(const uint8_t *w, float bx, float by,
                 int bw, int bh, int include_platform);
