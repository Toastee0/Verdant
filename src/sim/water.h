#pragma once
#include "defs.h"

// Continuous water simulation using Cell.water (0..255 per CELL_AIR cell).
//
// Two passes per call, bottom-to-top:
//   Pass 1 — Gravity: fall into cell below as much as will fit.
//            Equalization: halve diff with each horizontal neighbour (flat surfaces).
//   Pass 2 — Upward pressure: cells blocked below push water upward into the
//            same blob only (blob_id check prevents pushing through walls).
//            Cascades the full column in one bottom-to-top sweep.
//
// bias: 0 or 1, alternates scan direction each frame.
void tick_water(Cell *cells, const uint16_t *blob_id, int bias);

// Clear FLAG_STICKY on the CELL_DIRT at (x,y), if present.
void unstick(Cell *cells, int x, int y);
