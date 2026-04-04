#include "impact.h"
#include "water.h"   // unstick

void explode(uint8_t *world, int cx, int cy, int radius) {
    int r2 = radius * radius;
    for (int dy = -radius; dy <= radius; dy++) {
        for (int dx = -radius; dx <= radius; dx++) {
            if (dx*dx + dy*dy > r2) continue;
            int wx = cx + dx, wy = cy + dy;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            if (CELL_TYPE(world[wy * WORLD_W + wx]) == CELL_DIRT)
                world[wy * WORLD_W + wx] = CELL_AIR;
            unstick(world, wx,     wy - 1);
            unstick(world, wx - 1, wy);
            unstick(world, wx + 1, wy);
            unstick(world, wx,     wy + 1);
        }
    }
}

void impact_soil_ball(uint8_t *world, int cx, int cy, int radius) {
    int r2 = radius * radius;
    for (int dy = -radius; dy <= radius; dy++)
        for (int dx = -radius; dx <= radius; dx++) {
            if (dx*dx + dy*dy > r2) continue;
            int wx = cx + dx, wy = cy + dy;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            if (CELL_TYPE(world[wy * WORLD_W + wx]) == CELL_AIR)
                world[wy * WORLD_W + wx] = CELL_DIRT;  // loose — will fall
        }
}

void impact_sticky_soil(uint8_t *world, int cx, int cy, int radius) {
    int r2 = radius * radius;
    for (int dy = -radius; dy <= radius; dy++)
        for (int dx = -radius; dx <= radius; dx++) {
            if (dx*dx + dy*dy > r2) continue;
            int wx = cx + dx, wy = cy + dy;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            if (CELL_TYPE(world[wy * WORLD_W + wx]) == CELL_AIR)
                world[wy * WORLD_W + wx] = CELL_DIRT | FLAG_STICKY;
        }
}

void impact_liquid_soil(uint8_t *world, int cx, int cy, int radius) {
    int flood_r  = radius + 2;   // wider than solid ball
    int flood_h  = radius * 3;   // tall column so it slumps and spreads
    for (int dy = -flood_h; dy <= flood_r; dy++)
        for (int dx = -flood_r; dx <= flood_r; dx++) {
            if (dx*dx + (dy > 0 ? dy*dy : 0) > flood_r * flood_r) continue;
            int wx = cx + dx, wy = cy + dy;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            if (CELL_TYPE(world[wy * WORLD_W + wx]) == CELL_AIR)
                world[wy * WORLD_W + wx] = CELL_DIRT;  // loose, flows via tick_dirt
        }
}
