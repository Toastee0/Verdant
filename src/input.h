#pragma once
#include "defs.h"

// All logical inputs for one frame, abstracted from physical keys/buttons.
// This is the control-remapping shim — change key bindings in input.c only.
typedef struct {
    // ── Movement ────────────────────────────────────────────────────────────
    int move_left;      // A / Left stick left
    int move_right;     // D / Left stick right
    int do_jump;        // W / Space / A-button (edge-triggered: only set on press frame)
    int do_fall;        // S / Down — on foot: fall through platform; in rover: brake

    // ── Vehicle ─────────────────────────────────────────────────────────────
    int do_vehicle;     // F / X-button — enter or exit rover (edge-triggered)
    int do_handbrake;   // P — toggle rover handbrake (edge-triggered)
    int do_fire;        // Space / RT2 — fire ballistic arm (edge-triggered, rover only)
    int cycle_ammo;     // Tab / Q — cycle ammo type (edge-triggered, rover only)

    // ── Arm (rover only) ────────────────────────────────────────────────────
    float angle_delta;  // degrees this frame (arrow keys / right stick X)
    float power_delta;  // charge fraction this frame (arrow keys / right stick Y)

    // ── UI ───────────────────────────────────────────────────────────────────
    int toggle_fullscreen; // F11 (edge-triggered)
    int toggle_debug;      // backtick (edge-triggered)
    int quit;              // Escape (edge-triggered)

    // ── Mouse (world coordinates, valid when input_mode==0) ─────────────────
    int mouse_wx;       // mouse world x after scale/offset correction
    int mouse_wy;       // mouse world y after scale/offset correction
    int dig_held;       // LMB held or E held
    int dig_just;       // LMB or E pressed this frame (edge-triggered)
    int place_just;     // RMB pressed this frame (edge-triggered)

    // ── Input source ────────────────────────────────────────────────────────
    int input_mode;     // 0 = mouse-aim mode, 1 = gamepad-nearest mode
    int _last_mx;       // (internal) previous mouse world x for moved detection
    int _last_my;       // (internal) previous mouse world y for moved detection
} InputState;

// Poll keyboard, mouse, and gamepad; fill inp for this frame.
// in_rover: changes key bindings (arrow keys control arm instead of movement).
// screenW/H, offsetX/Y, scale: needed to project mouse coords into world space.
void input_poll(InputState *inp, int in_rover,
                int screenW, int screenH,
                int offsetX, int offsetY, int scale);
