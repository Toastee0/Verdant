#include "raylib.h"
#include <stdint.h>
#include <string.h>

// === WORLD ===
#define WORLD_W    480
#define WORLD_H    270
#define CELL_AIR     0
#define CELL_STONE   1
#define CELL_DIRT    2

// === CHARACTER ===
#define CHAR_W  4
#define CHAR_H  8

// Physics — pixels per frame at ~60 fps
#define GRAVITY     0.35f
#define JUMP_VEL   -5.5f
#define WALK_SPEED  1.5f
#define MAX_FALL   10.0f

// Inventory
#define PICKUP_RADIUS 18    // world-pixel radius for harvest
#define INV_MAX       99

// 2-frame walk cycle: [frame][row][col]
// Palette: 0=transparent  1=skin  2=shirt (blue)  3=pants (dark blue)
static const uint8_t SPRITE[2][CHAR_H][CHAR_W] = {
    {   // frame 0 — legs wide
        {0,1,1,0},
        {0,1,1,0},
        {0,1,1,0},
        {2,2,2,2},
        {2,2,2,2},
        {3,3,3,3},
        {3,0,0,3},
        {3,0,0,3},
    },
    {   // frame 1 — legs together (mid-stride)
        {0,1,1,0},
        {0,1,1,0},
        {0,1,1,0},
        {2,2,2,2},
        {2,2,2,2},
        {3,3,3,3},
        {0,3,3,0},
        {3,0,0,3},
    },
};

static const Color PAL[4] = {
    {  0,   0,   0,   0},   // 0  transparent
    {220, 180, 120, 255},   // 1  skin
    { 60, 120, 200, 255},   // 2  shirt
    { 40,  60, 120, 255},   // 3  pants
};

// Solid if out-of-bounds or stone cell.
static int is_solid(const uint8_t *w, int wx, int wy) {
    if (wx < 0 || wx >= WORLD_W) return 1;
    if (wy < 0 || wy >= WORLD_H) return 1;
    uint8_t c = w[wy * WORLD_W + wx];
    return c == CELL_STONE || c == CELL_DIRT;
}

// 1 if the axis-aligned box at (bx,by) size (bw x bh) overlaps any solid cell.
// Uses floor(bx)/floor(by) so a position of 238.9 still only tests column 238.
static int box_solid(const uint8_t *w, float bx, float by, int bw, int bh) {
    int x0 = (int)bx,        x1 = (int)bx + bw - 1;
    int y0 = (int)by,        y1 = (int)by + bh - 1;
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++)
            if (is_solid(w, x, y)) return 1;
    return 0;
}

