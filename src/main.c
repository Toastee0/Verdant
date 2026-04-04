#include "defs.h"
#include "terrain.h"
#include "input.h"
#include "world.h"
#include "sim/dirt.h"
#include "sim/water.h"
#include "player.h"
#include "rover.h"
#include "rover_arm.h"
#include "render.h"

int main(void) {
    SetConfigFlags(FLAG_BORDERLESS_WINDOWED_MODE | FLAG_VSYNC_HINT);
    InitWindow(0, 0, "VERDANT F1 — Scaled Canvas");

    // ── World ─────────────────────────────────────────────────────────────
    static uint8_t world[WORLD_W * WORLD_H];
    static uint8_t water[WORLD_W * WORLD_H];   // parallel water-amount array (0..255)
    terrain_generate(world, water);

    Image     worldImg = GenImageColor(WORLD_W, WORLD_H, BLACK);
    Texture2D worldTex = LoadTextureFromImage(worldImg);
    SetTextureFilter(worldTex, TEXTURE_FILTER_POINT);

    const int dirtStart = (WORLD_H * 2) / 3 - 10;   // row 170 (matches terrain.c)

    // ── Player state ──────────────────────────────────────────────────────
    PlayerState player = {0};
    player.x      = 30.0f;
    player.y      = (float)(dirtStart - CHAR_H);
    player.facing = 1;

    // ── Rover state ───────────────────────────────────────────────────────
    RoverState rover = {0};
    rover.x         = 80.0f;
    rover.y         = (float)(dirtStart - ROVER_H);
    rover.facing    = 1;
    rover.handbrake = 1;   // starts parked

    // ── Arm + projectile state ────────────────────────────────────────────
    ArmState arm = {0};
    arm.angle     = 90.0f;
    arm.charge    = 0.5f;
    arm.ammo_type = AMMO_SOIL_BALL;

    ProjState proj = {0};

    // ── Input state ───────────────────────────────────────────────────────
    InputState inp = {0};

    // ── UI state ──────────────────────────────────────────────────────────
    int sel_wx    = -1, sel_wy = -1;
    int show_debug = 0;
    double dig_timer = 0.0;
    int frame = 0;

    while (!WindowShouldClose()) {
        // ── Window / scale ─────────────────────────────────────────────────
        int screenW = GetScreenWidth(),  screenH = GetScreenHeight();
        int scaleX  = screenW / WORLD_W, scaleY  = screenH / WORLD_H;
        int scale   = (scaleX < scaleY) ? scaleX : scaleY;
        if (scale < 1) scale = 1;
        int scaledW = WORLD_W * scale,   scaledH = WORLD_H * scale;
        int offsetX = (screenW - scaledW) / 2;
        int offsetY = (screenH - scaledH) / 2;

        // ── Input ──────────────────────────────────────────────────────────
        input_poll(&inp, rover.in_rover, screenW, screenH, offsetX, offsetY, scale);

        if (inp.toggle_fullscreen) ToggleBorderlessWindowed();
        if (inp.toggle_debug)      show_debug ^= 1;
        if (inp.quit)              break;

        // ── Dirt + water simulation ────────────────────────────────────────
        tick_dirt(world, frame & 1);
        tick_water(world, water, frame & 1);
        tick_water(world, water, (frame + 1) & 1);
        tick_water(world, water, frame & 1);

        // ── Rover enter / exit (F key) ─────────────────────────────────────
        float pcx_f = player.x + CHAR_W  * 0.5f;
        float pcy_f = player.y + CHAR_H  * 0.5f;
        float rcx_f = rover.x  + ROVER_W * 0.5f;
        float rcy_f = rover.y  + ROVER_H * 0.5f;
        float ddx = pcx_f - rcx_f, ddy = pcy_f - rcy_f;
        int near_rover = !rover.in_rover &&
                         (ddx*ddx + ddy*ddy < (float)(ROVER_ENTER_R * ROVER_ENTER_R));

        if (inp.do_vehicle) {
            if (rover.in_rover) {
                float ex_r = rover.x + ROVER_W + 1;
                float ex_l = rover.x - CHAR_W  - 1;
                float ey   = rover.y + ROVER_H - CHAR_H;
                float exit_x = (rover.facing > 0) ? ex_r : ex_l;
                float alt_x  = (rover.facing > 0) ? ex_l : ex_r;
                if (box_solid_ex(world, exit_x, ey, CHAR_W, CHAR_H, 0))
                    exit_x = alt_x;
                player.x      = exit_x;
                player.y      = ey;
                player.vx     = 0; player.vy = 0;
                player.facing = rover.facing;
                rover.in_rover = 0;
            } else if (near_rover) {
                rover.in_rover = 1;
                rover.facing   = player.facing;
            }
        }

        // Handbrake toggle
        if (inp.do_handbrake && rover.in_rover)
            rover.handbrake ^= 1;

        // Ammo cycle
        if (inp.cycle_ammo)
            arm.ammo_type = (arm.ammo_type + 1) % AMMO_COUNT;

        // ── Rover physics ──────────────────────────────────────────────────
        rover_update(&rover, world,
                     inp.move_left, inp.move_right, inp.do_fall);

        // ── Arm + projectile ──────────────────────────────────────────────
        if (rover.in_rover) {
            arm_update(&arm, &rover, inp.angle_delta, inp.power_delta);
            if (inp.do_fire) arm_fire(&arm, &proj, &rover);
        }
        proj_update(&proj, world);

        // ── Player physics (on foot only) ──────────────────────────────────
        if (!rover.in_rover) {
            player_update(&player, world,
                          inp.move_left, inp.move_right,
                          inp.do_jump, inp.do_fall);

            // Mouse facing: player faces cursor in mouse mode
            if (inp.input_mode == 0)
                player.facing = (inp.mouse_wx >= (int)(player.x + CHAR_W * 0.5f)) ? 1 : -1;
        }

        // ── Cell selection & dig / place (on foot only) ───────────────────
        sel_wx = -1; sel_wy = -1;
        if (!rover.in_rover) {
            float pcx = player.x + CHAR_W * 0.5f;
            float pcy = player.y + CHAR_H * 0.5f;
            int   pr2  = PICKUP_RADIUS * PICKUP_RADIUS;

            if (inp.input_mode == 0) {
                // Mouse mode: hover cell under cursor within pickup radius
                if (inp.mouse_wx >= 0 && inp.mouse_wx < WORLD_W &&
                    inp.mouse_wy >= 0 && inp.mouse_wy < WORLD_H) {
                    float dx = inp.mouse_wx - pcx, dy = inp.mouse_wy - pcy;
                    if (dx*dx + dy*dy <= (float)pr2 &&
                        CELL_TYPE(world[inp.mouse_wy * WORLD_W + inp.mouse_wx]) != CELL_AIR)
                        { sel_wx = inp.mouse_wx; sel_wy = inp.mouse_wy; }
                }
            } else {
                // Gamepad mode: nearest dirt cell in facing direction within pickup radius
                float best = (float)(pr2 + 1);
                int bx0 = (int)pcx - PICKUP_RADIUS, bx1 = (int)pcx + PICKUP_RADIUS;
                int by0 = (int)pcy - PICKUP_RADIUS, by1 = (int)pcy + PICKUP_RADIUS;
                for (int wy = by0; wy <= by1; wy++) {
                    for (int wx = bx0; wx <= bx1; wx++) {
                        if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
                        float dx = wx - pcx, dy = wy - pcy;
                        if (dx * player.facing < 0.0f) continue;
                        float d2 = dx*dx + dy*dy;
                        if (d2 > (float)pr2 || d2 >= best) continue;
                        if (CELL_TYPE(world[wy * WORLD_W + wx]) == CELL_DIRT)
                            { best = d2; sel_wx = wx; sel_wy = wy; }
                    }
                }
            }

            // Dig
            if (inp.dig_held) { dig_timer -= GetFrameTime(); } else { dig_timer = 0.0; }
            if (inp.dig_just || (inp.dig_held && dig_timer <= 0.0)) {
                if (inp.dig_just || dig_timer <= 0.0) dig_timer = DIG_REPEAT_MS / 1000.0;
                if (sel_wx >= 0 && player.inv_dirt < INV_MAX &&
                    CELL_TYPE(world[sel_wy * WORLD_W + sel_wx]) == CELL_DIRT) {
                    world[sel_wy * WORLD_W + sel_wx] = CELL_AIR;
                    player.inv_dirt++;
                    unstick(world, sel_wx,     sel_wy - 1);
                    unstick(world, sel_wx - 1, sel_wy - 1);
                    unstick(world, sel_wx + 1, sel_wy - 1);
                }
            }

            // Place
            if (inp.place_just) {
                int wx = inp.mouse_wx, wy = inp.mouse_wy;
                if (wx >= 0 && wx < WORLD_W && wy >= 0 && wy < WORLD_H) {
                    if (CELL_TYPE(world[wy * WORLD_W + wx]) == CELL_AIR &&
                        player.inv_dirt > 0) {
                        world[wy * WORLD_W + wx] = CELL_DIRT;
                        player.inv_dirt--;
                    }
                }
            }
        }

        // ── Render ────────────────────────────────────────────────────────
        Color *pixels = worldImg.data;
        render_world_to_pixels(pixels, world, water);
        render_rover_to_pixels(pixels, world, &rover, &arm, &proj);
        if (!rover.in_rover)
            render_player_to_pixels(pixels, &player);
        UpdateTexture(worldTex, pixels);

        BeginDrawing();
            ClearBackground(BLACK);
            DrawTexturePro(
                worldTex,
                (Rectangle){0, 0, WORLD_W, WORLD_H},
                (Rectangle){(float)offsetX, (float)offsetY,
                             (float)scaledW, (float)scaledH},
                (Vector2){0, 0}, 0.0f, WHITE
            );
            render_screen_overlay(&player, &rover, &arm, &proj, world,
                                  sel_wx, sel_wy, show_debug, near_rover,
                                  inp.input_mode,
                                  offsetX, offsetY, scaledW, scaledH, scale);
        EndDrawing();

        frame++;
    }

    UnloadTexture(worldTex);
    UnloadImage(worldImg);
    CloseWindow();
    return 0;
}
