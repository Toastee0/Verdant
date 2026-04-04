#include "blob.h"

// BFS queue — large enough for the entire world.
static int bfs[WORLD_W * WORLD_H];

static void fill_blob(const Cell *cells, uint16_t *blob_id,
                      Blob *blobs, int start, uint16_t id) {
    Blob *b    = &blobs[id];
    b->active  = 1;
    b->dirty   = 0;
    b->volume  = 0;
    b->water_sum = 0;
    b->sealed  = 1;   // assume sealed until we touch the world boundary

    int head = 0, tail = 0;
    bfs[tail++]    = start;
    blob_id[start] = id;

    while (head < tail) {
        int idx = bfs[head++];
        int x   = idx % WORLD_W;
        int y   = idx / WORLD_W;

        b->volume++;
        b->water_sum += cells[idx].water;

        // Any cell touching the world border means open to atmosphere.
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
            blob_id[n] = id;
            bfs[tail++] = n;
        }
    }
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
