#include "water.h"

// Horizontal drag: shrink v toward zero by DRAG_FACTOR_NUM/DRAG_FACTOR_DEN (≈7/8).
// Rounding ensures small values always decay to 0 rather than stalling at 1.
static inline int apply_drag(int v) {
    if (v > 0) {
        v = v - (v + DRAG_FACTOR_DEN - 1) / DRAG_FACTOR_DEN;
        if (v < 0) v = 0;
    } else if (v < 0) {
        v = v + (-v + DRAG_FACTOR_DEN - 1) / DRAG_FACTOR_DEN;
        if (v > 0) v = 0;
    }
    return v;
}

// CA-only water tick: gravity + equalization within blobs.
// Cross-blob pressure transfers are handled by blob_pressure_tick() in blob.c.
void tick_water(Cell *cells, int bias) {

    // === Pass 0: Vectored cell movement (ballistic flight) ===
    // Cells with nonzero vector are "in flight" — they move ballistically,
    // skipping normal gravity/equalization. Scan top-to-bottom so upward-moving
    // water is processed before the destination cell it will land in.
    for (int y = 0; y < WORLD_H; y++) {
        for (int x = 0; x < WORLD_W; x++) {
            int idx = y * WORLD_W + x;
            if (cells[idx].vector == VEC_ZERO) continue;
            // Clear stale vectors on solid cells or dry cells
            if (CELL_TYPE(cells[idx].type) != CELL_AIR) {
                cells[idx].vector = VEC_ZERO;
                continue;
            }
            if (cells[idx].water == 0) {
                cells[idx].vector = VEC_ZERO;
                continue;
            }

            int vdx = vec_dx(cells[idx].vector);
            int vdy = vec_dy(cells[idx].vector);

            // Step along the vector one cell at a time (Bresenham-style),
            // stopping at the first solid or full-water cell.
            int steps = (vdx < 0 ? -vdx : vdx) > (vdy < 0 ? -vdy : vdy)
                        ? (vdx < 0 ? -vdx : vdx)
                        : (vdy < 0 ? -vdy : vdy);
            if (steps == 0) { cells[idx].vector = VEC_ZERO; continue; }

            int final_tx = x, final_ty = y;
            int hit_wall = 0;
            float cx = (float)x, cy = (float)y;
            float step_x = (float)vdx / steps;
            float step_y = (float)vdy / steps;

            for (int s = 0; s < steps; s++) {
                cx += step_x;
                cy += step_y;
                int check_x = (int)(cx + 0.5f);
                int check_y = (int)(cy + 0.5f);

                if (check_x < 0 || check_x >= WORLD_W ||
                    check_y < 0 || check_y >= WORLD_H) {
                    hit_wall = 1; break;
                }

                int check_idx = check_y * WORLD_W + check_x;
                if (CELL_TYPE(cells[check_idx].type) != CELL_AIR) {
                    hit_wall = 1; break;
                }
                if (cells[check_idx].water >= 250) {
                    // Entering a water body — deposit here, stop
                    final_tx = check_x;
                    final_ty = check_y;
                    hit_wall = 1; break;
                }
                final_tx = check_x;
                final_ty = check_y;
            }

            if (final_tx != x || final_ty != y) {
                int final_idx = final_ty * WORLD_W + final_tx;
                int space = 255 - (int)cells[final_idx].water;
                int move  = (int)cells[idx].water < space ? (int)cells[idx].water : space;

                if (move > 0) {
                    cells[final_idx].water += (uint8_t)move;
                    cells[idx].water       -= (uint8_t)move;

                    if (hit_wall) {
                        cells[final_idx].vector = VEC_ZERO;
                    } else {
                        // Still in flight: horizontal drag, constant downward gravity
                        vdx = apply_drag(vdx);
                        vdy = vdy + GRAVITY_TICK;
                        if (vdx >  7) vdx =  7;
                        if (vdx < -8) vdx = -8;
                        if (vdy >  7) vdy =  7;
                        if (vdy < -8) vdy = -8;
                        cells[final_idx].vector = (vdx == 0 && vdy == 0)
                            ? VEC_ZERO
                            : vec_encode(vdx, vdy);
                    }
                } else {
                    // No room — kill velocity
                    cells[idx].vector = VEC_ZERO;
                }
            } else {
                // Blocked immediately — kill velocity
                cells[idx].vector = VEC_ZERO;
            }
        }
    }

    // === Pass 1: Gravity + equalization ===
    // Only processes cells at rest (vector == VEC_ZERO). In-flight water skips this.
    for (int y = WORLD_H - 2; y >= 0; y--) {
        for (int xi = 0; xi < WORLD_W; xi++) {
            int x   = bias ? xi : (WORLD_W - 1 - xi);
            int idx = y * WORLD_W + x;

            if (CELL_TYPE(cells[idx].type) != CELL_AIR) continue;
            if (cells[idx].water == 0) continue;
            if (cells[idx].vector != VEC_ZERO) continue;  // skip in-flight water

            // Gravity: fall into the cell below as much as will fit.
            int below = (y + 1) * WORLD_W + x;
            if (CELL_TYPE(cells[below].type) == CELL_AIR &&
                cells[below].vector == VEC_ZERO) {
                int space = 255 - (int)cells[below].water;
                int move  = (int)cells[idx].water < space ? (int)cells[idx].water : space;
                cells[below].water += (uint8_t)move;
                cells[idx].water   -= (uint8_t)move;
                if (cells[idx].water == 0) continue;
            }

            // Equalization: halve diff with each horizontal neighbour.
            int dx0 = bias ? -1 :  1;
            int dx1 = bias ?  1 : -1;
            for (int pass = 0; pass < 2; pass++) {
                int nx = x + (pass == 0 ? dx0 : dx1);
                if (nx < 0 || nx >= WORLD_W) continue;
                int nidx = y * WORLD_W + nx;
                if (CELL_TYPE(cells[nidx].type) != CELL_AIR) continue;
                if (cells[nidx].vector != VEC_ZERO) continue;  // don't equalize with in-flight
                int diff = (int)cells[idx].water - (int)cells[nidx].water;
                if (diff > 1) {
                    uint8_t t         = (uint8_t)(diff / 2);
                    cells[idx].water  -= t;
                    cells[nidx].water += t;
                }
            }
        }
    }
}

void unstick(Cell *cells, int x, int y) {
    if (x < 0 || x >= WORLD_W || y < 0 || y >= WORLD_H) return;
    if (CELL_TYPE(cells[y * WORLD_W + x].type) == CELL_DIRT)
        cells[y * WORLD_W + x].type &= ~FLAG_STICKY;
}
