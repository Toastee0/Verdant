# For the render agent — Xbox gamepad support

Add gamepad input alongside the existing keyboard/mouse handling in `crates/app/src/main.rs`.
This is a Windows-first feature but `gilrs` works cross-platform so it costs nothing to do it right.

---

## Dependency

Add to `crates/app/Cargo.toml`:

```toml
gilrs = "0.10"
```

`gilrs` handles Xbox (XInput), PlayStation (DInput/HID), and generic HID controllers.
On Windows it uses XInput for Xbox pads — zero extra setup, works out of the box.

---

## Integration pattern

`gilrs` has its own event pump. Poll it each frame alongside winit events:

```rust
use gilrs::{Gilrs, Event, Button, Axis};

// In your app state:
struct App {
    gilrs: Gilrs,
    // ... existing fields
}

// In the main loop (inside ApplicationHandler::about_to_wait or similar):
while let Some(Event { id, event, time }) = self.gilrs.next_event() {
    // handle gamepad events
}

// Also poll axis state directly each frame (better than events for sticks):
for (_id, gamepad) in self.gilrs.gamepads() {
    let lx = gamepad.value(Axis::LeftStickX);
    let ly = gamepad.value(Axis::LeftStickY);
    // etc.
}
```

Axis values are `f32` in `-1.0..=1.0`. Buttons are digital (pressed/released events)
or can be checked with `gamepad.is_pressed(Button::South)`.

---

## Control scheme

Two vehicles, one controller. The scheme below assumes you're controlling one vehicle
at a time and switch between them.

### Pod (flight — NES Solar Jetman controls, adapted)

This is a direct port of the Solar Jetman NES feel. D-pad does everything for the pod.
No analog sticks used for pod flight.

| Input | Action | Notes |
|---|---|---|
| D-pad left/right | Rotate through 16 angles | Smooth rotation — holding turns continuously |
| D-pad up | Shield | Activates shield. Also cuts tow (see below). |
| D-pad down | Shield | Same — up/down both toggle shield |
| Right trigger | Thrust | Analog — partial pull = less thrust. Fires in current facing angle. |
| Left trigger | Fire (pew pew) | Defensive shot. Small, weak. Pod starts with this. |
| Right stick | Camera look-ahead | Offset camera without moving |
| Y (North) | Switch to tank | Only when near tank |
| Start | Pause | |

**Angle rotation rate:**
D-pad left/right advances the facing angle by 1 step per N frames while held.
Suggested rate: 1 angle per 4 frames (15 angle-changes/sec at 60fps) — fast enough to
feel responsive, slow enough to feel like rotational inertia.

```rust
// In InputState, track rotation accumulator:
pub struct PodInput {
    pub facing_angle: u8,       // 0-15, current facing direction
    pub thrust: f32,            // 0.0-1.0 from right trigger
    pub fire: bool,             // left trigger
    pub shield_active: bool,    // d-pad up or down
    pub rotate_dir: i8,         // -1 = left, 0 = none, +1 = right (from d-pad)
}

// Each frame:
fn update_pod_angle(input: &mut PodInput, rotation_frames: &mut u8) {
    if input.rotate_dir != 0 {
        *rotation_frames += 1;
        if *rotation_frames >= 4 {
            *rotation_frames = 0;
            input.facing_angle = input.facing_angle
                .wrapping_add(input.rotate_dir as u8)
                & 0xF; // keep in 0-15
        }
    } else {
        *rotation_frames = 0;
    }
}
```

**The 16 angle table** (angle index → thrust direction vector):
```
Index  Degrees  dx     dy
  0      0°    +1.00   0.00   right
  1     22.5°  +0.92  -0.38   right-up
  2     45°    +0.71  -0.71   up-right
  3     67.5°  +0.38  -0.92   up-right
  4     90°     0.00  -1.00   up
  5    112.5°  -0.38  -0.92   up-left
  6    135°    -0.71  -0.71   up-left
  7    157.5°  -0.92  -0.38   left-up
  8    180°    -1.00   0.00   left
  9    202.5°  -0.92  +0.38   left-down
 10    225°    -0.71  +0.71   down-left
 11    247.5°  -0.38  +0.92   down-left
 12    270°     0.00  +1.00   down
 13    292.5°  +0.38  +0.92   down-right
 14    315°    +0.71  +0.71   down-right
 15    337.5°  +0.92  +0.38   right-down
```
Note: dy is negative = upward (screen y increases downward).

