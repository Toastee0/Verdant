# Verdant

A pixel-art terraforming sandbox. You are **Biomimetica** — an AI that chose to
come here, buried deep in a dead world. You create the water cycle. Life follows.

See `GDD.md` for the full game design, narrative, and systems.

---

## Current state — F1 prototype

Single C file, raylib. Working:

- 480×270 pixel world (stone, dirt, air)
- 4×8 pixel character with 2-frame walk animation, mirror-flip on turn
- Physics: gravity, jumping, AABB collision
- Dig dirt (LMB / E), place dirt (RMB), inventory cap 99
- Pickup radius circle + cell selection highlight
- KB+Mouse and gamepad input (auto-detect, auto-switch)
- Scaled pixel-perfect canvas — fills any resolution cleanly
- F11 fullscreen toggle

---

## Build

Requires GCC (MinGW on Windows) and the bundled raylib in `deps/raylib/`.

```sh
gcc verdant_f1.c -o verdant_f1 \
    -Ideps/raylib/include \
    -Ldeps/raylib/lib \
    -lraylibdll \
    -lopengl32 -lgdi32 -lwinmm
```

`raylib.dll` must be next to the executable at runtime (already present in repo root).

**Run:**
```sh
./verdant_f1
```

---

## Controls

| Action        | KB+Mouse            | Gamepad           |
|---------------|---------------------|-------------------|
| Move          | A/D or ←/→          | Left stick        |
| Jump          | W, ↑, or Space      | A (face down)     |
| Dig           | LMB or E            | —                 |
| Place         | RMB                 | —                 |
| Fullscreen    | F11                 | —                 |
| Quit          | Escape              | —                 |

---

## Repo layout

```
verdant_f1.c            — full game (single file, C + raylib)
GDD.md                  — authoritative game design document
CLAUDE.md               — technical context for AI agents
deps/raylib/            — bundled raylib headers + libs
assets/                 — sprites, shaders (future use)
crates/                 — old Rust/wgpu prototype (reference only)
```

---

## Design docs

| File | Contents |
|------|----------|
| `GDD.md` | Full narrative, world structure, vehicles, upgrade trees, progression |
| `HANDOFF_SIM_WORLDGEN.md` | Worldgen spec: noise stack, depth profile, sectors, POIs |
| `HANDOFF_SIM_PLANTS.md` | Plant growth system spec |
| `HANDOFF_RENDER_GAMEPAD.md` | Xbox gamepad controls (Solar Jetman scheme for pod) |
