#pragma once
#include "defs.h"

// Carve a circular crater of given radius centred at (cx, cy).
// Removes CELL_DIRT within the radius; unsticks neighbours so overhanging
// dirt collapses into the crater.
void explode(Cell *cells, int cx, int cy, int radius);

// Deposit loose CELL_DIRT in a filled circle of given radius.
// Only fills CELL_AIR — won't overwrite existing terrain.
// Dirt placed here has no FLAG_STICKY so it falls immediately.
void impact_soil_ball(Cell *cells, int cx, int cy, int radius);

// Same as impact_soil_ball but sets FLAG_STICKY — adheres to ceilings and walls.
void impact_sticky_soil(Cell *cells, int cx, int cy, int radius);

// Deposits a tall dense column of loose dirt at (cx, cy).
// Width = radius+2, height = radius*3. The existing dirt sim immediately
// makes it flow and fill low spots naturally (liquid soil effect).
void impact_liquid_soil(Cell *cells, int cx, int cy, int radius);
