#include "raylib.h"
#include <stdint.h>
#include <string.h>

// === WORLD CONSTANTS ===
#define WORLD_W 480
#define WORLD_H 270

// === PIXEL TYPES ===
#define CELL_AIR   0
#define CELL_STONE 1

int main(void)
{
    // --- INIT ---
    // Start borderless fullscreen — raylib sizes the window to match the monitor.
    // GetScreenWidth/Height will reflect the real monitor resolution after init.
    SetConfigFlags(FLAG_BORDERLESS_WINDOWED_MODE | FLAG_VSYNC_HINT);
    InitWindow(0, 0, "VERDANT F1 — Scaled Canvas");

    // --- WORLD STATE ---
    // 0 = Air, 1 = Stone. Static array; no heap needed at this scale.
    uint8_t world[WORLD_W * WORLD_H];
    memset(world, CELL_AIR, sizeof(world));

    // Fill bottom third with stone
    int stoneStart = (WORLD_H * 2) / 3;
    for (int y = stoneStart; y < WORLD_H; y++)
        memset(&world[y * WORLD_W], CELL_STONE, WORLD_W);

    Image worldImg = GenImageColor(WORLD_W, WORLD_H, BLACK);
    Texture2D worldTex = LoadTextureFromImage(worldImg);
    SetTextureFilter(worldTex, TEXTURE_FILTER_POINT);

    while (!WindowShouldClose())
    {
        if (IsKeyPressed(KEY_F11)) ToggleBorderlessWindowed();
        if (IsKeyPressed(KEY_ESCAPE)) break;

        int screenW = GetScreenWidth();
        int screenH = GetScreenHeight();

        int scaleX = screenW / WORLD_W;
        int scaleY = screenH / WORLD_H;
        int scale = scaleX < scaleY ? scaleX : scaleY;
        if (scale < 1) scale = 1;

        int scaledW = WORLD_W * scale;
        int scaledH = WORLD_H * scale;
        int offsetX = (screenW - scaledW) / 2;
        int offsetY = (screenH - scaledH) / 2;

        Color *pixels = worldImg.data;

        // Map world state to pixel colors
        for (int i = 0; i < WORLD_W * WORLD_H; i++) {
            switch (world[i]) {
                case CELL_STONE: pixels[i] = (Color){128, 128, 128, 255}; break;
                case CELL_AIR:   // fall through
                default:         pixels[i] = (Color){255, 255, 255,   0}; break;
            }
        }

        UpdateTexture(worldTex, pixels);

        BeginDrawing();
            ClearBackground(BLACK);
            DrawTexturePro(
                worldTex,
                (Rectangle){0, 0, WORLD_W, WORLD_H},
                (Rectangle){offsetX, offsetY, scaledW, scaledH},
                (Vector2){0, 0},
                0.0f,
                WHITE
            );
            DrawText(TextFormat("Screen: %dx%d  Scale: %dx  World: %dx%d",
                screenW, screenH, scale, WORLD_W, WORLD_H),
                8, 8, 16, GREEN);
            DrawText("F11=Borderless/Windowed  ESC=Quit", 8, 28, 16, GRAY);
        EndDrawing();
    }

    UnloadTexture(worldTex);
    UnloadImage(worldImg);
    CloseWindow();
    return 0;
}
