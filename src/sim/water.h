#pragma once
#include "defs.h"

// CA-only water tick: gravity + equalization within blobs.
// Does NOT handle cross-blob pressure — that is blob_pressure_tick() in blob.c.
//
// Pass 0: Vectored movement — cells with vector != VEC_ZERO fly ballistically.
//   Gravity (+1 dy/tick) and horizontal drag (7/8 per tick) applied each step.
//   Stops on solid wall, world boundary, or entering a full water body.
// Pass 1: Gravity — at-rest water falls into the cell below as much as will fit.
// Pass 2: Equalization — halve diff with each horizontal neighbour (flat surfaces).
//   Only processes cells with vector == VEC_ZERO. In-flight water is skipped.
// bias: 0 or 1, alternates scan direction each frame.
void tick_water(Cell *cells, int bias);

// Clear FLAG_STICKY on the CELL_DIRT at (x,y), if present.
void unstick(Cell *cells, int x, int y);
