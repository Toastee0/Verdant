#include "raylib.h"
#include <stdint.h>
#include <string.h>
#include <math.h>

// === WORLD ===
#define WORLD_W      480
#define WORLD_H      270
#define CELL_AIR       0
#define CELL_STONE     1
#define CELL_DIRT      2
#define CELL_PLATFORM  3   // one-way: stand on top, jump/walk through
#define CELL_WATER     4   // liquid — falls, spreads, equalizes

// Bit 7 of a cell byte: dirt won't fall while this is set.
// Generated terrain starts sticky. Digging a neighbour clears it.
#define FLAG_STICKY    0x80
#define CELL_TYPE(c)   ((c) & 0x7F)

// === PLAYER ===
#define CHAR_W  4
#define CHAR_H  8

#define GRAVITY       0.35f
#define JUMP_VEL     -5.5f
#define WALK_SPEED    1.5f
#define MAX_FALL     10.0f
#define MAX_STEP_UP    3
#define COYOTE_FRAMES  8   // frames after leaving ground where jump still fires

#define PICKUP_RADIUS   18
#define INV_MAX         99
#define DIG_REPEAT_MS   80

// === ROVER ===
#define ROVER_W            24
#define ROVER_H            16
#define ROVER_MAX_FALL     18.0f
#define ROVER_STEP_UP       4    // enough for stair steps, not sheer walls
#define ROVER_ENTER_R      24   // world-pixel radius to trigger enter/exit prompt

// Rover dynamics — all per-frame at ~60fps
#define ROVER_ACCEL        0.18f
#define ROVER_TOP_SPEED    3.8f
#define ROVER_BRAKE_DRAG   0.72f
#define ROVER_ROLL_DRAG    0.97f
#define ROVER_PARK_DRAG    0.80f
#define ROVER_SLOPE_FORCE  0.06f
#define ROVER_BOUNCE       0.35f

// === AMMO ===
#define AMMO_SOIL_BALL     0   // loose dirt circle, falls on impact
#define AMMO_STICKY_SOIL   1   // sticky dirt, adheres to ceiling/walls
#define AMMO_LIQUID_SOIL   2   // floods impact zone, flows into gaps
#define AMMO_COUNT         3
static const char *AMMO_NAMES[AMMO_COUNT] = { "SOIL BALL", "STICKY SOIL", "LIQUID SOIL" };
// Deposit radius scales with power: DEPOSIT_R_MIN + charge * range = 2..5
#define DEPOSIT_R_MIN      2
#define DEPOSIT_R_MAX      5

// === BALLISTIC ARM ===
#define ARM_LEN            9      // barrel length in pixels
// arm_angle is absolute: 0°=right, 90°=up, 180°=left
#define ARM_ANGLE_MIN      5.0f   // nearly horizontal right
#define ARM_ANGLE_MAX    175.0f   // nearly horizontal left
#define ARM_ANGLE_SPEED    1.8f   // degrees per frame
#define ARM_POWER_MIN      2.5f   // minimum launch speed (pixels/frame)
#define ARM_POWER_MAX      9.0f   // maximum launch speed
#define ARM_CHARGE_RATE    0.016f // power fraction gained per frame (full charge ~60f)
#define BLAST_RADIUS       5      // explosion carve radius (pixels)
#define PROJ_GRAVITY       0.25f  // projectile falls slower than player for nice arcs

// ── Player sprite ──────────────────────────────────────────────────────────
// 2-frame walk cycle. Palette: 0=transparent 1=skin 2=shirt 3=pants
static const uint8_t SPRITE[2][CHAR_H][CHAR_W] = {
    {   // frame 0 — legs wide
        {0,1,1,0}, {0,1,1,0}, {0,1,1,0},
        {2,2,2,2}, {2,2,2,2},
        {3,3,3,3}, {3,0,0,3}, {3,0,0,3},
    },
    {   // frame 1 — legs together
        {0,1,1,0}, {0,1,1,0}, {0,1,1,0},
        {2,2,2,2}, {2,2,2,2},
        {3,3,3,3}, {0,3,3,0}, {3,0,0,3},
    },
};
static const Color CHAR_PAL[4] = {
    {  0,   0,   0,   0},
    {220, 180, 120, 255},
    { 60, 120, 200, 255},
    { 40,  60, 120, 255},
};

// ── Rover sprite ───────────────────────────────────────────────────────────
// Palette: 0=transparent 1=dark body 2=body 3=dash/detail 4=glass 5=wheel 6=rim 7=headlight
static const Color ROVER_PAL[8] = {
    {  0,   0,   0,   0},   // 0  transparent
    { 50,  65,  40, 255},   // 1  dark body
    { 85, 110,  60, 255},   // 2  body
    {110, 140,  85, 255},   // 3  body highlight / dash
    { 88, 158, 198, 255},   // 4  glass
    { 35,  35,  35, 255},   // 5  wheel rubber
    { 68,  68,  72, 255},   // 6  wheel rim
    {235, 205,  70, 255},   // 7  headlight (right side when facing right)
};

// 24 wide × 16 tall. Wide low-slung rover, 9px wheels (+2 radius vs old).
// Headlight at col 23 (mirrored to col 0 when facing left).
// Left wheel: cols 0-7. Right wheel: cols 16-23. Body: cols 0-23.
static const uint8_t ROVER_SPRITE[ROVER_H][ROVER_W] = {
    //  0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23
    {   0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0 }, //  0 body top
    {   0, 0, 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1, 0, 0, 0, 7 }, //  1 windshield + headlight
    {   0, 0, 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1, 0, 0, 0, 7 }, //  2
    {   0, 0, 1, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 2, 1, 0, 0, 0, 0 }, //  3 dash bar
    {   1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1 }, //  4 body sides
    {   1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1 }, //  5
    {   1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1 }, //  6 chassis rail
    {   0, 0, 5, 5, 5, 5, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 5, 5, 5, 5, 0, 0 }, //  7 wheel top
    {   0, 5, 6, 6, 6, 6, 6, 5, 0, 0, 0, 0, 0, 0, 0, 0, 5, 6, 6, 6, 6, 6, 5, 0 }, //  8
    {   5, 6, 6, 6, 6, 6, 6, 6, 5, 0, 0, 0, 0, 0, 0, 5, 6, 6, 6, 6, 6, 6, 6, 5 }, //  9
    {   5, 6, 6, 1, 1, 1, 6, 6, 5, 0, 0, 0, 0, 0, 0, 5, 6, 6, 1, 1, 1, 6, 6, 5 }, // 10 hub
    {   5, 6, 1, 1, 1, 1, 1, 6, 5, 0, 0, 0, 0, 0, 0, 5, 6, 1, 1, 1, 1, 1, 6, 5 }, // 11 hub centre
    {   5, 6, 6, 1, 1, 1, 6, 6, 5, 0, 0, 0, 0, 0, 0, 5, 6, 6, 1, 1, 1, 6, 6, 5 }, // 12 hub
    {   5, 6, 6, 6, 6, 6, 6, 6, 5, 0, 0, 0, 0, 0, 0, 5, 6, 6, 6, 6, 6, 6, 6, 5 }, // 13
    {   0, 5, 6, 6, 6, 6, 6, 5, 0, 0, 0, 0, 0, 0, 0, 0, 5, 6, 6, 6, 6, 6, 5, 0 }, // 14 wheel bottom
    {   0, 0, 5, 5, 5, 5, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 5, 5, 5, 5, 0, 0 }, // 15 ground contact
};

