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
