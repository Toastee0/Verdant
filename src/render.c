#include "render.h"
#include "sprites.h"
#include "world.h"

// Ammo display names — only used in render (HUD prompt)
static const char *AMMO_NAMES[AMMO_COUNT] = {
    "SOIL BALL", "STICKY SOIL", "LIQUID SOIL"
};

void render_world_to_pixels(Color *pixels, const uint8_t *world) {
    for (int y = 0; y < WORLD_H; y++) {
        for (int x = 0; x < WORLD_W; x++) {
            int i = y * WORLD_W + x;
            switch (CELL_TYPE(world[i])) {
                case CELL_STONE:    pixels[i] = (Color){128, 128, 128, 255}; break;
                case CELL_DIRT:     pixels[i] = (Color){139,  90,  43, 255}; break;
                case CELL_PLATFORM: pixels[i] = (Color){165, 105,  50, 255}; break;
                case CELL_WATER: {
                    int surface = (y == 0) ||
                                  (CELL_TYPE(world[(y-1)*WORLD_W+x]) != CELL_WATER);
                    pixels[i] = surface
                        ? (Color){ 90, 160, 230, 255}
                        : (Color){ 30,  80, 160, 255};
                    break;
                }
                default: pixels[i] = (Color){255, 255, 255, 0}; break;
            }
        }
    }
}

void render_player_to_pixels(Color *pixels, const PlayerState *p) {
    int draw_x = (int)p->x, draw_y = (int)p->y;
    for (int row = 0; row < CHAR_H; row++) {
        for (int col = 0; col < CHAR_W; col++) {
            int     src = (p->facing < 0) ? (CHAR_W - 1 - col) : col;
            uint8_t idx = SPRITE[p->anim_frame][row][src];
            if (!idx) continue;
            int wx = draw_x + col, wy = draw_y + row;
            if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
            pixels[wy * WORLD_W + wx] = CHAR_PAL[idx];
        }
    }
}

void render_rover_to_pixels(Color *pixels, const uint8_t *world,
                             const RoverState *r, const ArmState *a,
                             const ProjState *proj) {
    // Rover sprite (slope-sheared)
    {
        int left_wx  = (int)r->x + 3;
        int right_wx = (int)r->x + ROVER_W - 4;
        int scan_y   = (int)r->y + ROVER_H;
        int left_g   = ground_y_at(world, left_wx,  scan_y);
        int right_g  = ground_y_at(world, right_wx, scan_y);
        int slope    = right_g - left_g;
        if (slope >  7) slope =  7;
        if (slope < -7) slope = -7;
        draw_rover_sheared(pixels, (int)r->x, (int)r->y, r->facing, slope);
    }

    // Arm line (only when player is in rover)
    if (r->in_rover) {
        float rad   = a->angle * (float)M_PI / 180.0f;
        float piv_x = r->x + ROVER_W * 0.5f;
        float piv_y = r->y + 2.0f;
        for (int i = 2; i <= ARM_LEN; i++) {
            int ax = (int)(piv_x + cosf(rad) * i);
            int ay = (int)(piv_y - sinf(rad) * i);
            if (ax < 0 || ax >= WORLD_W || ay < 0 || ay >= WORLD_H) continue;
            pixels[ay * WORLD_W + ax] = (Color){200, 200, 80, 255};
        }
    }

    // Projectile dot
    if (proj->active) {
        static const Color proj_cols[AMMO_COUNT] = {
            {139,  90,  43, 255},   // SOIL_BALL   — dirt brown
            { 80, 140,  60, 255},   // STICKY_SOIL — mossy green
            {180, 130,  40, 255},   // LIQUID_SOIL — muddy gold
        };
        Color pcol = proj_cols[proj->ammo];
        int px = (int)proj->x, py = (int)proj->y;
        for (int dy = -1; dy <= 1; dy++)
            for (int dx = -1; dx <= 1; dx++) {
                int wx = px+dx, wy = py+dy;
                if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
                pixels[wy * WORLD_W + wx] = pcol;
            }
    }
}

