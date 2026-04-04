#include "rover.h"
#include "world.h"
#include "sprites.h"

void rover_update(RoverState *r, Cell *cells,
                  int move_left, int move_right, int braking) {
    // ── Gravity / vertical ─────────────────────────────────────────────────
    r->grounded = box_solid_ex(cells, r->x, r->y + 1.0f, ROVER_W, ROVER_H, 0);
    if (!r->grounded) {
        r->vy += GRAVITY * 1.4f;
        if (r->vy > ROVER_MAX_FALL) r->vy = ROVER_MAX_FALL;
    }

    // ── Slope sensing ─────────────────────────────────────────────────────
    int left_gx  = (int)r->x + 4;
    int right_gx = (int)r->x + ROVER_W - 5;
    int scan_base = (int)r->y + ROVER_H + 1;
    int left_gy   = ground_y_at(cells, left_gx,  scan_base);
    int right_gy  = ground_y_at(cells, right_gx, scan_base);
    int slope_raw = right_gy - left_gy;

    // ── Horizontal velocity ────────────────────────────────────────────────
    int throttle_left  = r->in_rover && move_left;
    int throttle_right = r->in_rover && move_right;

    if (throttle_left) {
        r->vx -= ROVER_ACCEL;
        if (r->vx < -ROVER_TOP_SPEED) r->vx = -ROVER_TOP_SPEED;
        r->facing = -1;
        r->handbrake = 0;
    } else if (throttle_right) {
        r->vx += ROVER_ACCEL;
        if (r->vx >  ROVER_TOP_SPEED) r->vx =  ROVER_TOP_SPEED;
        r->facing = 1;
        r->handbrake = 0;
    }

    if (!throttle_left && !throttle_right && r->vx != 0.0f)
        r->facing = (r->vx < 0.0f) ? -1 : 1;

    // Slope rolling
    if (r->grounded && !r->handbrake) {
        r->vx += slope_raw * ROVER_SLOPE_FORCE;
        if (r->vx >  ROVER_TOP_SPEED * 1.5f) r->vx =  ROVER_TOP_SPEED * 1.5f;
        if (r->vx < -ROVER_TOP_SPEED * 1.5f) r->vx = -ROVER_TOP_SPEED * 1.5f;
    }

    // Drag
    if (r->grounded) {
        if (braking && r->in_rover) {
            r->vx *= ROVER_BRAKE_DRAG;
        } else if (r->handbrake) {
            r->vx *= ROVER_PARK_DRAG;
        } else {
            r->vx *= ROVER_ROLL_DRAG;
        }
        if (r->vx > -0.01f && r->vx < 0.01f) r->vx = 0.0f;
    }

    // ── Apply horizontal movement ──────────────────────────────────────────
    if (r->vx != 0.0f) {
        float new_x = r->x + r->vx;
        if (new_x < 0)                 { new_x = 0;                           r->vx = 0.0f; }
        if (new_x + ROVER_W > WORLD_W) { new_x = (float)(WORLD_W - ROVER_W); r->vx = 0.0f; }

        if (!box_solid_ex(cells, new_x, r->y, ROVER_W, ROVER_H, 0)) {
            r->x = new_x;
        } else if (r->grounded) {
            int stepped = 0;
            for (int s = 1; s <= ROVER_STEP_UP; s++) {
                if (!box_solid_ex(cells, new_x, r->y - (float)s, ROVER_W, ROVER_H, 0)) {
                    r->x = new_x;
                    r->y -= (float)s;
                    stepped = 1;
                    break;
                }
            }
            if (!stepped) r->vx = 0.0f;
        } else {
            r->vx = 0.0f;
        }
    }

    // ── Edge erosion ───────────────────────────────────────────────────────
    if (r->grounded && r->vx != 0.0f) {
        int dir    = (r->vx > 0.0f) ? 1 : -1;
        int foot_y = (int)r->y + ROVER_H;
        if (foot_y >= 0 && foot_y < WORLD_H) {
            for (int col = 0; col < ROVER_W; col++) {
                int wx = (int)r->x + col;
                if (wx < 0 || wx >= WORLD_W) continue;
                if (cells[foot_y * WORLD_W + wx].type != (CELL_DIRT | FLAG_STICKY)) continue;
                int nx = wx + dir;
                if (nx < 0 || nx >= WORLD_W) continue;
                if (CELL_TYPE(cells[foot_y * WORLD_W + nx].type) == CELL_AIR)
                    cells[foot_y * WORLD_W + wx].type &= ~FLAG_STICKY;
            }
        }
    }

    // ── Ground snap ────────────────────────────────────────────────────────
    {
        int snapped = 0;
        for (int snap = 0; snap < ROVER_STEP_UP + 2; snap++) {
            if (!box_solid_ex(cells, r->x, r->y + 1.0f, ROVER_W, ROVER_H, 0)) {
                r->y += 1.0f; snapped = 1;
            } else break;
        }
        r->grounded = box_solid_ex(cells, r->x, r->y + 1.0f, ROVER_W, ROVER_H, 0);
        if (snapped && r->grounded) r->vy = 0.0f;
    }

    // ── Apply vertical movement ────────────────────────────────────────────
    if (r->vy != 0.0f) {
        float new_y = r->y + r->vy;
        if (!box_solid_ex(cells, r->x, new_y, ROVER_W, ROVER_H, 0)) {
            r->y = new_y;
        } else {
            if (r->vy > 0.0f) {
                float fy = (float)(int)r->y;
                while (!box_solid_ex(cells, r->x, fy + 1.0f, ROVER_W, ROVER_H, 0))
                    fy += 1.0f;
                r->y        = fy;
                r->grounded = 1;
                r->vy = -r->vy * ROVER_BOUNCE;
                if (r->vy > -0.5f) r->vy = 0.0f;
                if (!r->handbrake)
                    r->vx += slope_raw * ROVER_SLOPE_FORCE * 4.0f;
            }
            if (r->vy < 0.0f) r->vy = 0.0f;
        }
    }

    if (r->y < 0)                { r->y = 0;                           r->vy = 0; }
    if (r->y + ROVER_H > WORLD_H){ r->y = (float)(WORLD_H - ROVER_H); r->vy = 0; r->grounded = 1; }
}

void draw_rover_sheared(Color *pixels, int rx, int ry, int rfacing, int slope) {
    for (int row = 0; row < ROVER_H; row++) {
        for (int col = 0; col < ROVER_W; col++) {
            int src = (rfacing < 0) ? (ROVER_W - 1 - col) : col;
            uint8_t idx = ROVER_SPRITE[row][src];
            if (idx == 0) continue;
            int shear = (slope * col) / ROVER_W;
            int wx = rx + col;
            int wy = ry + row + shear;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            pixels[wy * WORLD_W + wx] = ROVER_PAL[idx];
        }
    }
}