// ── Noise & wave helpers ───────────────────────────────────────────────────
// Deterministic hash → float [0,1]
static float hash1(int n) {
    n = (n << 13) ^ n;
    n = n * (n * n * 15731 + 789221) + 1376312589;
    return (float)(n & 0x7fffffff) / (float)0x7fffffff;
}

// Smooth value noise: interpolate between hashed lattice points
static float vnoise(float x, int seed) {
    int   ix = (int)floorf(x);
    float fx = x - (float)ix;
    float t  = fx * fx * (3.0f - 2.0f * fx);  // smoothstep
    float a  = hash1(ix * 1619 + seed);
    float b  = hash1((ix + 1) * 1619 + seed);
    return a + t * (b - a);
}

// Fractal value noise: 4 octaves
static float fbm(float x, int seed) {
    float v = 0.0f, amp = 0.5f, freq = 1.0f;
    for (int o = 0; o < 4; o++) {
        v    += vnoise(x * freq, seed + o * 997) * amp;
        freq *= 2.1f;
        amp  *= 0.5f;
    }
    return v;   // ~[0,1]
}

// Triangle wave: period p, range [0,1]
static float triwave(float x, float p) {
    float t = fmodf(x / p, 1.0f);
    return (t < 0.5f) ? (2.0f * t) : (2.0f - 2.0f * t);
}

// Spiky sine: |sin|^0.25 — thin base, sharp peaks
static float spike(float x, float p) {
    float s = sinf(x * (float)M_PI * 2.0f / p);
    s = s < 0.0f ? -s : s;    // |sin|
    return powf(s, 0.25f);    // sharpen toward 1, crush near 0
}

// ── Surface normal helpers ─────────────────────────────────────────────────
// Returns the y of the first solid cell at or below (wx, start_y).
// Used to find ground height under each wheel.
static int ground_y_at(const uint8_t *w, int wx, int start_y) {
    if (wx < 0 || wx >= WORLD_W) return start_y;
    for (int y = start_y; y < WORLD_H; y++) {
        uint8_t t = CELL_TYPE(w[y * WORLD_W + wx]);
        if (t == CELL_STONE || t == CELL_DIRT) return y;
    }
    return WORLD_H;
}

// Draw the rover sprite sheared to match the slope between its two wheel contacts.
// slope = right_ground_y - left_ground_y (positive = right side lower).
// Each column is offset vertically by slope * col / ROVER_W — left cols rise,
// right cols fall, matching the surface angle under the wheels.
static void draw_rover_sheared(Color *pixels, int rx, int ry,
                                int rfacing, int slope) {
    for (int row = 0; row < ROVER_H; row++) {
        for (int col = 0; col < ROVER_W; col++) {
            int src = (rfacing < 0) ? (ROVER_W - 1 - col) : col;
            uint8_t idx = ROVER_SPRITE[row][src];
            if (idx == 0) continue;
            // Vertical shear: columns shift up/down based on horizontal position
            int shear = (slope * col) / ROVER_W;
            int wx = rx + col;
            int wy = ry + row + shear;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            pixels[wy * WORLD_W + wx] = ROVER_PAL[idx];
        }
    }
}

// ── Collision ──────────────────────────────────────────────────────────────
// CELL_STONE and CELL_DIRT are always solid.
// CELL_PLATFORM solid only when include_platform=1 (downward player collision).
// Out-of-bounds always solid.
static int box_solid_ex(const uint8_t *w, float bx, float by,
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

// ── Dirt sand simulation ───────────────────────────────────────────────────
static void tick_dirt(uint8_t *world, int bias) {
    for (int y = WORLD_H - 2; y >= 0; y--) {
        for (int xi = 0; xi < WORLD_W; xi++) {
            int x = bias ? xi : (WORLD_W - 1 - xi);
            uint8_t c = world[y * WORLD_W + x];
            if (CELL_TYPE(c) != CELL_DIRT) continue;
            if (c & FLAG_STICKY) continue;

            int below = (y + 1) * WORLD_W + x;
            if (CELL_TYPE(world[below]) == CELL_AIR) {
                world[below] = c;
                world[y * WORLD_W + x] = CELL_AIR;
                continue;
            }
            // Dirt sinks through water (displaces it upward)
            if (CELL_TYPE(world[below]) == CELL_WATER) {
                world[y * WORLD_W + x] = CELL_WATER;
                world[below]           = c;
                continue;
            }
            int dx0 = bias ? -1 : 1, dx1 = -dx0;
            for (int pass = 0; pass < 2; pass++) {
                int dx = (pass == 0) ? dx0 : dx1;
                int nx = x + dx;
                if (nx < 0 || nx >= WORLD_W) continue;
                int diag = (y + 1) * WORLD_W + nx;
                int side = y       * WORLD_W + nx;
                if (CELL_TYPE(world[diag]) == CELL_AIR &&
                    CELL_TYPE(world[side]) == CELL_AIR) {
                    world[diag] = c;
                    world[y * WORLD_W + x] = CELL_AIR;
                    break;
                }
            }
        }
    }
}

// ── Water simulation ───────────────────────────────────────────────────────
// Bottom-up scan. Each cell tries to:
//   1. Fall into air below
//   2. Fall diagonally (like sand but no angle-of-repose — water fills flat)
//   3. Spread sideways into air
// Run multiple passes per frame for fast equalization across connected basins.
// Water displaces nothing — it only moves into CELL_AIR.
static void tick_water(uint8_t *world, int bias) {
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

            // 2. Spread sideways — try both directions, prefer the side whose
            //    water column is shorter (communicating vessels).
            //    Measure column height: count water cells upward from y+1.
            int col_l = 0, col_r = 0;
            int lx = x - 1, rx2 = x + 1;
            int can_l = (lx >= 0        && CELL_TYPE(world[y*WORLD_W+lx])  == CELL_AIR);
            int can_r = (rx2 < WORLD_W  && CELL_TYPE(world[y*WORLD_W+rx2]) == CELL_AIR);

            if (can_l || can_r) {
                // Count water height on each side (how many water rows above the spread target)
                if (can_l) for (int cy2 = y-1; cy2 >= 0; cy2--)
                    { if (CELL_TYPE(world[cy2*WORLD_W+lx]) == CELL_WATER) col_l++; else break; }
                if (can_r) for (int cy2 = y-1; cy2 >= 0; cy2--)
                    { if (CELL_TYPE(world[cy2*WORLD_W+rx2]) == CELL_WATER) col_r++; else break; }

                // Pick the shorter column (or use bias to break ties)
                int go_left = 0, go_right = 0;
                if (can_l && can_r) {
                    if      (col_l < col_r) go_left  = 1;
                    else if (col_r < col_l) go_right = 1;
                    else { if (bias) go_left = 1; else go_right = 1; }
                } else if (can_l) { go_left  = 1; }
                  else             { go_right = 1; }

                int tx = go_left ? lx : rx2;
                world[y*WORLD_W+tx] = CELL_WATER;
                world[y*WORLD_W+x]  = CELL_AIR;
            }
        }
    }
}

