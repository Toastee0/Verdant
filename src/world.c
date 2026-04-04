#include "world.h"

int ground_y_at(const uint8_t *w, int wx, int start_y) {
    if (wx < 0 || wx >= WORLD_W) return start_y;
    for (int y = start_y; y < WORLD_H; y++) {
        uint8_t t = CELL_TYPE(w[y * WORLD_W + wx]);
        if (t == CELL_STONE || t == CELL_DIRT) return y;
    }
    return WORLD_H;
}

int box_solid_ex(const uint8_t *w, float bx, float by,
                 int bw, int bh, int include_platform) {
    int x0 = (int)bx,  x1 = (int)bx + bw - 1;
    int y0 = (int)by,  y1 = (int)by + bh - 1;
    for (int y = y0; y <= y1; y++) {
        for (int x = x0; x <= x1; x++) {
            if (x < 0 || x >= WORLD_W || y < 0 || y >= WORLD_H) return 1;
            uint8_t t = CELL_TYPE(w[y * WORLD_W + x]);
            if (t == CELL_STONE || t == CELL_DIRT) return 1;
            if (t == CELL_PLATFORM && include_platform) return 1;
            // CELL_WATER is passable — player and rover move through it
        }
    }
    return 0;
}
