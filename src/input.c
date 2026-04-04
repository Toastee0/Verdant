#include "input.h"

void input_poll(InputState *inp, int in_rover,
                int screenW, int screenH,
                int offsetX, int offsetY, int scale) {
    // ── Mouse world position ───────────────────────────────────────────────
    Vector2 mouse = GetMousePosition();
    int mwx = (int)((mouse.x - offsetX) / scale);
    int mwy = (int)((mouse.y - offsetY) / scale);

    int mouse_moved = (mwx != inp->_last_mx || mwy != inp->_last_my);
    inp->_last_mx = mwx;
    inp->_last_my = mwy;
    inp->mouse_wx = mwx;
    inp->mouse_wy = mwy;

    // ── Raw keyboard ──────────────────────────────────────────────────────
    int shift = IsKeyDown(KEY_LEFT_SHIFT) || IsKeyDown(KEY_RIGHT_SHIFT);
    int ctrl  = IsKeyDown(KEY_LEFT_CONTROL) || IsKeyDown(KEY_RIGHT_CONTROL);

    // Suppress unused warning when not building with -Wunused
    (void)screenW; (void)screenH;

    // Movement — arrow keys are arm controls in rover mode
    inp->move_left  = IsKeyDown(KEY_A) || (!in_rover && IsKeyDown(KEY_LEFT));
    inp->move_right = IsKeyDown(KEY_D) || (!in_rover && IsKeyDown(KEY_RIGHT));

    // Jump / fall-through (on foot only)
    inp->do_jump = !in_rover && (IsKeyPressed(KEY_W) || IsKeyPressed(KEY_UP)
                               || IsKeyPressed(KEY_SPACE));
    inp->do_fall = !in_rover && (IsKeyDown(KEY_S) || IsKeyDown(KEY_DOWN));

    // Rover braking (replaces do_fall while in rover)
    if (in_rover)
        inp->do_fall = IsKeyDown(KEY_S);   // S only — arrow keys busy for arm

    // Vehicle / handbrake / ammo
    inp->do_vehicle   = IsKeyPressed(KEY_F);
    inp->do_handbrake = IsKeyPressed(KEY_P);
    inp->cycle_ammo   = in_rover && (IsKeyPressed(KEY_TAB) || IsKeyPressed(KEY_Q));

    // Fire (rover only, edge-triggered)
    inp->do_fire = in_rover && (IsKeyPressed(KEY_SPACE) ||
                  IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_TRIGGER_2));

    // ── Arm angle / power ──────────────────────────────────────────────────
    inp->angle_delta = 0.0f;
    inp->power_delta = 0.0f;
    if (in_rover) {
        if (shift || ctrl) {
            float step  = ctrl ? 10.0f : 1.0f;
            float pstep = ctrl ? 0.10f : 0.01f;
            if (IsKeyPressed(KEY_LEFT))  inp->angle_delta -= step;
            if (IsKeyPressed(KEY_RIGHT)) inp->angle_delta += step;
            if (IsKeyPressed(KEY_DOWN))  inp->power_delta -= pstep;
            if (IsKeyPressed(KEY_UP))    inp->power_delta += pstep;
        } else {
            if (IsKeyDown(KEY_LEFT))  inp->angle_delta -= ARM_ANGLE_SPEED;
            if (IsKeyDown(KEY_RIGHT)) inp->angle_delta += ARM_ANGLE_SPEED;
            if (IsKeyDown(KEY_DOWN))  inp->power_delta -= ARM_CHARGE_RATE;
            if (IsKeyDown(KEY_UP))    inp->power_delta += ARM_CHARGE_RATE;
        }
    }

    // ── UI keys ────────────────────────────────────────────────────────────
    inp->toggle_fullscreen = IsKeyPressed(KEY_F11);
    inp->toggle_debug      = IsKeyPressed(KEY_GRAVE);
    inp->quit              = IsKeyPressed(KEY_ESCAPE);

    // ── Mouse buttons ──────────────────────────────────────────────────────
    inp->dig_held  = IsMouseButtonDown(MOUSE_BUTTON_LEFT)    || IsKeyDown(KEY_E);
    inp->dig_just  = IsMouseButtonPressed(MOUSE_BUTTON_LEFT) || IsKeyPressed(KEY_E);
    inp->place_just = IsMouseButtonPressed(MOUSE_BUTTON_RIGHT);

    // ── Gamepad ────────────────────────────────────────────────────────────
    if (IsGamepadAvailable(0)) {
        float ax  = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_X);
        float ay  = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_Y);
        float rsx = GetGamepadAxisMovement(0, GAMEPAD_AXIS_RIGHT_X);
        float rsy = GetGamepadAxisMovement(0, GAMEPAD_AXIS_RIGHT_Y);

        if (ax < -0.3f) inp->move_left  = 1;
        if (ax >  0.3f) inp->move_right = 1;

        if (!in_rover) {
            if (ay > 0.5f) inp->do_fall = 1;
            if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN))
                inp->do_jump = 1;
        } else {
            if (fabsf(rsx) > 0.15f) inp->angle_delta += rsx * ARM_ANGLE_SPEED * 2.5f;
            if (fabsf(rsy) > 0.15f) inp->power_delta -= rsy * ARM_CHARGE_RATE * 2.5f;
            if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN) && !inp->do_fire)
                inp->do_fire = 1;
        }
        if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_LEFT))
            inp->do_vehicle = 1;

        // Gamepad activity switches to gamepad mode
        if (ax < -0.3f || ax > 0.3f || ay < -0.3f || ay > 0.3f)
            inp->input_mode = 1;
        if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN))
            inp->input_mode = 1;
    }

    // Mouse activity switches to mouse mode
    if (mouse_moved || IsMouseButtonPressed(MOUSE_BUTTON_LEFT) ||
            IsMouseButtonPressed(MOUSE_BUTTON_RIGHT) ||
            inp->move_left || inp->move_right || inp->do_jump || GetKeyPressed() != 0)
        inp->input_mode = 0;
}
