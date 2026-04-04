#pragma once
#include "defs.h"

// Fill world[] with the test-scene terrain:
//   - Stone floor (row 180+), 10px sticky-dirt layer, three one-way platforms
//   - Raised stone ramp on the right half
//   - Procedural ceiling: rock base + sticky-dirt stalactites (fbm + spike waves)
//   - Communicating basins U-tube demo (left basin starts full, equalizes via bottom channel)
//
// This function is the sole owner of the starting world state.
// It will be replaced / upgraded when proper worldgen is implemented
// (see Archive/HANDOFF_SIM_WORLDGEN.md for the full spec).
void terrain_generate(Cell *cells);
