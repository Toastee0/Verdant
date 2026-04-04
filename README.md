# Verdant

A pixel-art terraforming sandbox. You are **Biomimetica** — an AI that chose to
come here, buried deep in a dead world. You create the water cycle. Life follows.

See `Archive/GDD.md` for the full game design, narrative, and systems.

---

## Current state — F1 prototype

Multi-file C, raylib. Working:

- 480×270 pixel world (stone, dirt, water, one-way platforms, sticky-dirt stalactites)
- 4×8 pixel character: gravity, jumping, coyote time, platform fall-through, step-up
- Dig dirt (LMB / E), place dirt (RMB), inventory cap 99
- Rover vehicle (24×16): enter/exit (F), drive (A/D), handbrake (P), slope shear
- Ballistic arm: aim (arrows), charge power (hold), fire (Space), 3 ammo types (Tab)
- Trajectory preview arc
- Water simulation: falls, spreads, communicating vessels
- Dirt simulation: sand-fall, falls through water, FLAG_STICKY adhesion
- KB+Mouse and gamepad input (auto-detect, auto-switch)
- Scaled pixel-perfect canvas — fills any resolution cleanly
- F11 fullscreen, backtick debug overlay

---

## Build

Requires GCC (MinGW on Windows) and the bundled raylib in `deps/raylib/`.
`raylib.dll` must be next to the executable at runtime (already in repo root).

```sh
make          # build verdant_f1.exe
make clean    # remove executable
```

**Run:**
```sh
./verdant_f1.exe
```

---

## Controls

### On foot

| Action            | KB+Mouse       | Gamepad      |
|-------------------|----------------|--------------|
| Move              | A/D            | Left stick   |
| Jump              | W / Space / ↑  | A button     |
| Fall through platform | S / ↓      | Left stick ↓ |
| Dig               | LMB or E       | —            |
| Place             | RMB            | —            |
| Enter rover       | F (near rover) | X button     |

### In rover

| Action            | KB             | Gamepad       |
|-------------------|----------------|---------------|
| Drive             | A / D          | Left stick    |
| Brake             | S              | —             |
| Handbrake         | P              | —             |
| Aim arm           | ← / →          | Right stick X |
| Adjust power      | ↑ / ↓          | Right stick Y |
| Fire              | Space          | A button / RT |
| Cycle ammo        | Tab / Q        | —             |
| Exit rover        | F              | X button      |

### Global

| Action       | Key     |
|--------------|---------|
| Fullscreen   | F11     |
| Debug overlay| ` (backtick) |
| Quit         | Escape  |

---

## Repo layout

```
src/                    — all C source (see src/INTERFACES.md for function reference)
  main.c                — game loop entry point
  defs.h                — all constants
  sim/                  — dirt, water, impact simulations
  INTERFACES.md         — master function/struct reference (keep updated!)
Makefile
deps/raylib/            — bundled raylib headers + libs
assets/                 — sprites, shaders (future use)
Archive/                — design docs + old versions
  GDD.md                — authoritative game design document
  HANDOFF_SIM_WORLDGEN.md
  HANDOFF_SIM_PLANTS.md
  HANDOFF_RENDER_GAMEPAD.md
crates/                 — old Rust/wgpu prototype (reference only)
```
