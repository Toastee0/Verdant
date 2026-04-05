#include "blob.h"

// ── Flood fill ────────────────────────────────────────────────────────────────

static int bfs[WORLD_W * WORLD_H];

static void fill_blob(const Cell *cells, uint16_t *blob_id,
                      Blob *blobs, int start, uint16_t id) {
    Blob *b         = &blobs[id];
    b->active       = 1;
    b->dirty        = 0;
    b->volume       = 0;
    b->water_sum    = 0;
    b->sealed       = 1;
    b->min_x        = WORLD_W; b->max_x = 0;
    b->min_y        = WORLD_H; b->max_y = 0;

    // Phase of the seed cell: wet (water > WATER_DAMP) or dry air.
    // Only propagate to neighbours in the same phase so that water bodies
    // and empty chambers become distinct blobs with a pressure interface
    // between them.
    int seed_wet = (cells[start].water > WATER_DAMP);

    int head = 0, tail = 0;
    bfs[tail++]    = start;
    blob_id[start] = id;

    while (head < tail) {
        int idx = bfs[head++];
        int x   = idx % WORLD_W;
        int y   = idx / WORLD_W;

        b->volume++;
        b->water_sum += cells[idx].water;

        if (x < b->min_x) b->min_x = x;
        if (x > b->max_x) b->max_x = x;
        if (y < b->min_y) b->min_y = y;
        if (y > b->max_y) b->max_y = y;

        if (x == 0 || x == WORLD_W - 1 || y == 0 || y == WORLD_H - 1)
            b->sealed = 0;

        int ns[4] = {
            x > 0         ? idx - 1       : -1,
            x < WORLD_W-1 ? idx + 1       : -1,
            y > 0         ? idx - WORLD_W : -1,
            y < WORLD_H-1 ? idx + WORLD_W : -1,
        };
        for (int i = 0; i < 4; i++) {
            int n = ns[i];
            if (n < 0) continue;
            if (blob_id[n] != BLOB_NONE) continue;
            if (CELL_TYPE(cells[n].type) != CELL_AIR) continue;
            // Same phase only.
            int n_wet = (cells[n].water > WATER_DAMP);
            if (n_wet != seed_wet) continue;
            blob_id[n] = id;
            bfs[tail++] = n;
        }
    }

    // gas_pressure: open blobs = atmosphere. Sealed blobs: initialised to ATM
    // here; Boyle's law adjustment happens in blob_pressure_tick once we know
    // current water fill vs. initial volume.
    b->gas_pressure = PRESSURE_ATM;
    if (b->sealed && b->initial_gas_vol == 0.0f)
        b->initial_gas_vol = b->volume - b->water_sum / 255.0f;
}

void blob_init(const Cell *cells, Blob *blobs, uint16_t *blob_id, int *blob_count) {
    memset(blob_id, 0, WORLD_W * WORLD_H * sizeof(uint16_t));
    memset(blobs,   0, MAX_BLOBS * sizeof(Blob));
    *blob_count = 0;

    for (int i = 0; i < WORLD_W * WORLD_H; i++) {
        if (blob_id[i] != BLOB_NONE) continue;
        if (CELL_TYPE(cells[i].type) != CELL_AIR) continue;
        int id = ++(*blob_count);
        if (id >= MAX_BLOBS) break;
        fill_blob(cells, blob_id, blobs, i, (uint16_t)id);
    }
}

void blob_mark_dirty(Blob *blobs, const uint16_t *blob_id, int x, int y) {
    int offsets[5][2] = { {0,0}, {-1,0}, {1,0}, {0,-1}, {0,1} };
    for (int i = 0; i < 5; i++) {
        int cx = x + offsets[i][0], cy = y + offsets[i][1];
        if (cx < 0 || cx >= WORLD_W || cy < 0 || cy >= WORLD_H) continue;
        uint16_t id = blob_id[cy * WORLD_W + cx];
        if (id != BLOB_NONE) blobs[id].dirty = 1;
    }
}

void blob_update(const Cell *cells, Blob *blobs, uint16_t *blob_id, int *blob_count) {
    for (int i = 1; i < MAX_BLOBS; i++) {
        if (blobs[i].active && blobs[i].dirty) {
            blob_init(cells, blobs, blob_id, blob_count);
            return;
        }
    }
}

// ── Velocity helpers ──────────────────────────────────────────────────────────