static void unstick(uint8_t *world, int x, int y) {
    if (x < 0 || x >= WORLD_W || y < 0 || y >= WORLD_H) return;
    if (CELL_TYPE(world[y * WORLD_W + x]) == CELL_DIRT)
        world[y * WORLD_W + x] &= ~FLAG_STICKY;
}

// ── Explosion ─────────────────────────────────────────────────────────────
// Carves a circular crater. Dirt destroyed; stone unsticks neighbours so
// overhanging dirt collapses into the crater.
static void explode(uint8_t *world, int cx, int cy, int radius) {
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

// ── Soil ball impact ──────────────────────────────────────────────────────
// Deposits loose dirt in a circle. Only fills AIR — won't overwrite terrain.
static void impact_soil_ball(uint8_t *world, int cx, int cy, int radius) {
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

// ── Sticky soil ball impact ───────────────────────────────────────────────
// Same circle but FLAG_STICKY set — adheres to ceiling and walls.
static void impact_sticky_soil(uint8_t *world, int cx, int cy, int radius) {
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

// ── Liquid soil impact ────────────────────────────────────────────────────
// Deposits a dense column of loose dirt at the impact point. The existing
// sand sim immediately makes it flow and fill low spots naturally.
// Deposits more material than a soil ball (wider + taller column).
static void impact_liquid_soil(uint8_t *world, int cx, int cy, int radius) {
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

int main(void)
{
    SetConfigFlags(FLAG_BORDERLESS_WINDOWED_MODE | FLAG_VSYNC_HINT);
    InitWindow(0, 0, "VERDANT F1 — Scaled Canvas");

    // ── World setup ───────────────────────────────────────────────────────
    uint8_t world[WORLD_W * WORLD_H];
    memset(world, CELL_AIR, sizeof(world));
    const int stoneStart = (WORLD_H * 2) / 3;   // row 180

    for (int y = stoneStart; y < WORLD_H; y++)
        memset(&world[y * WORLD_W], CELL_STONE, WORLD_W);

    for (int x = 300; x < WORLD_W; x++) {
        int extra   = (x < 380) ? ((x - 300) / 4 + 1) : 20;
        int surface = stoneStart - extra;
        for (int y = surface; y < stoneStart; y++)
            world[y * WORLD_W + x] = CELL_STONE;
    }

    const int dirtStart = stoneStart - 10;   // row 170
    for (int y = dirtStart; y < stoneStart; y++)
        for (int x = 0; x < 300; x++)
            world[y * WORLD_W + x] = CELL_DIRT | FLAG_STICKY;

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
    // Goal: water equalizes through the channel, then overflows the lower lip.
    {
        const int BX   = 148;   // left outer wall x
        const int BY   = 108;   // top of basin interior
        const int BW   = 50;    // interior width of each basin
        const int WALL = 2;     // wall thickness
        const int DIV  = 4;     // divider thickness
        const int BH   = dirtStart - BY - WALL;  // interior height (reaches dirt floor)
        const int CHW  = 3;     // channel width (pixels)
        const int CHH  = 20;    // channel height from bottom — left higher than right

        // Helper: fill a rect with a cell type
        #define FILL(X,Y,W,H,C) do { \
            for (int _y=(Y);_y<(Y)+(H);_y++) \
                for (int _x=(X);_x<(X)+(W);_x++) \
                    if (_x>=0&&_x<WORLD_W&&_y>=0&&_y<WORLD_H) \
                        world[_y*WORLD_W+_x]=(C); \
        } while(0)

        int lx  = BX;                        // left basin interior x
        int div = BX + WALL + BW;            // divider x
        int rx  = div + DIV;                 // right basin interior x
        int bot = BY + BH;                   // interior bottom y (= dirtStart)

        // Outer walls (left, right, top, bottom)
        FILL(lx - WALL,     BY - WALL, WALL, BH + WALL*2, CELL_STONE); // left wall
        FILL(rx + BW,       BY - WALL, WALL, BH + WALL*2, CELL_STONE); // right wall
        FILL(lx - WALL,     BY - WALL, rx + BW + WALL - (lx-WALL), WALL, CELL_STONE); // top
        FILL(lx - WALL,     bot,       rx + BW + WALL - (lx-WALL), WALL, CELL_STONE); // bottom

        // Divider — full stone first, then carve the channel at the base
        FILL(div, BY - WALL, DIV, BH + WALL*2, CELL_STONE);
        // Channel: left basin connects lower than right to show pressure differential
        // Left channel mouth is CHH px from floor; right mouth is CHH-6 px from floor
        int ch_y = bot - CHH;
        FILL(div, ch_y, DIV, CHH, CELL_AIR);   // carve channel through divider

        // Clear basin interiors (overwrite any dirt from floor gen)
        FILL(lx, BY, BW, BH, CELL_AIR);
        FILL(rx, BY, BW, BH, CELL_AIR);

        // Fill left basin with water to the brim
        FILL(lx, BY, BW, BH, CELL_WATER);

        #undef FILL
    }

    // ── Ceiling: rock base + sticky dirt stalactites ──────────────────────
    // For each column, compute:
    //   rock_depth  — how many rows of stone from y=0 downward
    //   dirt_depth  — additional sticky dirt hanging below the rock
    // Both driven by fbm + spiky waves so the profile is jagged and organic.
    for (int x = 0; x < WORLD_W; x++) {
        float xf = (float)x;

        // Rock layer: fbm base (3–10px) + a slow triangle wave
        float rock_n = fbm(xf * 0.04f, 42);               // 0..1 slow roll
        float rock_w = triwave(xf, 60.0f) * 0.4f           // period-60 tri
                     + triwave(xf, 23.0f) * 0.25f;         // period-23 tri
        float rock_depth_f = 3.0f + (rock_n + rock_w) * 9.0f;
        int rock_depth = (int)rock_depth_f;
        if (rock_depth < 2) rock_depth = 2;
        if (rock_depth > 14) rock_depth = 14;

        for (int y = 0; y < rock_depth; y++)
            world[y * WORLD_W + x] = CELL_STONE;

        // Dirt stalactites: hang below rock edge
        // Spike wave for sharp pointy formations, fbm for variety
        float dirt_n  = fbm(xf * 0.07f, 137);
        float dirt_sp = spike(xf, 18.0f) * 0.6f            // sharp period-18 spikes
                      + spike(xf, 7.0f)  * 0.3f            // finer period-7 spikes
                      + dirt_n           * 0.35f;           // fbm roughness
        int dirt_depth = (int)(dirt_sp * 14.0f);
        if (dirt_depth < 0) dirt_depth = 0;
        if (dirt_depth > 18) dirt_depth = 18;

        for (int y = rock_depth; y < rock_depth + dirt_depth; y++) {
            if (y >= WORLD_H) break;
            // Only place where it's currently air (don't overwrite platforms etc.)
            if (CELL_TYPE(world[y * WORLD_W + x]) == CELL_AIR)
                world[y * WORLD_W + x] = CELL_DIRT | FLAG_STICKY;
        }
    }

    Image     worldImg = GenImageColor(WORLD_W, WORLD_H, BLACK);
    Texture2D worldTex = LoadTextureFromImage(worldImg);
    SetTextureFilter(worldTex, TEXTURE_FILTER_POINT);

    // ── Player state ──────────────────────────────────────────────────────
    float cx = 30.0f;
    float cy = (float)(dirtStart - CHAR_H);
    float cvx = 0.0f, cvy = 0.0f;
    int grounded           = 0;
    int coyote_timer       = 0;   // counts down after leaving ground
    int facing             = 1;
    int anim_frame         = 0;
    int anim_timer         = 0;
    int fall_through_timer = 0;
    int inv_dirt           = 0;

    // ── Rover state ───────────────────────────────────────────────────────
    float rx = 80.0f;
    float ry = (float)(dirtStart - ROVER_H);
    float rvx = 0.0f, rvy = 0.0f;
    int rover_grounded  = 0;
    int rover_facing    = 1;
    int in_rover        = 0;   // 1 = player is driving
    int rover_handbrake = 1;   // 1 = parked, won't roll; P to toggle

    // ── Ballistic arm state ───────────────────────────────────────────────
    float arm_angle   = 90.0f;  // absolute: 0=right, 90=up, 180=left
    float arm_charge  = 0.5f;   // 0..1 power charge
    int   ammo_type   = AMMO_SOIL_BALL;

    // ── Projectile ────────────────────────────────────────────────────────
    int   proj_active = 0;
    int   proj_ammo   = AMMO_SOIL_BALL;  // ammo type locked at fire time
    float proj_x = 0, proj_y = 0;
    float proj_vx = 0, proj_vy = 0;

    // ── Selection & input state ───────────────────────────────────────────
    int sel_wx     = -1, sel_wy = -1;
    int last_mx    = -1, last_my = -1;
    int input_mode = 0;
    int show_debug = 0;

    double dig_timer = 0.0;
    int frame = 0;

    while (!WindowShouldClose())
    {
        // ── Window / scale ─────────────────────────────────────────────────
        if (IsKeyPressed(KEY_F11))    ToggleBorderlessWindowed();
        if (IsKeyPressed(KEY_ESCAPE)) break;
        if (IsKeyPressed(KEY_GRAVE))  show_debug ^= 1;

        int screenW = GetScreenWidth(),  screenH = GetScreenHeight();
        int scaleX  = screenW / WORLD_W, scaleY  = screenH / WORLD_H;
        int scale   = (scaleX < scaleY) ? scaleX : scaleY;
        if (scale < 1) scale = 1;
        int scaledW = WORLD_W * scale,   scaledH = WORLD_H * scale;
        int offsetX = (screenW - scaledW) / 2;
        int offsetY = (screenH - scaledH) / 2;

        // ── Input ──────────────────────────────────────────────────────────
        // When in rover, arrow keys control the arm. A/D drive, S brake.
        // On foot, arrows and WASD both move the player.
        int shift = IsKeyDown(KEY_LEFT_SHIFT) || IsKeyDown(KEY_RIGHT_SHIFT);
        int ctrl  = IsKeyDown(KEY_LEFT_CONTROL) || IsKeyDown(KEY_RIGHT_CONTROL);

        int move_left  = IsKeyDown(KEY_A) || (!in_rover && IsKeyDown(KEY_LEFT));
        int move_right = IsKeyDown(KEY_D) || (!in_rover && IsKeyDown(KEY_RIGHT));
        int do_jump    = !in_rover && (IsKeyPressed(KEY_W) || IsKeyPressed(KEY_UP)
                                    || IsKeyPressed(KEY_SPACE));
        int do_fall    = !in_rover && (IsKeyDown(KEY_S) || IsKeyDown(KEY_DOWN));
        int do_vehicle   = IsKeyPressed(KEY_F);
        int do_handbrake = IsKeyPressed(KEY_P);
        if (do_handbrake && in_rover) rover_handbrake ^= 1;

        // Arm controls — arrow keys, modifier-stepped or continuous
        // Left/Right = angle,  Up/Down = power,  Space = fire
        // Unmodified held: continuous.  Shift: ±1 per press.  Ctrl: ±10 per press.
        int do_fire = in_rover && !proj_active &&
                      (IsKeyPressed(KEY_SPACE) ||
                       IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_TRIGGER_2));

        // Cycle ammo — Tab or Q
        if (in_rover && (IsKeyPressed(KEY_TAB) || IsKeyPressed(KEY_Q)))
            ammo_type = (ammo_type + 1) % AMMO_COUNT;

        float angle_delta = 0.0f, power_delta = 0.0f;
        if (in_rover) {
            if (shift || ctrl) {
                // Stepped (on press only)
                float step = ctrl ? 10.0f : 1.0f;
                if (IsKeyPressed(KEY_LEFT))  angle_delta += step;
                if (IsKeyPressed(KEY_RIGHT)) angle_delta -= step;
                float pstep = ctrl ? 0.10f : 0.01f;
                if (IsKeyPressed(KEY_DOWN))  power_delta -= pstep;
                if (IsKeyPressed(KEY_UP))    power_delta += pstep;
            } else {
                // Continuous (held)
                if (IsKeyDown(KEY_LEFT))  angle_delta += ARM_ANGLE_SPEED;
                if (IsKeyDown(KEY_RIGHT)) angle_delta -= ARM_ANGLE_SPEED;
                if (IsKeyDown(KEY_DOWN))  power_delta -= ARM_CHARGE_RATE;
                if (IsKeyDown(KEY_UP))    power_delta += ARM_CHARGE_RATE;
            }
            // Rover brake — S key only (arrow keys busy)
            do_fall = IsKeyDown(KEY_S);
        }

        if (IsGamepadAvailable(0)) {
            float ax  = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_X);
            float ay  = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_Y);
            float rx2 = GetGamepadAxisMovement(0, GAMEPAD_AXIS_RIGHT_X);
            float ry2 = GetGamepadAxisMovement(0, GAMEPAD_AXIS_RIGHT_Y);

            // Left stick drives in both modes
            if (ax < -0.3f) move_left  = 1;
            if (ax >  0.3f) move_right = 1;

            if (!in_rover) {
                if (ay >  0.5f) do_fall = 1;
                if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN)) do_jump = 1;
            } else {
                // Right stick X → turret angle, right stick Y → power (up=more)
                if (fabsf(rx2) > 0.15f) angle_delta += rx2 * ARM_ANGLE_SPEED * 2.5f;
                if (fabsf(ry2) > 0.15f) power_delta -= ry2 * ARM_CHARGE_RATE * 2.5f;
                // A button (face down) fires
                if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN) && !proj_active)
                    do_fire = 1;
            }
            if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_LEFT)) do_vehicle = 1;
        }

        Vector2 mouse = GetMousePosition();
        int mwx = (int)((mouse.x - offsetX) / scale);
        int mwy = (int)((mouse.y - offsetY) / scale);

        // ── Dirt simulation ────────────────────────────────────────────────
        tick_dirt(world, frame & 1);

        // ── Water simulation — 3 passes for fast equalization ──────────────
        tick_water(world, frame & 1);
        tick_water(world, (frame + 1) & 1);
        tick_water(world, frame & 1);

        // ── Rover enter / exit ─────────────────────────────────────────────
        // Player centre distance to rover centre.
        float pcx_f = cx + CHAR_W  * 0.5f;
        float pcy_f = cy + CHAR_H  * 0.5f;
        float rcx_f = rx + ROVER_W * 0.5f;
        float rcy_f = ry + ROVER_H * 0.5f;
        float ddx = pcx_f - rcx_f, ddy = pcy_f - rcy_f;
        int near_rover = !in_rover && (ddx*ddx + ddy*ddy < (float)(ROVER_ENTER_R*ROVER_ENTER_R));

        if (do_vehicle) {
            if (in_rover) {
                // Exit: spawn player at the side rover is facing, else other side.
                float ex_r = rx + ROVER_W + 1;
                float ex_l = rx - CHAR_W  - 1;
                float ey   = ry + ROVER_H - CHAR_H;
                float exit_x = (rover_facing > 0) ? ex_r : ex_l;
                float alt_x  = (rover_facing > 0) ? ex_l : ex_r;
                if (box_solid_ex(world, exit_x, ey, CHAR_W, CHAR_H, 0))
                    exit_x = alt_x;
                cx     = exit_x;
                cy     = ey;
                cvx    = 0; cvy = 0;
                facing = rover_facing;
                in_rover = 0;
            } else if (near_rover) {
                in_rover     = 1;
                rover_facing = facing;
            }
        }

        // ── Rover physics ──────────────────────────────────────────────────
        // Rover always simulates — momentum and gravity persist when unoccupied.
        rover_grounded = box_solid_ex(world, rx, ry + 1.0f, ROVER_W, ROVER_H, 0);

        // ── Gravity / vertical ─────────────────────────────────────────────
        if (!rover_grounded) {
            rvy += GRAVITY * 1.4f;   // heavier than player
            if (rvy > ROVER_MAX_FALL) rvy = ROVER_MAX_FALL;
        }

        // ── Slope sensing (for tilt and rolling) ───────────────────────────
        // Sample ground height 4px inside each wheel. Positive slope = right lower.
        int left_gx  = (int)rx + 4;
        int right_gx = (int)rx + ROVER_W - 5;
        int scan_base = (int)ry + ROVER_H + 1;
        int left_gy   = ground_y_at(world, left_gx,  scan_base);
        int right_gy  = ground_y_at(world, right_gx, scan_base);
        int slope_raw = right_gy - left_gy;

        // ── Horizontal velocity ────────────────────────────────────────────
        int throttle_left  = in_rover && move_left;
        int throttle_right = in_rover && move_right;
        int braking        = in_rover && do_fall;   // S/down = brake

        if (throttle_left) {
            rvx -= ROVER_ACCEL;
            if (rvx < -ROVER_TOP_SPEED) rvx = -ROVER_TOP_SPEED;
            rover_facing = -1;
            rover_handbrake = 0;   // touching throttle releases handbrake
        } else if (throttle_right) {
            rvx += ROVER_ACCEL;
            if (rvx >  ROVER_TOP_SPEED) rvx =  ROVER_TOP_SPEED;
            rover_facing = 1;
            rover_handbrake = 0;
        }

        // Update facing from momentum when unoccupied or coasting
        if (!throttle_left && !throttle_right && rvx != 0.0f)
            rover_facing = (rvx < 0.0f) ? -1 : 1;

        // Slope rolling — only when grounded and handbrake is off
        if (rover_grounded && !rover_handbrake) {
            rvx += slope_raw * ROVER_SLOPE_FORCE;
            if (rvx >  ROVER_TOP_SPEED * 1.5f) rvx =  ROVER_TOP_SPEED * 1.5f;
            if (rvx < -ROVER_TOP_SPEED * 1.5f) rvx = -ROVER_TOP_SPEED * 1.5f;
        }

        // Drag — applied every frame
        if (rover_grounded) {
            if (braking) {
                rvx *= ROVER_BRAKE_DRAG;
            } else if (rover_handbrake) {
                rvx *= ROVER_PARK_DRAG;
            } else {
                rvx *= ROVER_ROLL_DRAG;
            }
            if (rvx > -0.01f && rvx < 0.01f) rvx = 0.0f;  // dead-stop threshold
        }

        // ── Apply horizontal movement ──────────────────────────────────────
        if (rvx != 0.0f) {
            float new_x = rx + rvx;
            if (new_x < 0)                  { new_x = 0;                           rvx = 0.0f; }
            if (new_x + ROVER_W > WORLD_W)  { new_x = (float)(WORLD_W - ROVER_W); rvx = 0.0f; }

            if (!box_solid_ex(world, new_x, ry, ROVER_W, ROVER_H, 0)) {
                rx = new_x;
            } else if (rover_grounded) {
                // Step up — limited to ROVER_STEP_UP so sheer walls block us
                int stepped = 0;
                for (int s = 1; s <= ROVER_STEP_UP; s++) {
                    if (!box_solid_ex(world, new_x, ry - (float)s, ROVER_W, ROVER_H, 0)) {
                        rx = new_x;
                        ry -= (float)s;
                        stepped = 1;
                        break;
                    }
                }
                // Wall hit — kill horizontal momentum
                if (!stepped) rvx = 0.0f;
            } else {
                // Airborne wall hit
                rvx = 0.0f;
            }
        }

        // ── Rover edge erosion ─────────────────────────────────────────────
        // When the rover rolls over the edge of a sticky dirt block, the
        // outermost cell in the direction of travel gets unstuck — the weight
        // of the rover breaks the adhesion at exposed edges.
        if (rover_grounded && rvx != 0.0f) {
            int dir    = (rvx > 0.0f) ? 1 : -1;
            int foot_y = (int)ry + ROVER_H;   // row just below rover
            if (foot_y >= 0 && foot_y < WORLD_H) {
                for (int col = 0; col < ROVER_W; col++) {
                    int wx = (int)rx + col;
                    if (wx < 0 || wx >= WORLD_W) continue;
                    // Is this cell sticky dirt?
                    if (world[foot_y * WORLD_W + wx] != (CELL_DIRT | FLAG_STICKY)) continue;
                    // Is the neighbour in the direction of travel air? (exposed edge)
                    int nx = wx + dir;
                    if (nx < 0 || nx >= WORLD_W) continue;
                    if (CELL_TYPE(world[foot_y * WORLD_W + nx]) == CELL_AIR)
                        world[foot_y * WORLD_W + wx] &= ~FLAG_STICKY;
                }
            }
        }

        // ── Ground snap ────────────────────────────────────────────────────
        // Pull rover down to hug terrain after step-up or slope descent.
        // Runs unconditionally so a stale rover_grounded value can't cause floating.
        {
            int snapped = 0;
            for (int snap = 0; snap < ROVER_STEP_UP + 2; snap++) {
                if (!box_solid_ex(world, rx, ry + 1.0f, ROVER_W, ROVER_H, 0)) {
                    ry += 1.0f; snapped = 1;
                } else break;
            }
            rover_grounded = box_solid_ex(world, rx, ry + 1.0f, ROVER_W, ROVER_H, 0);
            if (snapped && rover_grounded) rvy = 0.0f;
        }

        // ── Apply vertical movement ────────────────────────────────────────
        if (rvy != 0.0f) {
            float new_y = ry + rvy;
            if (!box_solid_ex(world, rx, new_y, ROVER_W, ROVER_H, 0)) {
                ry = new_y;
            } else {
                if (rvy > 0.0f) {
                    // Snap to ground pixel
                    float fy = (float)(int)ry;
                    while (!box_solid_ex(world, rx, fy + 1.0f, ROVER_W, ROVER_H, 0))
                        fy += 1.0f;
                    ry             = fy;
                    rover_grounded = 1;
                    // Bounce — flip and attenuate vertical velocity
                    rvy = -rvy * ROVER_BOUNCE;
                    if (rvy > -0.5f) rvy = 0.0f;   // kill micro-bounces
                    // Landing transfers a little force into horizontal roll
                    if (!rover_handbrake)
                        rvx += slope_raw * ROVER_SLOPE_FORCE * 4.0f;
                }
                if (rvy < 0.0f) rvy = 0.0f;   // ceiling hit
            }
        }

        if (ry < 0)                  { ry = 0;                            rvy = 0; }
        if (ry + ROVER_H > WORLD_H)  { ry = (float)(WORLD_H - ROVER_H);  rvy = 0; rover_grounded = 1; }

        // ── Player physics (on foot only) ──────────────────────────────────
        if (!in_rover) {
            if (fall_through_timer > 0) fall_through_timer--;
            int include_plat = (fall_through_timer <= 0);

            grounded = box_solid_ex(world, cx, cy + 1.0f, CHAR_W, CHAR_H, include_plat);

            if (do_fall && grounded && include_plat) {
                int fy = (int)(cy + CHAR_H);
                int on_plat = 0;
                for (int fx = (int)cx; fx <= (int)cx + CHAR_W - 1 && !on_plat; fx++)
                    if (fx >= 0 && fx < WORLD_W && fy >= 0 && fy < WORLD_H)
                        if (CELL_TYPE(world[fy * WORLD_W + fx]) == CELL_PLATFORM) on_plat = 1;
                if (on_plat) { fall_through_timer = 15; include_plat = 0; grounded = 0; }
            }

            // Coyote time: refresh while grounded, count down while airborne
            if (grounded) {
                coyote_timer = COYOTE_FRAMES;
            } else if (coyote_timer > 0) {
                coyote_timer--;
            }

            if (!grounded) {
                cvy += GRAVITY;
                if (cvy > MAX_FALL) cvy = MAX_FALL;
            } else if (cvy > 0.0f) { cvy = 0.0f; }

            if (do_jump && coyote_timer > 0) { cvy = JUMP_VEL; grounded = 0; coyote_timer = 0; }

            cvx = 0.0f;
            if (move_left)  { cvx = -WALK_SPEED; facing = -1; }
            if (move_right) { cvx =  WALK_SPEED; facing =  1; }

            if (cvx != 0.0f) {
                float new_x = cx + cvx;
                if (new_x < 0)                 new_x = 0;
                if (new_x + CHAR_W > WORLD_W)  new_x = (float)(WORLD_W - CHAR_W);
                if (!box_solid_ex(world, new_x, cy, CHAR_W, CHAR_H, 0)) {
                    cx = new_x;
                } else if (grounded) {
                    for (int s = 1; s <= MAX_STEP_UP; s++) {
                        if (!box_solid_ex(world, new_x, cy - (float)s, CHAR_W, CHAR_H, 0)) {
                            cx = new_x; cy -= (float)s; break;
                        }
                    }
                }
            }

            if (cvy != 0.0f) {
                float new_y      = cy + cvy;
                int   plat_solid = (cvy > 0.0f) && include_plat;
                if (!box_solid_ex(world, cx, new_y, CHAR_W, CHAR_H, plat_solid)) {
                    cy = new_y;
                } else {
                    if (cvy > 0.0f) {
                        float fy = (float)(int)cy;
                        while (!box_solid_ex(world, cx, fy + 1.0f, CHAR_W, CHAR_H, plat_solid))
                            fy += 1.0f;
                        cy = fy; grounded = 1;
                    }
                    cvy = 0.0f;
                }
            }

            if (cy < 0)                { cy = 0;                          cvy = 0; }
            if (cy + CHAR_H > WORLD_H) { cy = (float)(WORLD_H - CHAR_H); cvy = 0; grounded = 1; }

            if (move_left || move_right) {
                if (++anim_timer >= 8) { anim_timer = 0; anim_frame ^= 1; }
            } else { anim_frame = 0; anim_timer = 0; }
        }

        // ── Ballistic arm ──────────────────────────────────────────────────
        if (in_rover) {
            // Apply angle delta from input
            arm_angle += angle_delta;

            // Clamp: parked = full 5..175 arc; driving = facing half only
            if (!rover_handbrake) {
                if (rover_facing > 0) {
                    if (arm_angle > 88.0f)         arm_angle = 88.0f;
                    if (arm_angle < ARM_ANGLE_MIN)  arm_angle = ARM_ANGLE_MIN;
                } else {
                    if (arm_angle < 92.0f)         arm_angle = 92.0f;
                    if (arm_angle > ARM_ANGLE_MAX)  arm_angle = ARM_ANGLE_MAX;
                }
            } else {
                if (arm_angle > ARM_ANGLE_MAX) arm_angle = ARM_ANGLE_MAX;
                if (arm_angle < ARM_ANGLE_MIN) arm_angle = ARM_ANGLE_MIN;
            }

            // Apply power delta from input
            arm_charge += power_delta;
            if (arm_charge > 1.0f) arm_charge = 1.0f;
            if (arm_charge < 0.0f) arm_charge = 0.0f;

            // Fire — absolute angle, no rover_facing flip
            if (do_fire) {
                float rad   = arm_angle * (float)M_PI / 180.0f;
                float speed = ARM_POWER_MIN + arm_charge * (ARM_POWER_MAX - ARM_POWER_MIN);
                float pivot_x = rx + ROVER_W * 0.5f;
                float pivot_y = ry + 2.0f;
                proj_x    = pivot_x + cosf(rad) * ARM_LEN;
                proj_y    = pivot_y - sinf(rad) * ARM_LEN;
                proj_vx   = cosf(rad) * speed;
                proj_vy   = -sinf(rad) * speed;
                proj_ammo = ammo_type;  // lock ammo type for this shot
                proj_active = 1;
            }
        }

        // ── Projectile physics ─────────────────────────────────────────────
        if (proj_active) {
            proj_vy += PROJ_GRAVITY;
            proj_x  += proj_vx;
            proj_y  += proj_vy;

            int px = (int)proj_x, py = (int)proj_y;

            if (px < 0 || px >= WORLD_W || py < 0 || py >= WORLD_H) {
                proj_active = 0;
            } else {
                uint8_t hit = CELL_TYPE(world[py * WORLD_W + px]);
                if (hit == CELL_STONE || hit == CELL_DIRT) {
                    // Back up 1 cell opposite velocity so the impact circle
                    // is centred in open air, not inside the solid surface.
                    int icx = px - (proj_vx > 0.5f ? 1 : proj_vx < -0.5f ? -1 : 0);
                    int icy = py - (proj_vy > 0.5f ? 1 : proj_vy < -0.5f ? -1 : 0);
                    int r = DEPOSIT_R_MIN + (int)(arm_charge * (DEPOSIT_R_MAX - DEPOSIT_R_MIN));
                    switch (proj_ammo) {
                        case AMMO_SOIL_BALL:
                            impact_soil_ball(world, icx, icy, r);
                            break;
                        case AMMO_STICKY_SOIL:
                            impact_sticky_soil(world, icx, icy, r);
                            break;
                        case AMMO_LIQUID_SOIL:
                            impact_liquid_soil(world, icx, icy, r);
                            break;
                    }
                    proj_active = 0;
                }
            }
        }

        // ── Cell selection & inventory (on foot only) ──────────────────────
        sel_wx = -1; sel_wy = -1;

        if (!in_rover) {
            float pcx = cx + CHAR_W * 0.5f;
            float pcy = cy + CHAR_H * 0.5f;
            int   pr2  = PICKUP_RADIUS * PICKUP_RADIUS;

            int mouse_moved = (mwx != last_mx || mwy != last_my);
            last_mx = mwx; last_my = mwy;

            if (IsGamepadAvailable(0)) {
                float ax = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_X);
                float ay = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_Y);
                if (ax < -0.3f || ax > 0.3f || ay < -0.3f || ay > 0.3f) input_mode = 1;
                if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN)) input_mode = 1;
            }
            if (mouse_moved || IsMouseButtonPressed(MOUSE_BUTTON_LEFT) ||
                    IsMouseButtonPressed(MOUSE_BUTTON_RIGHT) ||
                    move_left || move_right || do_jump || GetKeyPressed() != 0)
                input_mode = 0;

            if (input_mode == 0)
                facing = (mwx >= (int)pcx) ? 1 : -1;

            if (input_mode == 0) {
                if (mwx >= 0 && mwx < WORLD_W && mwy >= 0 && mwy < WORLD_H) {
                    float dx = mwx - pcx, dy = mwy - pcy;
                    if (dx*dx + dy*dy <= (float)pr2
                            && CELL_TYPE(world[mwy * WORLD_W + mwx]) != CELL_AIR)
                        { sel_wx = mwx; sel_wy = mwy; }
                }
            } else {
                float best = (float)(pr2 + 1);
                int bx0 = (int)pcx - PICKUP_RADIUS, bx1 = (int)pcx + PICKUP_RADIUS;
                int by0 = (int)pcy - PICKUP_RADIUS, by1 = (int)pcy + PICKUP_RADIUS;
                for (int wy = by0; wy <= by1; wy++) {
                    for (int wx = bx0; wx <= bx1; wx++) {
                        if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
                        float dx = wx - pcx, dy = wy - pcy;
                        if (dx * facing < 0.0f) continue;
                        float d2 = dx*dx + dy*dy;
                        if (d2 > (float)pr2 || d2 >= best) continue;
                        if (CELL_TYPE(world[wy * WORLD_W + wx]) == CELL_DIRT)
                            { best = d2; sel_wx = wx; sel_wy = wy; }
                    }
                }
            }

            int dig_held = IsMouseButtonDown(MOUSE_BUTTON_LEFT) || IsKeyDown(KEY_E);
            int dig_just = IsMouseButtonPressed(MOUSE_BUTTON_LEFT) || IsKeyPressed(KEY_E);
            if (dig_held) { dig_timer -= GetFrameTime(); } else { dig_timer = 0.0; }

            if (dig_just || (dig_held && dig_timer <= 0.0)) {
                if (dig_just || dig_timer <= 0.0) dig_timer = DIG_REPEAT_MS / 1000.0;
                if (sel_wx >= 0 && inv_dirt < INV_MAX
                        && CELL_TYPE(world[sel_wy * WORLD_W + sel_wx]) == CELL_DIRT) {
                    world[sel_wy * WORLD_W + sel_wx] = CELL_AIR;
                    inv_dirt++;
                    unstick(world, sel_wx,     sel_wy - 1);
                    unstick(world, sel_wx - 1, sel_wy - 1);
                    unstick(world, sel_wx + 1, sel_wy - 1);
                }
            }

            if (IsMouseButtonPressed(MOUSE_BUTTON_RIGHT)) {
                if (mwx >= 0 && mwx < WORLD_W && mwy >= 0 && mwy < WORLD_H) {
                    if (CELL_TYPE(world[mwy * WORLD_W + mwx]) == CELL_AIR && inv_dirt > 0) {
                        world[mwy * WORLD_W + mwx] = CELL_DIRT;
                        inv_dirt--;
                    }
                }
            }
        }

        // ── Render world to pixel buffer ───────────────────────────────────
        Color *pixels = worldImg.data;
        for (int y = 0; y < WORLD_H; y++) {
            for (int x = 0; x < WORLD_W; x++) {
                int i = y * WORLD_W + x;
                switch (CELL_TYPE(world[i])) {
                    case CELL_STONE:    pixels[i] = (Color){128, 128, 128, 255}; break;
                    case CELL_DIRT:     pixels[i] = (Color){139,  90,  43, 255}; break;
                    case CELL_PLATFORM: pixels[i] = (Color){165, 105,  50, 255}; break;
                    case CELL_WATER: {
                        // Surface highlight: water cell with air directly above
                        int surface = (y == 0) || (CELL_TYPE(world[(y-1)*WORLD_W+x]) != CELL_WATER);
                        pixels[i] = surface
                            ? (Color){ 90, 160, 230, 255}   // bright surface
                            : (Color){ 30,  80, 160, 255};  // deep water
                        break;
                    }
                    default:            pixels[i] = (Color){255, 255, 255,   0}; break;
                }
            }
        }

        // ── Draw rover (sheared to surface normal) ────────────────────────
        {
            // Sample ground under each wheel contact column
            int left_wx  = (int)rx + 3;
            int right_wx = (int)rx + ROVER_W - 4;
            int scan_y   = (int)ry + ROVER_H;
            int left_g   = ground_y_at(world, left_wx,  scan_y);
            int right_g  = ground_y_at(world, right_wx, scan_y);
            int slope    = right_g - left_g;
            // Clamp slope so sprite doesn't shear wildly on steep drops
            if (slope >  7) slope =  7;
            if (slope < -7) slope = -7;
            draw_rover_sheared(pixels, (int)rx, (int)ry, rover_facing, slope);
        }

        // ── Draw arm ─────────────────────────────────────────────────────
        if (in_rover) {
            float rad   = arm_angle * (float)M_PI / 180.0f;
            float piv_x = rx + ROVER_W * 0.5f;
            float piv_y = ry + 2.0f;
            for (int i = 2; i <= ARM_LEN; i++) {
                int ax = (int)(piv_x + cosf(rad) * i);
                int ay = (int)(piv_y - sinf(rad) * i);
                if (ax < 0 || ax >= WORLD_W || ay < 0 || ay >= WORLD_H) continue;
                pixels[ay * WORLD_W + ax] = (Color){200, 200, 80, 255};
            }
        }

        // ── Draw projectile ───────────────────────────────────────────────
        if (proj_active) {
            Color pcols[AMMO_COUNT] = {
                {139,  90,  43, 255},   // SOIL_BALL    — dirt brown
                { 80, 140,  60, 255},   // STICKY_SOIL  — mossy green
                {180, 130,  40, 255},   // LIQUID_SOIL  — muddy gold
            };
            Color pcol = pcols[proj_ammo];
            int px = (int)proj_x, py = (int)proj_y;
            for (int dy = -1; dy <= 1; dy++)
                for (int dx = -1; dx <= 1; dx++) {
                    int wx = px+dx, wy = py+dy;
                    if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
                    pixels[wy * WORLD_W + wx] = pcol;
                }
        }

        // ── Draw player (only when on foot) ───────────────────────────────
        if (!in_rover) {
            int draw_x = (int)cx, draw_y = (int)cy;
            for (int row = 0; row < CHAR_H; row++) {
                for (int col = 0; col < CHAR_W; col++) {
                    int     src = (facing < 0) ? (CHAR_W - 1 - col) : col;
                    uint8_t idx = SPRITE[anim_frame][row][src];
                    if (!idx) continue;
                    int wx = draw_x + col, wy = draw_y + row;
                    if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
                    pixels[wy * WORLD_W + wx] = CHAR_PAL[idx];
                }
            }
        }

        UpdateTexture(worldTex, pixels);

        // ── Screen composite ──────────────────────────────────────────────
        BeginDrawing();
            ClearBackground(BLACK);
            DrawTexturePro(
                worldTex,
                (Rectangle){0, 0, WORLD_W, WORLD_H},
                (Rectangle){(float)offsetX, (float)offsetY, (float)scaledW, (float)scaledH},
                (Vector2){0, 0}, 0.0f, WHITE
            );

            // Debug overlays
            if (show_debug && !in_rover) {
                float pcx = cx + CHAR_W * 0.5f, pcy = cy + CHAR_H * 0.5f;
                DrawCircleLines(
                    offsetX + (int)(pcx * scale),
                    offsetY + (int)(pcy * scale),
                    PICKUP_RADIUS * scale,
                    (Color){255, 255, 100, 80}
                );
                DrawText(TextFormat("Player:(%.0f,%.0f) vel:(%.1f,%.1f) %s%s",
                    cx, cy, cvx, cvy,
                    grounded ? "GND" : "AIR",
                    fall_through_timer > 0 ? " FALLTHRU" : ""),
                    8, 28, 16, YELLOW);
            }
            if (show_debug) {
                DrawText(TextFormat("Rover:(%.0f,%.0f) vel:(%.1f,%.1f) %s  Frame:%d",
                    rx, ry, rvx, rvy,
                    rover_grounded ? "GND" : "AIR", frame),
                    8, 48, 16, (Color){180, 255, 160, 255});
            }

            // Cell selection outline
            if (sel_wx >= 0) {
                Color outline = (input_mode == 0)
                    ? (Color){255, 255,  50, 230}
                    : (Color){100, 220, 255, 230};
                DrawRectangleLinesEx(
                    (Rectangle){
                        (float)(offsetX + sel_wx * scale),
                        (float)(offsetY + sel_wy * scale),
                        (float)scale, (float)scale
                    }, 1.0f, outline
                );
            }

            // Trajectory preview arc (screen space dots)
            if (in_rover && !proj_active) {
                float rad   = arm_angle * (float)M_PI / 180.0f;
                float speed = ARM_POWER_MIN + arm_charge * (ARM_POWER_MAX - ARM_POWER_MIN);
                float piv_x = rx + ROVER_W * 0.5f;
                float piv_y = ry + 2.0f;
                float sx  = piv_x + cosf(rad) * ARM_LEN;
                float sy  = piv_y - sinf(rad) * ARM_LEN;
                float svx = cosf(rad) * speed;
                float svy = -sinf(rad) * speed;
                for (int step = 0; step < 120; step++) {
                    svy += PROJ_GRAVITY;
                    sx  += svx; sy += svy;
                    if (sx < 0 || sx >= WORLD_W || sy < 0 || sy >= WORLD_H) break;
                    if (CELL_TYPE(world[(int)sy * WORLD_W + (int)sx]) != CELL_AIR) break;
                    if (step % 4 == 0) {
                        int spx = offsetX + (int)(sx * scale);
                        int spy = offsetY + (int)(sy * scale);
                        DrawRectangle(spx, spy, 2, 2, (Color){255, 255, 100, 120});
                    }
                }
            }

            // Power bar (screen space, right of centre)
            if (in_rover) {
                int bar_x = offsetX + scaledW/2 + 60;
                int bar_y = offsetY + 8;
                int bar_h = 40;
                DrawRectangle(bar_x, bar_y, 8, bar_h, (Color){60, 60, 60, 200});
                int filled = (int)(arm_charge * bar_h);
                Color pcol = arm_charge < 0.5f
                    ? (Color){80, 200, 80, 255}
                    : arm_charge < 0.85f
                        ? (Color){220, 200, 50, 255}
                        : (Color){255, 80, 50, 255};
                DrawRectangle(bar_x, bar_y + bar_h - filled, 8, filled, pcol);
                DrawRectangleLines(bar_x, bar_y, 8, bar_h, WHITE);
                DrawText(TextFormat("%.0f°", arm_angle),
                    bar_x - 24, bar_y + bar_h + 2, 14, YELLOW);
            }

            // Contextual prompt
            if (in_rover) {
                DrawText("F: Exit rover", offsetX + scaledW/2 - 50, offsetY + 8, 16, WHITE);
                DrawText(rover_handbrake ? "BRAKE" : "ROLLING",
                    offsetX + scaledW/2 - 28, offsetY + 28, 16,
                    rover_handbrake ? (Color){255,80,80,255} : (Color){80,255,120,255});
                // Ammo name — colour-coded per type
                Color ammo_cols[AMMO_COUNT] = {
                    {200, 140,  60, 255},
                    { 80, 200,  80, 255},
                    {180, 160,  40, 255},
                };
                DrawText(TextFormat("< %s >  [Tab]", AMMO_NAMES[ammo_type]),
                    offsetX + 8, offsetY + 28, 16, ammo_cols[ammo_type]);
            } else if (near_rover) {
                DrawText("F: Enter rover", offsetX + scaledW/2 - 56, offsetY + 8, 16, WHITE);
            }

            // HUD
            DrawText(TextFormat("Dirt:%d/%d", inv_dirt, INV_MAX), 8, 8, 16, WHITE);
            DrawText(
                in_rover
                    ? "A/D=Drive  S=Brake  P=Handbrake  W/Dn=Aim  ]/[=Power  Space=Fire  F=Exit"
                    : "WASD=Move  Space=Jump  S=FallThru  LMB/E=Dig  RMB=Place  F=Rover  `=Debug  ESC=Quit",
                8, scaledH + offsetY + 30, 16, GRAY);
        EndDrawing();

        frame++;
    }

    UnloadTexture(worldTex);
    UnloadImage(worldImg);
    CloseWindow();
    return 0;
}