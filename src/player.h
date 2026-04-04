#pragma once
#include "defs.h"

typedef struct {
    float x, y;          // top-left pixel position
    float vx, vy;        // velocity (pixels/frame)
    int grounded;        // 1 = standing on solid ground or platform
    int coyote_timer;    // frames remaining to jump after leaving ground
    int facing;          // 1 = right, -1 = left
    int anim_frame;      // 0 or 1: current walk-cycle frame
    int anim_timer;      // frame counter for walk animation (advances every 8 frames)
    int fall_through_timer; // frames left ignoring platform collision (fall-through)
    int inv_dirt;        // dirt carried in inventory (0..INV_MAX)
} PlayerState;

// Advance player physics one frame.
// Reads world for collision; does not modify world (digging is in main).
// move_left/move_right: directional input flags
// do_jump: jump pressed this frame (edge-triggered)
// do_fall: fall-through pressed (held; S or Down)
void player_update(PlayerState *p, const Cell *cells,
                   int move_left, int move_right, int do_jump, int do_fall);
