#include "water.h"

void tick_water(uint8_t *world, uint8_t *water, int bias) {
    for (int y = WORLD_H - 2; y >= 0; y--) {
        for (int xi = 0; xi < WORLD_W; xi++) {
            int x   = bias ? xi : (WORLD_W - 1 - xi);
            int idx = y * WORLD_W + x;

            if (CELL_TYPE(world[idx]) != CELL_AIR) continue;
            if (water[idx] == 0) continue;

            // 1. Gravity: fall into cell below as much as will fit.
            int below = (y + 1) * WORLD_W + x;
            if (CELL_TYPE(world[below]) == CELL_AIR) {
                int space = 255 - (int)water[below];
                int move  = (int)water[idx] < space ? (int)water[idx] : space;
                water[below] += (uint8_t)move;
                water[idx]   -= (uint8_t)move;
                if (water[idx] == 0) continue;
            }

            // 2. Equalization: halve the diff with each horizontal neighbour.
            // Left neighbour first, then right — bias reverses scan order so both
            // directions get equal priority across frames.
            int dx0 = bias ? -1 :  1;
            int dx1 = bias ?  1 : -1;
            for (int pass = 0; pass < 2; pass++) {
                int dx  = (pass == 0) ? dx0 : dx1;
                int nx  = x + dx;
                if (nx < 0 || nx >= WORLD_W) continue;
                int nidx = y * WORLD_W + nx;
                if (CELL_TYPE(world[nidx]) != CELL_AIR) continue;
                int diff = (int)water[idx] - (int)water[nidx];
                if (diff > 1) {
                    uint8_t t    = (uint8_t)(diff / 2);
                    water[idx]  -= t;
                    water[nidx] += t;
                }
            }

        }
    }

    // Pass 2: Upward pressure — water blocked below (solid or saturated) pushes
    // upward into the cell above if there is room. Bottom-to-top scan so the
    // effect cascades through the whole sealed column in one pass, simulating
    // near-instant pressure propagation (correct for an incompressible fluid).
    // This drives fountain jets and siphons.
    for (int y = WORLD_H - 2; y > 0; y--) {
        for (int xi = 0; xi < WORLD_W; xi++) {
            int x   = bias ? xi : (WORLD_W - 1 - xi);
            int idx = y * WORLD_W + x;
            if (CELL_TYPE(world[idx]) != CELL_AIR) continue;
            if (water[idx] == 0) continue;

            // Only push up when we cannot fall — gravity already handles the rest.
            int below = (y + 1) * WORLD_W + x;
            if (CELL_TYPE(world[below]) == CELL_AIR && water[below] < 255) continue;

            int above = (y - 1) * WORLD_W + x;
            if (CELL_TYPE(world[above]) != CELL_AIR) continue;

            // Equalize upward: halve the deficit between this cell and the one above.
            int diff = (int)water[idx] - (int)water[above];
            if (diff <= 0) continue;
            uint8_t move = (uint8_t)(diff / 2);
            if (move < 1) move = 1;
            if (move > water[idx]) move = water[idx];
            water[above] += move;
            water[idx]   -= move;
        }
    }
}

void unstick(uint8_t *world, int x, int y) {
    if (x < 0 || x >= WORLD_W || y < 0 || y >= WORLD_H) return;
    if (CELL_TYPE(world[y * WORLD_W + x]) == CELL_DIRT)
        world[y * WORLD_W + x] &= ~FLAG_STICKY;
}
