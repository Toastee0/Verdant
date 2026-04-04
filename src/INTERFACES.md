# Verdant — Module Interface Reference

**Keep this file up to date.** When you add, remove, or change any function signature,
struct field, or constant exported from a module, update the entry here immediately.
This is the contract between files — breaking an interface without updating this doc
will cause confusion across sessions.

---

## Dependency graph

```
defs.h        ← everything
sprites.h     ← defs.h
noise         ← defs.h
world         ← defs.h
sim/dirt      ← defs.h, world.h
sim/water     ← defs.h
sim/impact    ← defs.h, sim/water.h
terrain       ← defs.h, noise.h, sim/water.h
input         ← defs.h, raylib
player        ← defs.h, world.h
rover         ← defs.h, world.h, sprites.h
rover_arm     ← defs.h, rover.h, sim/impact.h
render        ← defs.h, world.h, sprites.h, player.h, rover.h, rover_arm.h
main          ← everything
```

---

## defs.h — constants and macros

No functions. Exports only `#define` constants and the `CELL_TYPE` macro.

**World constants**
```c
WORLD_W = 480          // world width in pixels
WORLD_H = 270          // world height in pixels
CELL_AIR     = 0
CELL_STONE   = 1
CELL_DIRT    = 2
CELL_PLATFORM = 3      // one-way: solid from above only
// NOTE: CELL_WATER removed — water is now a parallel uint8_t water[] amount array
FLAG_STICKY  = 0x80    // bit 7 of cell byte: dirt won't fall
CELL_TYPE(c) = (c) & 0x7F   // mask flags to get type
```

**Water amount thresholds** (for the parallel `water[]` array)
```c
WATER_DRY      = 0     // completely dry
WATER_DAMP     = 8     // below this: render as dry air
WATER_SHALLOW  = 64    // below this: surface/shallow color
WATER_FULL     = 200   // at or above: solid saturated water color
```

**Player constants**
```c
CHAR_W=4, CHAR_H=8
GRAVITY=0.35f, JUMP_VEL=-5.5f, WALK_SPEED=1.5f
MAX_FALL=10.0f, MAX_STEP_UP=3, COYOTE_FRAMES=8
PICKUP_RADIUS=18, INV_MAX=99, DIG_REPEAT_MS=80
```

**Rover constants**
```c
ROVER_W=24, ROVER_H=16
ROVER_MAX_FALL=18.0f, ROVER_STEP_UP=4, ROVER_ENTER_R=24
ROVER_ACCEL=0.18f, ROVER_TOP_SPEED=3.8f
ROVER_BRAKE_DRAG=0.72f, ROVER_ROLL_DRAG=0.97f, ROVER_PARK_DRAG=0.80f
ROVER_SLOPE_FORCE=0.06f, ROVER_BOUNCE=0.35f
```

**Ammo constants**
```c
AMMO_SOIL_BALL=0, AMMO_STICKY_SOIL=1, AMMO_LIQUID_SOIL=2, AMMO_COUNT=3
DEPOSIT_R_MIN=2, DEPOSIT_R_MAX=5
```

**Arm constants**
```c
ARM_LEN=9
ARM_ANGLE_MIN=5.0f, ARM_ANGLE_MAX=175.0f, ARM_ANGLE_SPEED=1.8f
ARM_POWER_MIN=2.5f, ARM_POWER_MAX=9.0f, ARM_CHARGE_RATE=0.016f
BLAST_RADIUS=5, PROJ_GRAVITY=0.25f
```

---

## sprites.h — sprite data (header-only)

No functions. All arrays are `static const` — safe to include from multiple TUs.

```c
static const uint8_t SPRITE[2][CHAR_H][CHAR_W]     // player walk frames (palette indices)
static const Color   CHAR_PAL[4]                    // player palette
static const uint8_t ROVER_SPRITE[ROVER_H][ROVER_W] // rover bitmap (palette indices)
static const Color   ROVER_PAL[8]                   // rover palette
```

---

## noise.h / noise.c — procedural math

Pure functions, no side effects, no world state.

```c
float hash1(int n)
    // Deterministic hash → float [0,1]

float vnoise(float x, int seed)
    // Smooth value noise via smoothstep interpolation between hashed lattice points

float fbm(float x, int seed)
    // Fractal Brownian motion: 4 octaves of vnoise, returns ~[0,1]

float triwave(float x, float p)
    // Triangle wave with period p, range [0,1]

float spike(float x, float p)
    // |sin(x)|^0.25 — spiky waveform, thin base, sharp peaks
```

---

## world.h / world.c — collision queries

Read-only queries against the world array.