int main(void)
{
    SetConfigFlags(FLAG_BORDERLESS_WINDOWED_MODE | FLAG_VSYNC_HINT);
    InitWindow(0, 0, "VERDANT F1 — Scaled Canvas");

    // --- WORLD ---
    uint8_t world[WORLD_W * WORLD_H];
    memset(world, CELL_AIR, sizeof(world));
    const int stoneStart = (WORLD_H * 2) / 3;   // row 180
    for (int y = stoneStart; y < WORLD_H; y++)
        memset(&world[y * WORLD_W], CELL_STONE, WORLD_W);

    // 10px dirt topsoil layer sitting on top of stone
    const int dirtStart = stoneStart - 10;      // row 170
    for (int y = dirtStart; y < stoneStart; y++)
        memset(&world[y * WORLD_W], CELL_DIRT, WORLD_W);

    Image     worldImg = GenImageColor(WORLD_W, WORLD_H, BLACK);
    Texture2D worldTex = LoadTextureFromImage(worldImg);
    SetTextureFilter(worldTex, TEXTURE_FILTER_POINT);

    // --- CHARACTER STATE ---
    float cx = (float)((WORLD_W - CHAR_W) / 2);  // centre of world
    float cy = (float)(dirtStart - CHAR_H);        // feet just above dirt layer
    float cvx = 0.0f, cvy = 0.0f;
    int grounded   = 0;
    int facing     = 1;    // 1=right  -1=left
    int anim_frame = 0;
    int anim_timer = 0;

    // --- INVENTORY ---
    int inv_dirt = 0;

    // --- SELECTION & INPUT STATE ---
    int sel_wx     = -1, sel_wy = -1;  // selected cell in world coords (-1 = none)
    int last_mx    = -1, last_my = -1; // previous mouse world pos (mode detection)
    int input_mode = 0;                // 0=KB/M  1=Gamepad

    while (!WindowShouldClose())
    {
        // --- WINDOW ---
        if (IsKeyPressed(KEY_F11))    ToggleBorderlessWindowed();
        if (IsKeyPressed(KEY_ESCAPE)) break;

        int screenW = GetScreenWidth();
        int screenH = GetScreenHeight();
        int scaleX  = screenW / WORLD_W;
        int scaleY  = screenH / WORLD_H;
        int scale   = (scaleX < scaleY) ? scaleX : scaleY;
        if (scale < 1) scale = 1;
        int scaledW = WORLD_W * scale,  scaledH = WORLD_H * scale;
        int offsetX = (screenW - scaledW) / 2;
        int offsetY = (screenH - scaledH) / 2;

        // --- INPUT ---
        int move_left  = IsKeyDown(KEY_A) || IsKeyDown(KEY_LEFT);
        int move_right = IsKeyDown(KEY_D) || IsKeyDown(KEY_RIGHT);
        int do_jump    = IsKeyPressed(KEY_W) || IsKeyPressed(KEY_UP)
                      || IsKeyPressed(KEY_SPACE);

        if (IsGamepadAvailable(0)) {
            float ax = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_X);
            if (ax < -0.3f) move_left  = 1;
            if (ax >  0.3f) move_right = 1;
            if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN)) do_jump = 1;
        }

        // Mouse → world coordinates
        Vector2 mouse = GetMousePosition();
        int mwx = (int)((mouse.x - offsetX) / scale);
        int mwy = (int)((mouse.y - offsetY) / scale);

        // --- PHYSICS ---

        // Ground contact: probe one pixel below feet.
        // Checked before gravity so we don't accumulate downward vel while standing.
        grounded = box_solid(world, cx, cy + 1.0f, CHAR_W, CHAR_H);

        // Gravity — only when airborne
        if (!grounded) {
            cvy += GRAVITY;
            if (cvy > MAX_FALL) cvy = MAX_FALL;
        } else if (cvy > 0.0f) {
            cvy = 0.0f;   // clear any residual downward vel on landing
        }

        // Jump — requires ground contact
        if (do_jump && grounded) {
            cvy      = JUMP_VEL;
            grounded = 0;
        }

        // Horizontal — Terraria-style: instant speed, no acceleration
        cvx = 0.0f;
        if (move_left)  { cvx = -WALK_SPEED; facing = -1; }
        if (move_right) { cvx =  WALK_SPEED; facing =  1; }

        // Apply horizontal (clamped to world edges; wrap comes with camera later)
        if (cvx != 0.0f) {
            float new_x = cx + cvx;
            if (new_x < 0)                 new_x = 0;
            if (new_x + CHAR_W > WORLD_W)  new_x = (float)(WORLD_W - CHAR_W);
            if (!box_solid(world, new_x, cy, CHAR_W, CHAR_H))
                cx = new_x;
            // else: wall, discard horizontal movement silently
        }

        // Apply vertical
        if (cvy != 0.0f) {
            float new_y = cy + cvy;
            if (!box_solid(world, cx, new_y, CHAR_W, CHAR_H)) {
                cy = new_y;
            } else {
                if (cvy > 0.0f) {
                    // Landing: snap feet to integer pixel boundary above stone.
                    // Step down one integer pixel at a time from floor(cy)
                    // so the final position is always an exact pixel.
                    float floor_y = (float)(int)cy;
                    while (!box_solid(world, cx, floor_y + 1.0f, CHAR_W, CHAR_H))
                        floor_y += 1.0f;
                    cy = floor_y;
                    grounded = 1;
                }
                // Ceiling hit (cvy < 0): position unchanged, just kill upward vel
                cvy = 0.0f;
            }
        }

        // Hard clamp to world bounds
        if (cy < 0)                  { cy = 0;                          cvy = 0; }
        if (cy + CHAR_H > WORLD_H)   { cy = (float)(WORLD_H - CHAR_H); cvy = 0; grounded = 1; }

        // Walk animation: toggle frame every 8 ticks while moving
        if (move_left || move_right) {
            if (++anim_timer >= 8) { anim_timer = 0; anim_frame ^= 1; }
        } else {
            anim_frame = 0;
            anim_timer = 0;
        }

        // --- CELL SELECTION & INVENTORY ACTIONS ---

        // Player centre in world coords (radius tests and overlay circle)
        float pcx = cx + CHAR_W * 0.5f;
        float pcy = cy + CHAR_H * 0.5f;
        int   pr2  = PICKUP_RADIUS * PICKUP_RADIUS;

        // Input mode switching: any KB/M activity → 0; any gamepad activity → 1.
        // Both can fire in the same frame — KB/M wins only if we test it last,
        // so we test gamepad first, then KB/M overwrites.
        int mouse_moved = (mwx != last_mx || mwy != last_my);
        last_mx = mwx;  last_my = mwy;

        if (IsGamepadAvailable(0)) {
            float ax = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_X);
            float ay = GetGamepadAxisMovement(0, GAMEPAD_AXIS_LEFT_Y);
            if (ax < -0.3f || ax > 0.3f || ay < -0.3f || ay > 0.3f) input_mode = 1;
            if (IsGamepadButtonPressed(0, GAMEPAD_BUTTON_RIGHT_FACE_DOWN))  input_mode = 1;
        }
        if (mouse_moved || IsMouseButtonPressed(MOUSE_BUTTON_LEFT) ||
                IsMouseButtonPressed(MOUSE_BUTTON_RIGHT) ||
                move_left || move_right || do_jump || GetKeyPressed() != 0)
            input_mode = 0;

        // KB/M: facing follows mouse cursor, not movement direction
        if (input_mode == 0)
            facing = (mwx >= (int)pcx) ? 1 : -1;

        // Cell selection
        if (input_mode == 0) {
            // Mouse mode: cell under cursor, only if within pickup radius
            sel_wx = -1;  sel_wy = -1;
            if (mwx >= 0 && mwx < WORLD_W && mwy >= 0 && mwy < WORLD_H) {
                float dx = mwx - pcx, dy = mwy - pcy;
                if (dx*dx + dy*dy <= (float)pr2)
                    { sel_wx = mwx; sel_wy = mwy; }
            }
        } else {
            // Gamepad mode: nearest CELL_DIRT in front-facing arc within radius
            sel_wx = -1;  sel_wy = -1;
            float best = (float)(pr2 + 1);
            int bx0 = (int)pcx - PICKUP_RADIUS,  bx1 = (int)pcx + PICKUP_RADIUS;
            int by0 = (int)pcy - PICKUP_RADIUS,  by1 = (int)pcy + PICKUP_RADIUS;
            for (int wy = by0; wy <= by1; wy++) {
                for (int wx = bx0; wx <= bx1; wx++) {
                    if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
                    float dx = wx - pcx, dy = wy - pcy;
                    if (dx * facing < 0.0f) continue;   // wrong half-plane
                    float d2 = dx*dx + dy*dy;
                    if (d2 > (float)pr2 || d2 >= best) continue;
                    if (world[wy * WORLD_W + wx] == CELL_DIRT)
                        { best = d2; sel_wx = wx; sel_wy = wy; }
                }
            }
        }

        // Harvest: one press = one cell (LMB or E)
        if (IsMouseButtonPressed(MOUSE_BUTTON_LEFT) || IsKeyPressed(KEY_E)) {
            if (sel_wx >= 0 && inv_dirt < INV_MAX
                    && world[sel_wy * WORLD_W + sel_wx] == CELL_DIRT) {
                world[sel_wy * WORLD_W + sel_wx] = CELL_AIR;
                inv_dirt++;
            }
        }

        // Place: RMB — place CELL_DIRT at mouse world position
        if (IsMouseButtonPressed(MOUSE_BUTTON_RIGHT)) {
            if (mwx >= 0 && mwx < WORLD_W && mwy >= 0 && mwy < WORLD_H) {
                if (world[mwy * WORLD_W + mwx] == CELL_AIR && inv_dirt > 0) {
                    world[mwy * WORLD_W + mwx] = CELL_DIRT;
                    inv_dirt--;
                }
            }
        }

        // --- RENDER WORLD TO PIXEL BUFFER ---
        Color *pixels = worldImg.data;
        for (int i = 0; i < WORLD_W * WORLD_H; i++) {
            if      (world[i] == CELL_STONE) pixels[i] = (Color){128, 128, 128, 255};
            else if (world[i] == CELL_DIRT)  pixels[i] = (Color){139,  90,  43, 255};
            else                             pixels[i] = (Color){255, 255, 255,   0};
        }

        // --- DRAW CHARACTER INTO PIXEL BUFFER ---
        int draw_x = (int)cx;
        int draw_y = (int)cy;
        for (int row = 0; row < CHAR_H; row++) {
            for (int col = 0; col < CHAR_W; col++) {
                // Mirror sprite columns when facing left
                int src_col = (facing < 0) ? (CHAR_W - 1 - col) : col;
                uint8_t idx = SPRITE[anim_frame][row][src_col];
                if (idx == 0) continue;   // transparent pixel
                int wx = draw_x + col;
                int wy = draw_y + row;
                if (wx < 0 || wx >= WORLD_W || wy < 0 || wy >= WORLD_H) continue;
                pixels[wy * WORLD_W + wx] = PAL[idx];
            }
        }

        UpdateTexture(worldTex, pixels);

        // --- SCREEN COMPOSITE ---
        BeginDrawing();
            ClearBackground(BLACK);
            DrawTexturePro(
                worldTex,
                (Rectangle){0, 0, WORLD_W, WORLD_H},
                (Rectangle){(float)offsetX, (float)offsetY, (float)scaledW, (float)scaledH},
                (Vector2){0, 0},
                0.0f,
                WHITE
            );
            // --- WORLD-SPACE OVERLAYS (drawn at screen scale) ---

            // Pickup radius circle around player centre
            DrawCircleLines(
                offsetX + (int)(pcx * scale),
                offsetY + (int)(pcy * scale),
                PICKUP_RADIUS * scale,
                (Color){255, 255, 100, 80}
            );

            // Selected cell: 1px outline only, no fill.
            // Yellow = mouse mode; cyan = gamepad auto-select.
            if (sel_wx >= 0) {
                Color outline = (input_mode == 0)
                    ? (Color){255, 255,  50, 230}   // yellow — mouse
                    : (Color){100, 220, 255, 230};  // cyan  — gamepad
                DrawRectangleLinesEx(
                    (Rectangle){
                        (float)(offsetX + sel_wx * scale),
                        (float)(offsetY + sel_wy * scale),
                        (float)scale, (float)scale
                    },
                    1.0f, outline
                );
            }

            // HUD
            DrawText(TextFormat("Screen: %dx%d  Scale: %dx  World: %dx%d",
                screenW, screenH, scale, WORLD_W, WORLD_H),
                8, 8, 16, GREEN);
            DrawText(TextFormat("Pos: (%.0f, %.0f)  Vel: (%.1f, %.1f)  %s",
                cx, cy, cvx, cvy, grounded ? "GROUNDED" : "AIR"),
                8, 28, 16, YELLOW);
            DrawText(TextFormat("Mouse: (%d, %d)  Dirt: %d/%d",
                mwx, mwy, inv_dirt, INV_MAX),
                8, 48, 16, SKYBLUE);
            DrawText("WASD/Arrows=Move  Space/W=Jump  LMB/E=Dig  RMB=Place  F11=Fullscreen  ESC=Quit",
                8, 68, 16, GRAY);
        EndDrawing();
    }

    UnloadTexture(worldTex);
    UnloadImage(worldImg);
    CloseWindow();
    return 0;
}
