#include "terrain.h"
#include "noise.h"
#include "sim/water.h"   // for unstick (not used here, but sim deps go through this)

// Helper macro: fill a rectangle in world[] with a cell type.
// Only used during terrain_generate — not exported.
#define FILL(X,Y,W,H,C) do { \
    for (int _y=(Y);_y<(Y)+(H);_y++) \
        for (int _x=(X);_x<(X)+(W);_x++) \
            if (_x>=0&&_x<WORLD_W&&_y>=0&&_y<WORLD_H) \
                world[_y*WORLD_W+_x]=(C); \
} while(0)

void terrain_generate(uint8_t *world) {
    memset(world, CELL_AIR, WORLD_W * WORLD_H);

    const int stoneStart = (WORLD_H * 2) / 3;   // row 180

    // ── Stone floor ───────────────────────────────────────────────────────
    for (int y = stoneStart; y < WORLD_H; y++)
        memset(&world[y * WORLD_W], CELL_STONE, WORLD_W);

    // ── Raised stone ramp (right half) ────────────────────────────────────
    for (int x = 300; x < WORLD_W; x++) {
        int extra   = (x < 380) ? ((x - 300) / 4 + 1) : 20;
        int surface = stoneStart - extra;
        for (int y = surface; y < stoneStart; y++)
            world[y * WORLD_W + x] = CELL_STONE;
    }

    // ── Sticky dirt layer (left flat section) ─────────────────────────────
    const int dirtStart = stoneStart - 10;   // row 170
    for (int y = dirtStart; y < stoneStart; y++)
        for (int x = 0; x < 300; x++)
            world[y * WORLD_W + x] = CELL_DIRT | FLAG_STICKY;

    // ── One-way platforms ─────────────────────────────────────────────────
    typedef struct { int x, y, w, h; } PlatDef;
    PlatDef plats[] = {
        {  50, 158, 20, 4 },
        { 110, 143, 20, 4 },
        { 180, 150, 20, 4 },
    };
    for (int i = 0; i < 3; i++)
        for (int py = plats[i].y; py < plats[i].y + plats[i].h; py++)
            for (int px = plats[i].x; px < plats[i].x + plats[i].w; px++)
                if (px < WORLD_W && py < WORLD_H)
                    world[py * WORLD_W + px] = CELL_PLATFORM;

    // ── Communicating basins (water equalization demo) ────────────────────
    // Two stone-walled chambers connected by a 3px channel at the base.
    // Left basin starts full; right basin starts empty.
    {
        const int BX   = 148;   // left outer wall x
        const int BY   = 108;   // top of basin interior
        const int BW   = 50;    // interior width of each basin
        const int WALL = 2;     // wall thickness
        const int DIV  = 4;     // divider thickness
        const int BH   = dirtStart - BY - WALL;
        const int CHW  = 3;     // channel width (unused — channel carved below)
        const int CHH  = 20;    // channel height from bottom
        (void)CHW;

        int lx  = BX;
        int div = BX + WALL + BW;
        int rx  = div + DIV;
        int bot = BY + BH;

        FILL(lx - WALL,     BY - WALL, WALL, BH + WALL*2, CELL_STONE);
        FILL(rx + BW,       BY - WALL, WALL, BH + WALL*2, CELL_STONE);
        FILL(lx - WALL,     BY - WALL, rx + BW + WALL - (lx-WALL), WALL, CELL_STONE);
        FILL(lx - WALL,     bot,       rx + BW + WALL - (lx-WALL), WALL, CELL_STONE);

        FILL(div, BY - WALL, DIV, BH + WALL*2, CELL_STONE);
        int ch_y = bot - CHH;
        FILL(div, ch_y, DIV, CHH, CELL_AIR);

        FILL(lx, BY, BW, BH, CELL_AIR);
        FILL(rx, BY, BW, BH, CELL_AIR);

        FILL(lx, BY, BW, BH, CELL_WATER);
    }

    // ── Procedural ceiling: rock base + sticky-dirt stalactites ───────────
    for (int x = 0; x < WORLD_W; x++) {
        float xf = (float)x;

        // Rock layer driven by fbm + triangle waves
        float rock_n = fbm(xf * 0.04f, 42);
        float rock_w = triwave(xf, 60.0f) * 0.4f
                     + triwave(xf, 23.0f) * 0.25f;
        float rock_depth_f = 3.0f + (rock_n + rock_w) * 9.0f;
        int rock_depth = (int)rock_depth_f;
        if (rock_depth < 2) rock_depth = 2;
        if (rock_depth > 14) rock_depth = 14;

        for (int y = 0; y < rock_depth; y++)
            world[y * WORLD_W + x] = CELL_STONE;

        // Dirt stalactites hanging below rock edge
        float dirt_n  = fbm(xf * 0.07f, 137);
        float dirt_sp = spike(xf, 18.0f) * 0.6f
                      + spike(xf, 7.0f)  * 0.3f
                      + dirt_n           * 0.35f;
        int dirt_depth = (int)(dirt_sp * 14.0f);
        if (dirt_depth < 0) dirt_depth = 0;
        if (dirt_depth > 18) dirt_depth = 18;

        for (int y = rock_depth; y < rock_depth + dirt_depth; y++) {
            if (y >= WORLD_H) break;
            if (CELL_TYPE(world[y * WORLD_W + x]) == CELL_AIR)
                world[y * WORLD_W + x] = CELL_DIRT | FLAG_STICKY;
        }
    }
}

#undef FILL
