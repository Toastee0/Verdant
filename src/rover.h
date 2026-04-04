#pragma once
#include "defs.h"

typedef struct {
    float x, y;          // top-left pixel position
    float vx, vy;        // velocity (pixels/frame)
    int grounded;        // 1 = wheels touching solid ground
    int facing;          // 1 = right, -1 = left
    int in_rover;        // 1 = player is currently driving
    int handbrake;       // 1 = parked, won't roll; 0 = free to roll on slopes
} RoverState;

// Advance rover physics one frame (always runs, even when unoccupied).
// Handles gravity, throttle, drag, slope rolling, step-up, edge erosion, ground snap.
// move_left / move_right: throttle input (ignored unless in_rover)
// braking: S key held while in rover
void rover_update(RoverState *r, uint8_t *world,
                  int move_left, int move_right, int braking);

// Draw the rover sprite into the pixel buffer, sheared vertically to match
// the slope of the terrain under the wheels.
// slope = right_wheel_ground_y - left_wheel_ground_y (positive = right side lower)
void draw_rover_sheared(Color *pixels, int rx, int ry, int rfacing, int slope);
