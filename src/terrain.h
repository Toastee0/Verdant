#pragma once
#include "defs.h"

// Fill world[] with the test-scene terrain:
//   - Stone floor (row 180+), 10px sticky-dirt layer, three one-way platforms
//   - Raised stone ramp on the right half
//   - Procedural ceiling: rock base + sticky-dirt stalactites (fbm + spike waves)
//   - Communicating basins water demo (left basin starts full)
//
// This function is the sole owner of the starting world state.
// It will be replaced / upgraded when proper worldgen is implemented
// (see Archive/HANDOFF_SIM_WORLDGEN.md for the full spec).
void terrain_generate(uint8_t *world, uint8_t *water);
