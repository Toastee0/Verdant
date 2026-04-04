#include "impact.h"
#include "water.h"   // unstick

void explode(Cell *cells, int cx, int cy, int radius) {
    int r2 = radius * radius;
    for (int dy = -radius; dy <= radius; dy++) {
        for (int dx = -radius; dx <= radius; dx++) {
            if (dx*dx + dy*dy > r2) continue;
            int wx = cx + dx, wy = cy + dy;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            if (CELL_TYPE(cells[wy * WORLD_W + wx].type) == CELL_DIRT)
                cells[wy * WORLD_W + wx].type = CELL_AIR;
            unstick(cells, wx,     wy - 1);
            unstick(cells, wx - 1, wy);
            unstick(cells, wx + 1, wy);
            unstick(cells, wx,     wy + 1);
        }
    }
}

void impact_soil_ball(Cell *cells, int cx, int cy, int radius) {
    int r2 = radius * radius;
    for (int dy = -radius; dy <= radius; dy++)
        for (int dx = -radius; dx <= radius; dx++) {
            if (dx*dx + dy*dy > r2) continue;
            int wx = cx + dx, wy = cy + dy;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            if (CELL_TYPE(cells[wy * WORLD_W + wx].type) == CELL_AIR)
                cells[wy * WORLD_W + wx].type = CELL_DIRT;
        }
}

void impact_sticky_soil(Cell *cells, int cx, int cy, int radius) {
    int r2 = radius * radius;
    for (int dy = -radius; dy <= radius; dy++)
        for (int dx = -radius; dx <= radius; dx++) {
            if (dx*dx + dy*dy > r2) continue;
            int wx = cx + dx, wy = cy + dy;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            if (CELL_TYPE(cells[wy * WORLD_W + wx].type) == CELL_AIR)
                cells[wy * WORLD_W + wx].type = CELL_DIRT | FLAG_STICKY;
        }
}

void impact_liquid_soil(Cell *cells, int cx, int cy, int radius) {
    int flood_r  = radius + 2;
    int flood_h  = radius * 3;
    for (int dy = -flood_h; dy <= flood_r; dy++)
        for (int dx = -flood_r; dx <= flood_r; dx++) {
            if (dx*dx + (dy > 0 ? dy*dy : 0) > flood_r * flood_r) continue;
            int wx = cx + dx, wy = cy + dy;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            if (CELL_TYPE(cells[wy * WORLD_W + wx].type) == CELL_AIR)
                cells[wy * WORLD_W + wx].type = CELL_DIRT;
        }
}
