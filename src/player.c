#include "player.h"
#include "world.h"

void player_update(PlayerState *p, const uint8_t *world,
                   int move_left, int move_right, int do_jump, int do_fall) {
    if (p->fall_through_timer > 0) p->fall_through_timer--;
    int include_plat = (p->fall_through_timer <= 0);

    p->grounded = box_solid_ex(world, p->x, p->y + 1.0f, CHAR_W, CHAR_H, include_plat);

    // Fall through platforms: if standing on platform and pressing down, start timer
    if (do_fall && p->grounded && include_plat) {
        int fy = (int)(p->y + CHAR_H);
        int on_plat = 0;
        for (int fx = (int)p->x; fx <= (int)p->x + CHAR_W - 1 && !on_plat; fx++)
            if (fx >= 0 && fx < WORLD_W && fy >= 0 && fy < WORLD_H)
                if (CELL_TYPE(world[fy * WORLD_W + fx]) == CELL_PLATFORM) on_plat = 1;
        if (on_plat) { p->fall_through_timer = 15; include_plat = 0; p->grounded = 0; }
    }

    // Coyote time
    if (p->grounded) {
        p->coyote_timer = COYOTE_FRAMES;
    } else if (p->coyote_timer > 0) {
        p->coyote_timer--;
    }

    // Gravity
    if (!p->grounded) {
        p->vy += GRAVITY;
        if (p->vy > MAX_FALL) p->vy = MAX_FALL;
    } else if (p->vy > 0.0f) {
        p->vy = 0.0f;
    }

    // Jump
    if (do_jump && p->coyote_timer > 0) {
        p->vy = JUMP_VEL;
        p->grounded = 0;
        p->coyote_timer = 0;
    }

    // Horizontal movement
    p->vx = 0.0f;
    if (move_left)  { p->vx = -WALK_SPEED; p->facing = -1; }
    if (move_right) { p->vx =  WALK_SPEED; p->facing =  1; }

    if (p->vx != 0.0f) {
        float new_x = p->x + p->vx;
        if (new_x < 0)                new_x = 0;
        if (new_x + CHAR_W > WORLD_W) new_x = (float)(WORLD_W - CHAR_W);
        if (!box_solid_ex(world, new_x, p->y, CHAR_W, CHAR_H, 0)) {
            p->x = new_x;
        } else if (p->grounded) {
            // Step up
            for (int s = 1; s <= MAX_STEP_UP; s++) {
                if (!box_solid_ex(world, new_x, p->y - (float)s, CHAR_W, CHAR_H, 0)) {
                    p->x = new_x;
                    p->y -= (float)s;
                    break;
                }
            }
        }
    }

    // Vertical movement
    if (p->vy != 0.0f) {
        float new_y     = p->y + p->vy;
        int plat_solid  = (p->vy > 0.0f) && include_plat;
        if (!box_solid_ex(world, p->x, new_y, CHAR_W, CHAR_H, plat_solid)) {
            p->y = new_y;
        } else {
            if (p->vy > 0.0f) {
                float fy = (float)(int)p->y;
                while (!box_solid_ex(world, p->x, fy + 1.0f, CHAR_W, CHAR_H, plat_solid))
                    fy += 1.0f;
                p->y = fy;
                p->grounded = 1;
            }
            p->vy = 0.0f;
        }
    }

    // Clamp to world bounds
    if (p->y < 0)                { p->y = 0;                          p->vy = 0; }
    if (p->y + CHAR_H > WORLD_H) { p->y = (float)(WORLD_H - CHAR_H); p->vy = 0; p->grounded = 1; }

    // Animation
    if (move_left || move_right) {
        if (++p->anim_timer >= 8) { p->anim_timer = 0; p->anim_frame ^= 1; }
    } else {
        p->anim_frame = 0;
        p->anim_timer = 0;
    }
}
