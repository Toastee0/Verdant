#include "terrain.h"
#include "noise.h"

// Helper macro: fill a rectangle with a cell type (writes .type only).
#define FILL(X,Y,W,H,C) do { \
    for (int _y=(Y);_y<(Y)+(H);_y++) \
        for (int _x=(X);_x<(X)+(W);_x++) \
            if (_x>=0&&_x<WORLD_W&&_y>=0&&_y<WORLD_H) \
                cells[_y*WORLD_W+_x].type=(C); \
} while(0)

void terrain_generate(Cell *cells) {
    // Zero all fields, then set ambient temp on every cell.
    memset(cells, 0, sizeof(Cell) * WORLD_W * WORLD_H);
    for (int i = 0; i < WORLD_W * WORLD_H; i++) {
        cells[i].temp   = 128;   // ambient temperature
        cells[i].vector = 0;
    }

    const int stoneStart = (WORLD_H * 2) / 3;   // row 180

    // ── Stone floor ───────────────────────────────────────────────────────
    FILL(0, stoneStart, WORLD_W, WORLD_H - stoneStart, CELL_STONE);

    // ── Raised stone ramp (right half) ────────────────────────────────────
    for (int x = 300; x < WORLD_W; x++) {
        int extra   = (x < 380) ? ((x - 300) / 4 + 1) : 20;
        int surface = stoneStart - extra;
        FILL(x, surface, 1, stoneStart - surface, CELL_STONE);
    }

    // ── Sticky dirt layer (left flat section) ─────────────────────────────
    const int dirtStart = stoneStart - 10;   // row 170
    FILL(0, dirtStart, 300, stoneStart - dirtStart, CELL_DIRT | FLAG_STICKY);

    // ── One-way platforms ─────────────────────────────────────────────────
    typedef struct { int x, y, w, h; } PlatDef;
    PlatDef plats[] = {
        {  50, 158, 20, 4 },
        { 110, 143, 20, 4 },
        { 180, 150, 20, 4 },
    };
    for (int i = 0; i < 3; i++)
        FILL(plats[i].x, plats[i].y, plats[i].w, plats[i].h, CELL_PLATFORM);

    // ── Communicating basins (U-tube equalization demo) ───────────────────
    // Two stone chambers connected by a channel at the base.
    // Left basin starts full; right starts empty.
    // Water flows down-left, along the bottom, up-right until levels equalize.
    {
        const int BW   = 50;   // interior width of each basin
        const int WALL = 2;    // wall thickness
        const int DIV  = 4;    // dividing wall thickness
        const int BY   = 108;  // top of basin interior
        const int BH   = dirtStart - BY - WALL;
        const int CHH  = 20;   // channel height from bottom (U-tube opening)

        const int lx  = 148;              // left basin interior left edge
        const int div = lx + BW;          // dividing wall left edge
        const int rx  = div + DIV;        // right basin interior left edge
        const int bot = BY + BH;          // bottom interior row

        // Outer walls
        FILL(lx - WALL,  BY - WALL, WALL,              BH + WALL*2, CELL_STONE); // left wall
        FILL(rx + BW,    BY - WALL, WALL,              BH + WALL*2, CELL_STONE); // right wall
        FILL(lx - WALL,  BY - WALL, rx + BW + WALL - (lx - WALL), WALL, CELL_STONE); // top
        FILL(lx - WALL,  bot,       rx + BW + WALL - (lx - WALL), WALL, CELL_STONE); // bottom

        // Dividing wall with U-tube channel carved at the base
        FILL(div, BY - WALL, DIV, BH + WALL*2, CELL_STONE);
        FILL(div, bot - CHH, DIV, CHH,         CELL_AIR);

        // Basin interiors (clear any terrain that landed here)
        FILL(lx, BY, BW, BH, CELL_AIR);
        FILL(rx, BY, BW, BH, CELL_AIR);

        // Fill left basin with water=255
        for (int wy = BY; wy < BY + BH; wy++)
            for (int wx = lx; wx < lx + BW; wx++)
                cells[wy * WORLD_W + wx].water = 255;

        // Prime the channel so pressure rule kicks in immediately
        for (int wy = bot - CHH; wy < bot; wy++)
            for (int wx = div; wx < div + DIV; wx++)
                cells[wy * WORLD_W + wx].water = 255;
    }

    // ── Procedural ceiling: rock base + sticky-dirt stalactites ───────────
    for (int x = 0; x < WORLD_W; x++) {
        float xf = (float)x;

        float rock_n = fbm(xf * 0.04f, 42);
        float rock_w = triwave(xf, 60.0f) * 0.4f
                     + triwave(xf, 23.0f) * 0.25f;
        float rock_depth_f = 3.0f + (rock_n + rock_w) * 9.0f;
        int rock_depth = (int)rock_depth_f;
        if (rock_depth < 2) rock_depth = 2;
        if (rock_depth > 14) rock_depth = 14;

        FILL(x, 0, 1, rock_depth, CELL_STONE);

        float dirt_n  = fbm(xf * 0.07f, 137);
        float dirt_sp = spike(xf, 18.0f) * 0.6f
                      + spike(xf, 7.0f)  * 0.3f
                      + dirt_n           * 0.35f;
        int dirt_depth = (int)(dirt_sp * 14.0f);
        if (dirt_depth < 0) dirt_depth = 0;
        if (dirt_depth > 18) dirt_depth = 18;

        for (int y = rock_depth; y < rock_depth + dirt_depth; y++) {
            if (y >= WORLD_H) break;
            if (CELL_TYPE(cells[y * WORLD_W + x].type) == CELL_AIR)
                cells[y * WORLD_W + x].type = CELL_DIRT | FLAG_STICKY;
        }
    }


}

#undef FILL
