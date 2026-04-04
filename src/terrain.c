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

void terrain_generate(uint8_t *world, uint8_t *water) {
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

    // ── Fountain test: pressurized basin + sealed nozzle ─────────────────
    // A deep basin (150×73px) sealed on all sides. A narrow 3px nozzle exits
    // through the roof and extends 80px upward. The upward-pressure pass in
    // tick_water fills the sealed tube and drives a continuous fountain jet.
    // Store constants so we can clean up after the ceiling stalactite pass.
    const int BX    = 140;               // basin interior left x
    const int BY    = 95;                // basin interior top y
    const int BW    = 150;               // basin interior width
    const int BH    = dirtStart - BY - 2; // basin interior height (≈73px)
    const int BWALL = 2;                 // basin wall thickness

    const int NW    = 3;                 // nozzle interior width (px)
    const int NX    = BX + BW/2 - 1;    // nozzle interior left x (centred)
    const int NY    = 15;                // nozzle interior top y (above ceiling after clear)
    const int NH    = BY - NY;           // nozzle interior height (80px)
    const int NWALL = 1;                 // nozzle wall thickness

    {
        // Basin: four stone walls with a sealed roof (hole carved for nozzle).
        FILL(BX - BWALL, BY - BWALL, BW + BWALL*2, BWALL,        CELL_STONE); // roof
        FILL(BX - BWALL, BY + BH,    BW + BWALL*2, BWALL,        CELL_STONE); // floor
        FILL(BX - BWALL, BY - BWALL, BWALL,         BH + BWALL*2, CELL_STONE); // left wall
        FILL(BX + BW,    BY - BWALL, BWALL,         BH + BWALL*2, CELL_STONE); // right wall

        // Nozzle hole through the roof (3px wide, centred).
        FILL(NX, BY - BWALL, NW, BWALL, CELL_AIR);

        // Nozzle walls (placed now; stalactites may grow over the interior —
        // we re-clear the interior after the ceiling pass below).
        FILL(NX - NWALL, NY, NWALL, NH + BWALL, CELL_STONE); // left nozzle wall
        FILL(NX + NW,    NY, NWALL, NH + BWALL, CELL_STONE); // right nozzle wall

        // Basin interior: clear any overlapping terrain, then flood with water.
        FILL(BX, BY, BW, BH, CELL_AIR);
        for (int wy = BY; wy < BY + BH; wy++)
            for (int wx = BX; wx < BX + BW; wx++)
                water[wy * WORLD_W + wx] = 255;

        // Prime the roof hole with water so pressure starts immediately.
        for (int wy = BY - BWALL; wy < BY; wy++)
            for (int wx = NX; wx < NX + NW; wx++)
                water[wy * WORLD_W + wx] = 255;
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

    // ── Fountain post-pass: clear nozzle interior + open shaft above ──────
    // Ceiling stalactites may have grown into the nozzle tube. Clear them and
    // punch an open-air shaft above the nozzle exit so the jet can arc freely.
    FILL(NX - 20, 0, NW + 40, NY,        CELL_AIR);  // open arc space above nozzle
    FILL(NX,      NY, NW,     NH + BWALL, CELL_AIR);  // clear nozzle interior
    // Restore nozzle walls (ceiling gen may have punched holes).
    FILL(NX - NWALL, NY, NWALL, NH + BWALL, CELL_STONE);
    FILL(NX + NW,    NY, NWALL, NH + BWALL, CELL_STONE);
}

#undef FILL
