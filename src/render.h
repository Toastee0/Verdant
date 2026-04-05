#pragma once
#include "defs.h"
#include "player.h"
#include "rover.h"
#include "rover_arm.h"

// ── Pixel buffer phase (called before UpdateTexture) ────────────────────────

// Write all terrain cells to the pixel buffer.
void render_world_to_pixels(Color *pixels, const Cell *cells);

// Overwrite water cells with a pressure heat map (blue=low, red=high).
// Call after render_world_to_pixels when debug overlay is active.
void render_pressure_overlay(Color *pixels, const Cell *cells,
                             const uint16_t *blob_id, const Blob *blobs);

// Write player sprite into the pixel buffer (skip when in_rover).
void render_player_to_pixels(Color *pixels, const PlayerState *p);

// Write rover sprite (slope-sheared) into the pixel buffer.
// Also draws the arm line when in_rover, and the projectile dot when active.
void render_rover_to_pixels(Color *pixels, const Cell *cells,
                             const RoverState *r, const ArmState *a,
                             const ProjState *proj);

// ── Screen-space phase (called between BeginDrawing and EndDrawing) ─────────

// All screen-space overlays: cell selection outline, trajectory arc,
// power bar, contextual prompts, HUD text, debug overlay.
// sel_wx/wy: highlighted cell (-1 = none)
// near_rover: player is within entry range but not in rover
void render_screen_overlay(const PlayerState *p, const RoverState *r,
                            const ArmState *a, const ProjState *proj,
                            const Cell *cells,
                            int sel_wx, int sel_wy,
                            int show_debug, int near_rover, int input_mode,
                            int offsetX, int offsetY,
                            int scaledW, int scaledH, int scale);
