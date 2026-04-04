#pragma once
#include "defs.h"

// Continuous water simulation using a parallel water[WORLD_W*WORLD_H] amount array.
// world[] stores cell material (AIR/STONE/DIRT/PLATFORM). water[] stores 0..255 per cell.
// Only CELL_AIR cells participate in flow.
//
// Three rules per tick, applied bottom-to-top:
//   1. Gravity      — water falls into the cell below as much as will fit
//   2. Equalization — halve the difference with each horizontal neighbour (flat surfaces)
//   3. Pressure     — fully-saturated cell under another saturated cell pushes sideways
//
// bias: 0 or 1, alternates scan direction each frame to avoid directional drift.
// Run 3 passes per frame (see main.c) for fast equalization across basins.
void tick_water(uint8_t *world, uint8_t *water, int bias);

// Clear FLAG_STICKY on the dirt cell at (x, y) if one exists.
// Called when a neighbouring cell is dug or destroyed.
void unstick(uint8_t *world, int x, int y);