// Torricelli-ish: velocity magnitude scales with sqrt of pressure-head height.
// dx_dir/dy_dir: unit direction from source to destination (+1/-1/0).
// pop_y: y of the water surface in the source blob's column (< 0 = unknown, skip).
// interface_y: y of the interface cell where water exits.
static void assign_exit_velocity(Cell *cells, int dst_idx,
                                  int pop_y, int interface_y,
                                  int dx_dir, int dy_dir) {
    if (pop_y < 0) return;  // no surface found, can't compute
    int height_delta = interface_y - pop_y;
    if (height_delta < 0) height_delta = -height_delta;
    if (height_delta == 0) return;

    float v_mag = sqrtf((float)height_delta) * VELOCITY_K;
    int v_int   = (int)(v_mag + 0.5f);
    if (v_int > 7) v_int = 7;
    if (v_int < 1) v_int = 1;

    int vx, vy;
    if (dx_dir != 0 && dy_dir != 0) {
        // Diagonal exit: split magnitude equally
        int split = (int)(v_int * 0.7f + 0.5f);
        if (split < 1) split = 1;
        vx = dx_dir * split;
        vy = dy_dir * split;
    } else {
        vx = dx_dir * v_int;
        vy = dy_dir * v_int;
    }

    if (vx >  7) vx =  7;
    if (vx < -8) vx = -8;
    if (vy >  7) vy =  7;
    if (vy < -8) vy = -8;

    cells[dst_idx].vector = vec_encode(vx, vy);
}

// ── Column-scan pressure ──────────────────────────────────────────────────────

// Per-column scratch profile. surface_y == -1 means no water in this column.
typedef struct {
    int   surface_y;
    int   height;         // number of water-bearing cells from surface down
    float pressure_base;  // gas_pressure + 0 (pressure AT surface = gas_pressure)
} ColProfile;

static ColProfile col_profile[WORLD_W];

// Pressure at depth d below the water surface in a column (d=0 = surface cell).
// P = gas_pressure + d * HYDROSTATIC_K
static inline float pressure_at_depth(float gas_p, int depth) {
    return gas_p + (float)depth * HYDROSTATIC_K;
}

// Transfer `amount` units of water away from blob src, popping from the topmost
// water cells first.
static void pop_from_top(Cell *cells, const uint16_t *blob_id,
                         const Blob *b, uint16_t src_id, int amount) {
    int remaining = amount;
    for (int y = b->min_y; y <= b->max_y && remaining > 0; y++) {
        for (int x = b->min_x; x <= b->max_x && remaining > 0; x++) {
            int idx = y * WORLD_W + x;
            if (blob_id[idx] != src_id) continue;
            if (cells[idx].water == 0) continue;
            int take = cells[idx].water < remaining ? cells[idx].water : remaining;
            cells[idx].water -= (uint8_t)take;
            remaining -= take;
        }
    }
}