void render_screen_overlay(const PlayerState *p, const RoverState *r,
                            const ArmState *a, const ProjState *proj,
                            const uint8_t *world,
                            int sel_wx, int sel_wy,
                            int show_debug, int near_rover, int input_mode,
                            int offsetX, int offsetY,
                            int scaledW, int scaledH, int scale) {
    // ── Debug overlays ────────────────────────────────────────────────────
    if (show_debug && !r->in_rover) {
        float pcx = p->x + CHAR_W * 0.5f, pcy = p->y + CHAR_H * 0.5f;
        DrawCircleLines(
            offsetX + (int)(pcx * scale),
            offsetY + (int)(pcy * scale),
            PICKUP_RADIUS * scale,
            (Color){255, 255, 100, 80}
        );
        DrawText(TextFormat("Player:(%.0f,%.0f) vel:(%.1f,%.1f) %s%s",
            p->x, p->y, p->vx, p->vy,
            p->grounded ? "GND" : "AIR",
            p->fall_through_timer > 0 ? " FALLTHRU" : ""),
            8, 28, 16, YELLOW);
    }
    if (show_debug) {
        DrawText(TextFormat("Rover:(%.0f,%.0f) vel:(%.1f,%.1f) %s",
            r->x, r->y, r->vx, r->vy,
            r->grounded ? "GND" : "AIR"),
            8, 48, 16, (Color){180, 255, 160, 255});
    }

    // ── Cell selection outline ────────────────────────────────────────────
    if (sel_wx >= 0) {
        Color outline = (input_mode == 0)
            ? (Color){255, 255,  50, 230}
            : (Color){100, 220, 255, 230};
        DrawRectangleLinesEx(
            (Rectangle){
                (float)(offsetX + sel_wx * scale),
                (float)(offsetY + sel_wy * scale),
                (float)scale, (float)scale
            }, 1.0f, outline
        );
    }

    // ── Trajectory arc ────────────────────────────────────────────────────
    if (r->in_rover && !proj->active) {
        float rad   = a->angle * (float)M_PI / 180.0f;
        float speed = ARM_POWER_MIN + a->charge * (ARM_POWER_MAX - ARM_POWER_MIN);
        float piv_x = r->x + ROVER_W * 0.5f;
        float piv_y = r->y + 2.0f;
        float sx  = piv_x + cosf(rad) * ARM_LEN;
        float sy  = piv_y - sinf(rad) * ARM_LEN;
        float svx = cosf(rad) * speed;
        float svy = -sinf(rad) * speed;
        for (int step = 0; step < 120; step++) {
            svy += PROJ_GRAVITY;
            sx  += svx; sy += svy;
            if (sx < 0 || sx >= WORLD_W || sy < 0 || sy >= WORLD_H) break;
            if (CELL_TYPE(world[(int)sy * WORLD_W + (int)sx]) != CELL_AIR) break;
            if (step % 4 == 0) {
                int spx = offsetX + (int)(sx * scale);
                int spy = offsetY + (int)(sy * scale);
                DrawRectangle(spx, spy, 2, 2, (Color){255, 255, 100, 120});
            }
        }
    }

    // ── Power bar ─────────────────────────────────────────────────────────
    if (r->in_rover) {
        int bar_x = offsetX + scaledW/2 + 60;
        int bar_y = offsetY + 8;
        int bar_h = 40;
        DrawRectangle(bar_x, bar_y, 8, bar_h, (Color){60, 60, 60, 200});
        int filled = (int)(a->charge * bar_h);
        Color pcol = a->charge < 0.5f
            ? (Color){ 80, 200,  80, 255}
            : a->charge < 0.85f
                ? (Color){220, 200,  50, 255}
                : (Color){255,  80,  50, 255};
        DrawRectangle(bar_x, bar_y + bar_h - filled, 8, filled, pcol);
        DrawRectangleLines(bar_x, bar_y, 8, bar_h, WHITE);
        DrawText(TextFormat("%.0f°", a->angle),
            bar_x - 24, bar_y + bar_h + 2, 14, YELLOW);
    }

    // ── Contextual prompts ────────────────────────────────────────────────
    if (r->in_rover) {
        DrawText("F: Exit rover", offsetX + scaledW/2 - 50, offsetY + 8, 16, WHITE);
        DrawText(r->handbrake ? "BRAKE" : "ROLLING",
            offsetX + scaledW/2 - 28, offsetY + 28, 16,
            r->handbrake ? (Color){255,80,80,255} : (Color){80,255,120,255});
        static const Color ammo_cols[AMMO_COUNT] = {
            {200, 140,  60, 255},
            { 80, 200,  80, 255},
            {180, 160,  40, 255},
        };
        DrawText(TextFormat("< %s >  [Tab]", AMMO_NAMES[a->ammo_type]),
            offsetX + 8, offsetY + 28, 16, ammo_cols[a->ammo_type]);
    } else if (near_rover) {
        DrawText("F: Enter rover", offsetX + scaledW/2 - 56, offsetY + 8, 16, WHITE);
    }

    // ── HUD ───────────────────────────────────────────────────────────────
    DrawText(TextFormat("Dirt:%d/%d", p->inv_dirt, INV_MAX), 8, 8, 16, WHITE);
    DrawText(
        r->in_rover
            ? "A/D=Drive  S=Brake  P=Handbrake  Arrows=Aim  Space=Fire  Tab=Ammo  F=Exit"
            : "WASD=Move  Space=Jump  S=FallThru  LMB/E=Dig  RMB=Place  F=Rover  `=Debug  ESC=Quit",
        8, scaledH + offsetY - 20, 16, GRAY);
}
