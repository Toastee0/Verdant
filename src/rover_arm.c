#include "rover_arm.h"
#include "sim/impact.h"

void arm_update(ArmState *a, const RoverState *r,
                float angle_delta, float power_delta) {
    a->angle += angle_delta;

    // Clamp angle: full arc when handbraked, facing-half when rolling
    if (!r->handbrake) {
        if (r->facing > 0) {
            if (a->angle > 88.0f)        a->angle = 88.0f;
            if (a->angle < ARM_ANGLE_MIN) a->angle = ARM_ANGLE_MIN;
        } else {
            if (a->angle < 92.0f)        a->angle = 92.0f;
            if (a->angle > ARM_ANGLE_MAX) a->angle = ARM_ANGLE_MAX;
        }
    } else {
        if (a->angle > ARM_ANGLE_MAX) a->angle = ARM_ANGLE_MAX;
        if (a->angle < ARM_ANGLE_MIN) a->angle = ARM_ANGLE_MIN;
    }

    a->charge += power_delta;
    if (a->charge > 1.0f) a->charge = 1.0f;
    if (a->charge < 0.0f) a->charge = 0.0f;
}

void arm_fire(const ArmState *a, ProjState *proj, const RoverState *r) {
    if (proj->active) return;
    float rad   = a->angle * (float)M_PI / 180.0f;
    float speed = ARM_POWER_MIN + a->charge * (ARM_POWER_MAX - ARM_POWER_MIN);
    float pivot_x = r->x + ROVER_W * 0.5f;
    float pivot_y = r->y + 2.0f;
    proj->x      = pivot_x + cosf(rad) * ARM_LEN;
    proj->y      = pivot_y - sinf(rad) * ARM_LEN;
    proj->vx     = cosf(rad) * speed;
    proj->vy     = -sinf(rad) * speed;
    proj->ammo   = a->ammo_type;
    proj->charge = a->charge;   // locked at fire time for deposit radius
    proj->active = 1;
}

void proj_update(ProjState *proj, uint8_t *world) {
    if (!proj->active) return;

    proj->vy += PROJ_GRAVITY;
    proj->x  += proj->vx;
    proj->y  += proj->vy;

    int px = (int)proj->x, py = (int)proj->y;

    if (px < 0 || px >= WORLD_W || py < 0 || py >= WORLD_H) {
        proj->active = 0;
        return;
    }

    uint8_t hit = CELL_TYPE(world[py * WORLD_W + px]);
    if (hit == CELL_STONE || hit == CELL_DIRT) {
        int r = DEPOSIT_R_MIN + (int)(proj->charge * (DEPOSIT_R_MAX - DEPOSIT_R_MIN));
        switch (proj->ammo) {
            case AMMO_SOIL_BALL:    impact_soil_ball(world, px, py, r);    break;
            case AMMO_STICKY_SOIL:  impact_sticky_soil(world, px, py, r);  break;
            case AMMO_LIQUID_SOIL:  impact_liquid_soil(world, px, py, r);  break;
        }
        proj->active = 0;
    }
}
