#pragma once
#include "defs.h"

// Sand-fall simulation: each CELL_DIRT cell (without FLAG_STICKY) falls
// straight down into air, then diagonally if blocked below.
// bias: alternates scan direction each frame to avoid left/right artifacts.
void tick_dirt(Cell *cells, int bias);