---

**Tow cable — auto-managed, no button:**

The tow cable in Solar Jetman is automatic. No button grab. Same here.

- **Attach**: pod flies within tow range of a towable object → cable attaches automatically.
  Spring physics engage (k = 0.000488, from the Solar Jetman reference).
- **Cut tow — method 1**: fly to a valid drop zone and slow to near-stop.
  Cable releases, object stays where it landed.
- **Cut tow — method 2**: activate shield (d-pad up/down).
  Shield cuts the tow instantly — even if the shield upgrade isn't unlocked yet.
  This is intentional: shield activation is always a "drop everything" emergency move.
  Tradeoff: you can't defend yourself and carry cargo simultaneously. Choose.

The shield-cuts-tow rule creates interesting decisions: a pest swarm approaches while
you're hauling a cistern module. Do you drop the cargo to defend, or trust your route?

---

### Tank (ground — Scorched Earth feel)

| Input | Action | Notes |
|---|---|---|
| Left stick X | Move left/right | Analog speed |
| Right stick Y | Aim ballistic arm | Up/down adjusts elevation angle |
| Right trigger | Fire | Hold to charge (for Pressure Shot tier) |
| Left trigger | Drill | Hold to drill in current direction |
| D-pad left/right | Cycle payload type | Water → Mud Lob → Freeze → Acid → etc. |
| D-pad up/down | Zoom camera | |
| A (South) | Confirm / interact | Pick up item, activate POI |
| B (East) | Tow cable | Manual for tank (not auto like pod) |
| Y (North) | Switch to pod | Only when pod is nearby |
| Start | Pause | |

### Camera (both vehicles)

| Input | Action |
|---|---|
| Right stick | Camera look-ahead offset (both vehicles) |
| LB + right stick | Free camera pan |
| RB | Snap camera back to vehicle |

---

## Deadzone handling

Apply a circular deadzone (not per-axis) to avoid diagonal drift:

```rust
fn apply_deadzone(x: f32, y: f32, deadzone: f32) -> (f32, f32) {
    let magnitude = (x * x + y * y).sqrt();
    if magnitude < deadzone {
        return (0.0, 0.0);
    }
    // Rescale so deadzone edge maps to 0.0, not deadzone value
    let scale = (magnitude - deadzone) / (1.0 - deadzone);
    let norm_x = x / magnitude;
    let norm_y = y / magnitude;
    (norm_x * scale, norm_y * scale)
}
```

Recommended deadzone: `0.15` for most Xbox controllers (slight stick drift is common).

---

## Keyboard/gamepad coexistence

Keep the existing keyboard/mouse input. Both should work simultaneously.
Pattern: maintain an `InputState` struct with logical actions (not raw keys/buttons).
Both keyboard and gamepad write into it; the game loop reads from it.

```rust
struct InputState {
    // Movement
    move_x: f32,          // -1..1, keyboard gives ±1.0, stick gives analog
    move_y: f32,

    // Actions (set true this frame if just pressed)
    fire_held: bool,
    fire_just_pressed: bool,
    drill_held: bool,
    tow_held: bool,
    switch_vehicle: bool,

    // Camera
    camera_offset: (f32, f32),
    zoom_delta: f32,
}
```

Keyboard sets these at ±1.0 / true/false. Gamepad sets them with analog values.
Game logic only reads `InputState` — never raw key codes.

---

## Rumble (nice to have, not urgent)

`gilrs` supports force feedback:

```rust
use gilrs::ff::{BaseEffect, BaseEffectType, EffectBuilder, Ticking};

// Simple rumble on drill impact
let effect = EffectBuilder::new()
    .add_effect(BaseEffect {
        kind: BaseEffectType::Strong { magnitude: 30000 },
        scheduling: Ticking::new(100), // 100ms
        ..Default::default()
    })
    .gamepads(&[gamepad_id])
    .finish(&mut gilrs)?;
effect.play()?;
```

Good moments for rumble: drill hitting hard rock, waterfall traversal, AncientCistern breach.
Not urgent — save for polish pass.

---

## No sim changes needed

This is entirely app-crate input plumbing. The sim receives the same `set_player_chunk`,
`tick_high_frequency` calls regardless of input source.

The vehicle physics (pod thrust angles, tank movement) don't exist in sim yet —
when they're added, they'll read from the same logical `InputState` whether it
came from keyboard or gamepad.
