#pragma once
#include "defs.h"

// Liquid simulation: CELL_WATER falls straight down, then spreads sideways
// preferring the shallower column (communicating vessels).
// Run multiple passes per frame for fast equalization across basins.
// bias: alternates scan direction each frame to avoid directional artifacts.
void tick_water(uint8_t *world, int bias);

// Clear FLAG_STICKY on the dirt cell at (x, y) if one exists.
// Called when a neighbouring cell is dug or destroyed.
void unstick(uint8_t *world, int x, int y);
