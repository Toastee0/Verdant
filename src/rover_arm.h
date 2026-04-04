#pragma once
#include "defs.h"
#include "rover.h"

typedef struct {
    float angle;      // absolute degrees: 0=right, 90=up, 180=left
    float charge;     // power charge [0,1]; 0=min power, 1=max power
    int   ammo_type;  // AMMO_SOIL_BALL / AMMO_STICKY_SOIL / AMMO_LIQUID_SOIL
} ArmState;

typedef struct {
    int   active;     // 1 = projectile in flight
    int   ammo;       // ammo type locked at fire time
    float x, y;       // position (pixels)
    float vx, vy;     // velocity (pixels/frame)
    float charge;     // arm charge locked at fire time — sets deposit radius on impact
} ProjState;

// Update arm angle and charge from input deltas.
// Clamps angle to facing half when rover is rolling; full arc when handbraked.
// angle_delta: degrees per frame (from arrow keys / right stick X)
// power_delta: charge fraction per frame (from arrow keys / right stick Y)
void arm_update(ArmState *a, const RoverState *r,
                float angle_delta, float power_delta);

// Spawn a projectile from the barrel tip using the current arm state.
// Locks ammo type and charge into ProjState. No-op if proj->active.
void arm_fire(const ArmState *a, ProjState *proj, const RoverState *r);

// Advance projectile physics one frame.
// Applies PROJ_GRAVITY, moves the projectile, checks for terrain collision.
// On hit: dispatches the correct impact_* function and sets active=0.
void proj_update(ProjState *proj, Cell *cells);