void blob_pressure_tick(Cell *cells, Blob *blobs,
                        const uint16_t *blob_id, int blob_count) {
    // For each active blob: build column profiles, then scan for interfaces.
    for (int bid = 1; bid <= blob_count; bid++) {
        Blob *ba = &blobs[bid];
        if (!ba->active) continue;

        // ── 1. Column scan for this blob ──────────────────────────────────
        for (int x = ba->min_x; x <= ba->max_x; x++) {
            ColProfile *cp = &col_profile[x];
            cp->surface_y    = -1;
            cp->height       = 0;
            cp->pressure_base = ba->gas_pressure;

            for (int y = ba->min_y; y <= ba->max_y; y++) {
                int idx = y * WORLD_W + x;
                if (blob_id[idx] != (uint16_t)bid) continue;
                if (cells[idx].water < WATER_DAMP) continue;
                if (cells[idx].vector != VEC_ZERO) continue;  // in-flight water doesn't contribute to pressure
                if (cp->surface_y == -1) cp->surface_y = y;
                cp->height++;
            }
        }

        // ── 2. Interface scan — check right and bottom neighbours ─────────
        for (int y = ba->min_y; y <= ba->max_y; y++) {
            for (int x = ba->min_x; x <= ba->max_x; x++) {
                int idx = y * WORLD_W + x;
                if (blob_id[idx] != (uint16_t)bid) continue;

                // Try right neighbour
                if (x + 1 < WORLD_W) {
                    uint16_t nbid = blob_id[y * WORLD_W + (x + 1)];
                    if (nbid != BLOB_NONE && nbid != (uint16_t)bid) {
                        Blob *bb = &blobs[nbid];

                        // Pressure on ba's side at y
                        ColProfile *ca = &col_profile[x];
                        int depth_a = (ca->surface_y >= 0) ? (y - ca->surface_y) : -1;
                        float P_a = (depth_a >= 0)
                            ? pressure_at_depth(ba->gas_pressure, depth_a)
                            : ba->gas_pressure;

                        // Pressure on bb's side at y — need bb's column profile at x+1.
                        // Build it inline (cheap: just scan bb's column at x+1).
                        int nb_surf = -1, nb_h = 0;
                        for (int ny = bb->min_y; ny <= bb->max_y; ny++) {
                            int nidx = ny * WORLD_W + (x + 1);
                            if (blob_id[nidx] != nbid) continue;
                            if (cells[nidx].water < WATER_DAMP) continue;
                            if (nb_surf == -1) nb_surf = ny;
                            nb_h++;
                        }
                        int depth_b = (nb_surf >= 0) ? (y - nb_surf) : -1;
                        float P_b = (depth_b >= 0)
                            ? pressure_at_depth(bb->gas_pressure, depth_b)
                            : bb->gas_pressure;

                        float deltaP = P_a - P_b;
                        if (deltaP > PRESSURE_EPSILON) {
                            // Flow from ba → bb (rightward)
                            int amount = (int)(deltaP * TRANSFER_RATE * 255.0f);
                            if (amount > MAX_TRANSFER_PER_TICK) amount = MAX_TRANSFER_PER_TICK;
                            if (amount > 0) {
                                pop_from_top(cells, blob_id, ba, (uint16_t)bid, amount);
                                int dst = y * WORLD_W + (x + 1);
                                int give = (255 - cells[dst].water) < amount
                                           ? (255 - cells[dst].water) : amount;
                                cells[dst].water += (uint8_t)give;
                                ba->water_sum -= amount;
                                bb->water_sum += give;
                                if (give > 0)
                                    assign_exit_velocity(cells, dst,
                                        col_profile[x].surface_y, y, 1, 0);
                            }
                        } else if (deltaP < -PRESSURE_EPSILON) {
                            // Flow from bb → ba (leftward)
                            int amount = (int)((-deltaP) * TRANSFER_RATE * 255.0f);
                            if (amount > MAX_TRANSFER_PER_TICK) amount = MAX_TRANSFER_PER_TICK;
                            if (amount > 0) {
                                pop_from_top(cells, blob_id, bb, nbid, amount);
                                int dst = y * WORLD_W + x;
                                int give = (255 - cells[dst].water) < amount
                                           ? (255 - cells[dst].water) : amount;
                                cells[dst].water += (uint8_t)give;
                                bb->water_sum -= amount;
                                ba->water_sum += give;
                                if (give > 0)
                                    assign_exit_velocity(cells, dst,
                                        nb_surf, y, -1, 0);
                            }
                        }
                    }
                }

                // Try bottom neighbour (vertical interface: water above air/different blob)
                if (y + 1 < WORLD_H) {
                    uint16_t nbid = blob_id[(y + 1) * WORLD_W + x];
                    if (nbid != BLOB_NONE && nbid != (uint16_t)bid) {
                        // Same pressure logic, vertical interface.
                        // Pressure at y in ba vs. pressure at y+1 in bb.
                        Blob *bb = &blobs[nbid];
                        ColProfile *ca = &col_profile[x];
                        int depth_a = (ca->surface_y >= 0) ? (y - ca->surface_y) : -1;
                        float P_a = (depth_a >= 0)
                            ? pressure_at_depth(ba->gas_pressure, depth_a)
                            : ba->gas_pressure;

                        // Pressure just below the interface in bb
                        int nb_surf = -1;
                        for (int ny = bb->min_y; ny <= bb->max_y; ny++) {
                            int nidx = ny * WORLD_W + x;
                            if (blob_id[nidx] != nbid) continue;
                            if (cells[nidx].water < WATER_DAMP) continue;
                            nb_surf = ny; break;
                        }
                        int depth_b = (nb_surf >= 0) ? ((y + 1) - nb_surf) : -1;
                        float P_b = (depth_b >= 0)
                            ? pressure_at_depth(bb->gas_pressure, depth_b)
                            : bb->gas_pressure;

                        float deltaP = P_a - P_b;
                        if (deltaP > PRESSURE_EPSILON) {
                            int amount = (int)(deltaP * TRANSFER_RATE * 255.0f);
                            if (amount > MAX_TRANSFER_PER_TICK) amount = MAX_TRANSFER_PER_TICK;
                            if (amount > 0) {
                                pop_from_top(cells, blob_id, ba, (uint16_t)bid, amount);
                                int dst = (y + 1) * WORLD_W + x;
                                int give = (255 - cells[dst].water) < amount
                                           ? (255 - cells[dst].water) : amount;
                                cells[dst].water += (uint8_t)give;
                                ba->water_sum -= amount;
                                bb->water_sum += give;
                                if (give > 0)
                                    assign_exit_velocity(cells, dst,
                                        col_profile[x].surface_y, y + 1, 0, 1);
                            }
                        }
                        // Downward flow (ba above, bb below, P_a > P_b) is the only
                        // physically relevant case at a vertical interface — water falls
                        // naturally through gravity; only upward push via horizontal
                        // interfaces matters for communicating vessels.
                    }
                }
            }
        }
    }
}
