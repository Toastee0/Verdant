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

### Pod (flight — Solar Jetman feel)

| Input | Action | Notes |
|---|---|---|
| Left stick | Thrust direction | Angle quantizes to 16 directions for Solar Jetman feel |
| Right trigger | Thrust on/off | Analog — partial pull = less thrust |
| Left trigger | Tow cable | Hold to extend, release to lock/release |
| Right stick | Camera offset | Lets you look ahead without moving |
| A (South) | Primary action | Context: deploy scanner, release payload |
| B (East) | Cancel / abort | Release tow, abort action |
| Y (North) | Switch to tank | Only when landed / near tank |
| Start | Pause menu | |

**16-angle quantization for left stick:**
```rust
fn stick_to_16_angle(x: f32, y: f32) -> Option<u8> {
    let magnitude = (x * x + y * y).sqrt();
    if magnitude < 0.2 { return None; } // deadzone
    let angle_rad = y.atan2(x); // 0 = right, PI/2 = up (note: stick y is inverted)
    let angle_turns = (angle_rad / (2.0 * std::f32::consts::PI)).rem_euclid(1.0);
    Some((angle_turns * 16.0).round() as u8 % 16)
}
```
Returns an index 0-15 into the 16 thrust angle table. `None` = stick in deadzone = no thrust.

### Tank (ground — Scorched Earth feel)

| Input | Action | Notes |
|---|---|---|
| Left stick X | Move left/right | Analog speed |
| Right stick Y | Aim ballistic arm | Up/down adjusts elevation angle |
| Right trigger | Fire | Hold to charge (for Pressure Shot tier) |
| Left trigger | Drill | Hold to drill in current direction |
| D-pad left/right | Cycle payload type | Water → Mud Lob → Freeze → Acid → etc. |
| A (South) | Confirm / interact | Pick up item, activate POI |
| B (East) | Tow cable | Extend/retract |
| Y (North) | Switch to pod | Only when pod is nearby |
| Start | Pause menu | |

### Camera (both vehicles)

The camera follows the active vehicle. Right stick offsets it (look-ahead).
If you want a free camera mode (useful for surveying), hold LB and use right stick.

| Input | Action |
|---|---|
| Right stick (while driving) | Camera look-ahead offset |
| LB + right stick | Free camera pan |
| RB | Reset camera to vehicle |
| D-pad up/down | Zoom in/out |

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
