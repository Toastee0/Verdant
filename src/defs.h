#pragma once
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

// Water is stored in a parallel uint8_t water[WORLD_W*WORLD_H] array, not as a cell type.
// A CELL_AIR cell is "wet" when water[idx] > WATER_DRY (and visible at > WATER_DAMP).
#define WATER_DRY       0    // completely dry (no water present)
#define WATER_DAMP      8    // below this: render as dry
#define WATER_SHALLOW  64    // below this: surface/shallow color
#define WATER_FULL    200    // at or above: solid saturated water color

// Bit 7 of a cell byte: dirt won't fall while this is set.
// Generated terrain starts sticky. Digging a neighbour clears it.
#define FLAG_STICKY    0x80
#define CELL_TYPE(c)   ((c) & 0x7F)

// ── World cell ─────────────────────────────────────────────────────────────
// 4 bytes per cell. type uses the same CELL_* / FLAG_STICKY encoding as before.
// water: 0–255 liquid amount (only meaningful when type == CELL_AIR).
// temp:  0–255, initialised to 128 (ambient); reserved for thermal sim.
// vector: 0–255, initialised to 0; reserved for flow/force direction.
typedef struct {
    uint8_t type;    // CELL_AIR/STONE/DIRT/PLATFORM + FLAG_STICKY in bit 7
    uint8_t water;   // 0–255 water amount
    uint8_t temp;    // 0–255 temperature (128 = ambient)
    uint8_t vector;  // reserved
} Cell;

// === BLOB PRESSURE SIM ===
// A blob is a connected region of CELL_AIR cells (4-connectivity).
// blob_id[WORLD_W*WORLD_H] maps each cell to its owning blob (BLOB_NONE=unassigned/solid).
#define MAX_BLOBS  2048
#define BLOB_NONE  0    // sentinel: solid or unassigned cell

typedef struct {
    float   water_sum;  // total water amount across all cells in this blob
    float   volume;     // cell count
    uint8_t sealed;     // 1=fully enclosed by solid (no world-boundary contact)
    int     dirty;      // 1=topology changed, needs re-flood-fill before next tick
    int     active;     // 1=slot in use
} Blob;

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