```c
int ground_y_at(const uint8_t *w, int wx, int start_y)
    // Scan down from start_y; return y of first CELL_STONE or CELL_DIRT cell.
    // Returns start_y if wx out of bounds, WORLD_H if nothing found.

int box_solid_ex(const uint8_t *w, float bx, float by, int bw, int bh, int include_platform)
    // AABB collision: 1 if any cell in the box is solid, 0 if clear.
    // CELL_STONE and CELL_DIRT always solid. CELL_PLATFORM solid only if include_platform=1.
    // Out-of-bounds cells always solid.
```

---

## sim/dirt.h / dirt.c — dirt/sand simulation

```c
void tick_dirt(uint8_t *world, int bias)
    // Sand-fall: each unsticky CELL_DIRT falls straight down into air or water,
    // then diagonally. bias (0 or 1) alternates scan direction per frame.
```

## sim/water.h / water.c — water simulation + unstick

```c
void tick_water(uint8_t *world, uint8_t *water, int bias)
    // Continuous water sim using parallel water[] amount array (0..255 per CELL_AIR cell).
    // Three rules per cell, bottom-to-top:
    //   1. Gravity      — fall into cell below as much as will fit
    //   2. Equalization — halve diff with each horizontal neighbour (flat surfaces / U-tubes)
    //   3. Pressure     — fully-saturated cell under saturated cell pushes sideways
    // bias (0 or 1) alternates scan direction per frame.

void unstick(uint8_t *world, int x, int y)
    // Clear FLAG_STICKY on the CELL_DIRT at (x,y), if present.
    // Called after digging a neighbour or on explosion.
```

## sim/impact.h / impact.c — projectile impacts

All functions write to world[]. Only fill CELL_AIR — won't overwrite existing terrain.

```c
void explode(uint8_t *world, int cx, int cy, int radius)
    // Carve a circular crater: remove CELL_DIRT within radius, unstick neighbours.

void impact_soil_ball(uint8_t *world, int cx, int cy, int radius)
    // Fill a circle of radius with loose CELL_DIRT (falls immediately via tick_dirt).

void impact_sticky_soil(uint8_t *world, int cx, int cy, int radius)
    // Fill a circle of radius with CELL_DIRT | FLAG_STICKY (adheres to surfaces).

void impact_liquid_soil(uint8_t *world, int cx, int cy, int radius)
    // Deposit a tall column (width=radius+2, height=radius*3) of loose CELL_DIRT.
    // Flows and fills gaps naturally via the dirt sim.
```

---

## terrain.h / terrain.c — world generation

```c
void terrain_generate(uint8_t *world, uint8_t *water)
    // Fill world[] with the starting scene; fill water[] with initial water amounts.
    // Current scene: stone floor, sticky-dirt layer, platforms, raised ramp,
    // procedural ceiling stalactites, communicating basins water demo.
    // REPLACE THIS FILE when implementing proper worldgen.
```

---

## input.h / input.c — input abstraction

```c
typedef struct {
    int move_left, move_right;   // directional movement (held)
    int do_jump;                 // jump pressed this frame (edge-triggered)
    int do_fall;                 // on-foot: fall-through; in-rover: brake (held)
    int do_vehicle;              // F key: enter/exit rover (edge-triggered)
    int do_handbrake;            // P key: toggle handbrake (edge-triggered)
    int do_fire;                 // Space/RT: fire arm (edge-triggered, rover only)
    int cycle_ammo;              // Tab/Q: cycle ammo (edge-triggered, rover only)
    float angle_delta;           // arm angle change this frame (degrees)
    float power_delta;           // arm charge change this frame (fraction)
    int toggle_fullscreen;       // F11 (edge-triggered)
    int toggle_debug;            // backtick (edge-triggered)
    int quit;                    // Escape (edge-triggered)
    int mouse_wx, mouse_wy;      // mouse position in world coords
    int dig_held, dig_just;      // LMB/E held; LMB/E pressed this frame
    int place_just;              // RMB pressed this frame
    int input_mode;              // 0=mouse-aim, 1=gamepad-nearest
    int _last_mx, _last_my;      // internal: previous mouse pos for moved-detection
} InputState;

void input_poll(InputState *inp, int in_rover,
                int screenW, int screenH, int offsetX, int offsetY, int scale)
    // Poll all devices, fill inp for this frame.
    // in_rover controls key bindings (arrow keys = arm vs movement).
    // Screen params needed to map mouse position → world coords.
    // inp must be zero-initialised before first call; persists input_mode across frames.
```

---

## player.h / player.c — player physics

