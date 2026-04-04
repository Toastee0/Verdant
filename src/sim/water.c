#include "water.h"

void tick_water(Cell *cells, const uint16_t *blob_id, int bias) {
    // Pass 1: Gravity + equalization — bottom-to-top.
    for (int y = WORLD_H - 2; y >= 0; y--) {
        for (int xi = 0; xi < WORLD_W; xi++) {
            int x   = bias ? xi : (WORLD_W - 1 - xi);
            int idx = y * WORLD_W + x;

            if (CELL_TYPE(cells[idx].type) != CELL_AIR) continue;
            if (cells[idx].water == 0) continue;

            // Gravity: fall into the cell below as much as will fit.
            int below = (y + 1) * WORLD_W + x;
            if (CELL_TYPE(cells[below].type) == CELL_AIR) {
                int space = 255 - (int)cells[below].water;
                int move  = (int)cells[idx].water < space ? (int)cells[idx].water : space;
                cells[below].water += (uint8_t)move;
                cells[idx].water   -= (uint8_t)move;
                if (cells[idx].water == 0) continue;
            }

            // Equalization: halve diff with each horizontal neighbour.
            // bias reverses order so both directions get equal priority across frames.
            int dx0 = bias ? -1 :  1;
            int dx1 = bias ?  1 : -1;
            for (int pass = 0; pass < 2; pass++) {
                int nx = x + (pass == 0 ? dx0 : dx1);
                if (nx < 0 || nx >= WORLD_W) continue;
                int nidx = y * WORLD_W + nx;
                if (CELL_TYPE(cells[nidx].type) != CELL_AIR) continue;
                int diff = (int)cells[idx].water - (int)cells[nidx].water;
                if (diff > 1) {
                    uint8_t t         = (uint8_t)(diff / 2);
                    cells[idx].water  -= t;
                    cells[nidx].water += t;
                }
            }
        }
    }

    // Pass 2: Upward pressure — blob-gated.
    // A cell blocked below pushes water upward, but only within the same connected
    // air region (blob). This is what drives communicating vessels and siphons.
    // Bottom-to-top so the effect cascades through a full column in one sweep.
    for (int y = WORLD_H - 2; y > 0; y--) {
        for (int xi = 0; xi < WORLD_W; xi++) {
            int x   = bias ? xi : (WORLD_W - 1 - xi);
            int idx = y * WORLD_W + x;
            if (CELL_TYPE(cells[idx].type) != CELL_AIR) continue;
            if (cells[idx].water == 0) continue;

            // Only push up when we cannot fall further.
            int below = (y + 1) * WORLD_W + x;
            int blocked = (CELL_TYPE(cells[below].type) != CELL_AIR) ||
                          (cells[below].water == 255);
            if (!blocked) continue;

            int above = (y - 1) * WORLD_W + x;
            if (CELL_TYPE(cells[above].type) != CELL_AIR) continue;

            // Must be in the same connected blob — prevents pushing through walls.
            uint16_t bid = blob_id[idx];
            if (bid == BLOB_NONE || bid != blob_id[above]) continue;

            int diff = (int)cells[idx].water - (int)cells[above].water;
            if (diff <= 0) continue;
            uint8_t move = (uint8_t)(diff / 2);
            if (move < 1) move = 1;
            if (move > cells[idx].water) move = cells[idx].water;
            cells[above].water += move;
            cells[idx].water   -= move;
        }
    }
}

void unstick(Cell *cells, int x, int y) {
    if (x < 0 || x >= WORLD_W || y < 0 || y >= WORLD_H) return;
    if (CELL_TYPE(cells[y * WORLD_W + x].type) == CELL_DIRT)
        cells[y * WORLD_W + x].type &= ~FLAG_STICKY;
}
