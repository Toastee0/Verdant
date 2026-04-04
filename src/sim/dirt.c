#include "dirt.h"
#include "../world.h"

void tick_dirt(Cell *cells, int bias) {
    for (int y = WORLD_H - 2; y >= 0; y--) {
        for (int xi = 0; xi < WORLD_W; xi++) {
            int x = bias ? xi : (WORLD_W - 1 - xi);
            uint8_t c = cells[y * WORLD_W + x].type;
            if (CELL_TYPE(c) != CELL_DIRT) continue;
            if (c & FLAG_STICKY) continue;

            int below = (y + 1) * WORLD_W + x;
            if (CELL_TYPE(cells[below].type) == CELL_AIR) {
                cells[below].type          = c;
                cells[y * WORLD_W + x].type = CELL_AIR;
                continue;
            }
            int dx0 = bias ? -1 : 1, dx1 = -dx0;
            for (int pass = 0; pass < 2; pass++) {
                int dx = (pass == 0) ? dx0 : dx1;
                int nx = x + dx;
                if (nx < 0 || nx >= WORLD_W) continue;
                int diag = (y + 1) * WORLD_W + nx;
                int side = y       * WORLD_W + nx;
                if (CELL_TYPE(cells[diag].type) == CELL_AIR &&
                    CELL_TYPE(cells[side].type) == CELL_AIR) {
                    cells[diag].type           = c;
                    cells[y * WORLD_W + x].type = CELL_AIR;
                    break;
                }
            }
        }
    }
}