```c
typedef struct {
    float x, y;              // top-left pixel position
    float vx, vy;            // velocity (pixels/frame)
    int grounded;            // 1 = on solid ground or platform
    int coyote_timer;        // frames to still jump after leaving ground
    int facing;              // 1=right, -1=left
    int anim_frame;          // 0 or 1: walk-cycle frame
    int anim_timer;          // walk-animation frame counter (flips every 8 frames)
    int fall_through_timer;  // frames left ignoring platform collision
    int inv_dirt;            // carried dirt count (0..INV_MAX)
} PlayerState;

void player_update(PlayerState *p, const uint8_t *world,
                   int move_left, int move_right, int do_jump, int do_fall)
    // Advance player physics one frame.
    // Applies gravity, jump, coyote time, walk, step-up, platform fall-through.
    // Does NOT modify world (digging/placing stays in main.c).
```

---

## rover.h / rover.c — rover physics + sprite

```c
typedef struct {
    float x, y;       // top-left pixel position
    float vx, vy;     // velocity (pixels/frame)
    int grounded;     // 1 = wheels on solid ground
    int facing;       // 1=right, -1=left
    int in_rover;     // 1 = player is currently driving
    int handbrake;    // 1 = parked (no slope rolling); 0 = free
} RoverState;

void rover_update(RoverState *r, uint8_t *world,
                  int move_left, int move_right, int braking)
    // Advance rover physics one frame (always runs, occupied or not).
    // Handles gravity (1.4×), throttle, drag, slope rolling, step-up,
    // edge erosion of sticky dirt, and ground snap.
    // move_left / move_right only applied when r->in_rover is set.

void draw_rover_sheared(Color *pixels, int rx, int ry, int rfacing, int slope)
    // Write rover sprite into pixel buffer with vertical column shear.
    // slope = right_wheel_y - left_wheel_y; clamped to ±7 by caller.
    // rfacing: 1=right (normal), -1=left (horizontally mirrored).
```

---

## rover_arm.h / rover_arm.c — ballistic arm + projectile

```c
typedef struct {
    float angle;      // absolute degrees: 0=right, 90=up, 180=left
    float charge;     // power [0,1]
    int   ammo_type;  // AMMO_SOIL_BALL / AMMO_STICKY_SOIL / AMMO_LIQUID_SOIL
} ArmState;

typedef struct {
    int   active;     // 1 = projectile in flight
    int   ammo;       // ammo type locked at fire time
    float x, y;       // position (pixels)
    float vx, vy;     // velocity (pixels/frame)
    float charge;     // arm charge locked at fire time → sets deposit radius
} ProjState;

void arm_update(ArmState *a, const RoverState *r, float angle_delta, float power_delta)
    // Update arm angle and charge from input deltas.
    // Clamps angle to facing half when rover is rolling; full 5–175° arc when handbraked.

void arm_fire(const ArmState *a, ProjState *proj, const RoverState *r)
    // Spawn projectile from barrel tip. Locks ammo and charge into ProjState.
    // No-op if proj->active is already 1.

void proj_update(ProjState *proj, uint8_t *world)
    // Advance projectile physics one frame (no-op if !proj->active).
    // Applies PROJ_GRAVITY, moves position, checks terrain collision.
    // On hit: dispatches impact_soil_ball/sticky/liquid and sets active=0.
```

---

## render.h / render.c — rendering

```c
void render_world_to_pixels(Color *pixels, const uint8_t *world, const uint8_t *water)
    // Write all terrain cells to the pixel buffer.
    // Water is read from the parallel water[] amount array.
    // Full cells (≥WATER_FULL): surface bright / deep dark. Shallow/damp: bright. Dry: clear.

void render_player_to_pixels(Color *pixels, const PlayerState *p)
    // Write player sprite to pixel buffer (skip when in_rover — call only when on foot).
    // Uses p->facing for mirror, p->anim_frame for walk cycle.

void render_rover_to_pixels(Color *pixels, const uint8_t *world,
                             const RoverState *r, const ArmState *a, const ProjState *proj)
    // Write rover sprite (slope-sheared), arm line, and projectile dot to pixel buffer.
    // Arm and projectile only drawn when r->in_rover is set.

void render_screen_overlay(const PlayerState *p, const RoverState *r,
                            const ArmState *a, const ProjState *proj,
                            const uint8_t *world,
                            int sel_wx, int sel_wy,
                            int show_debug, int near_rover, int input_mode,
                            int offsetX, int offsetY, int scaledW, int scaledH, int scale)
    // All screen-space raylib draw calls (between BeginDrawing/EndDrawing):
    //   debug overlay, cell selection outline, trajectory arc,
    //   power bar, contextual prompts, HUD text.
    // sel_wx/wy = -1 means no cell selected.
    // near_rover: show "F: Enter rover" prompt.
```
