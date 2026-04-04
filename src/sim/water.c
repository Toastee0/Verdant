#include "water.h"

void tick_water(uint8_t *world, int bias) {
    for (int y = WORLD_H - 2; y >= 0; y--) {
        for (int xi = 0; xi < WORLD_W; xi++) {
            int x = bias ? xi : (WORLD_W - 1 - xi);
            if (CELL_TYPE(world[y * WORLD_W + x]) != CELL_WATER) continue;

            // 1. Fall straight down
            if (y + 1 < WORLD_H && CELL_TYPE(world[(y+1)*WORLD_W+x]) == CELL_AIR) {
                world[(y+1)*WORLD_W+x] = CELL_WATER;
                world[y*WORLD_W+x]     = CELL_AIR;
                continue;
            }

            // 2. Spread sideways — prefer the shallower column (communicating vessels).
            int col_l = 0, col_r = 0;
            int lx = x - 1, rx = x + 1;
            int can_l = (lx >= 0       && CELL_TYPE(world[y*WORLD_W+lx]) == CELL_AIR);
            int can_r = (rx < WORLD_W  && CELL_TYPE(world[y*WORLD_W+rx]) == CELL_AIR);

            if (can_l || can_r) {
                if (can_l) for (int cy = y-1; cy >= 0; cy--)
                    { if (CELL_TYPE(world[cy*WORLD_W+lx]) == CELL_WATER) col_l++; else break; }
                if (can_r) for (int cy = y-1; cy >= 0; cy--)
                    { if (CELL_TYPE(world[cy*WORLD_W+rx]) == CELL_WATER) col_r++; else break; }

                int go_left = 0;
                if (can_l && can_r) {
                    if      (col_l < col_r) go_left = 1;
                    else if (col_r < col_l) go_left = 0;
                    else                    go_left = bias;
                } else if (can_l) { go_left = 1; }

                int tx = go_left ? lx : rx;
                world[y*WORLD_W+tx] = CELL_WATER;
                world[y*WORLD_W+x]  = CELL_AIR;
            }
        }
    }
}

void unstick(uint8_t *world, int x, int y) {
    if (x < 0 || x >= WORLD_W || y < 0 || y >= WORLD_H) return;
    if (CELL_TYPE(world[y * WORLD_W + x]) == CELL_DIRT)
        world[y * WORLD_W + x] &= ~FLAG_STICKY;
}
