// input.rs — unified input system for keyboard, mouse, and gamepad
//
// Pattern: both keyboard and gamepad write into a shared InputState struct.
// The game loop reads only InputState — never raw key codes or button IDs.
// This means all gameplay code is input-source-agnostic.
//
// Two vehicles, one controller. ActiveVehicle determines which input struct
// is active. Press Y (North) to switch when near the other vehicle.

use gilrs::{Axis, Button, Gilrs, Event as GilrsEvent};
use winit::keyboard::KeyCode;

// ── 16-angle thrust table (Solar Jetman) ──────────────────────────────────────
//
// The pod rotates through 16 discrete angles (22.5° each).
// Index 0 = right (0°), index 4 = up (90°), etc.
// dy is negative = upward (screen y increases downward).

/// (dx, dy) unit vectors for the 16 thrust directions.
/// Indexed by `PodInput::facing_angle` (0..15).
#[allow(dead_code)] // used when pod physics come online
pub const ANGLE_TABLE: [(f32, f32); 16] = [
    ( 1.000,  0.000),  //  0:   0°   right
    ( 0.924, -0.383),  //  1:  22.5° right-up
    ( 0.707, -0.707),  //  2:  45°   up-right
    ( 0.383, -0.924),  //  3:  67.5° up-right
    ( 0.000, -1.000),  //  4:  90°   up
    (-0.383, -0.924),  //  5: 112.5° up-left
    (-0.707, -0.707),  //  6: 135°   up-left
    (-0.924, -0.383),  //  7: 157.5° left-up
    (-1.000,  0.000),  //  8: 180°   left
    (-0.924,  0.383),  //  9: 202.5° left-down
    (-0.707,  0.707),  // 10: 225°   down-left
    (-0.383,  0.924),  // 11: 247.5° down-left
    ( 0.000,  1.000),  // 12: 270°   down
    ( 0.383,  0.924),  // 13: 292.5° down-right
    ( 0.707,  0.707),  // 14: 315°   down-right
    ( 0.924,  0.383),  // 15: 337.5° right-down
];

// ── Vehicle types ─────────────────────────────────────────────────────────────

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ActiveVehicle {
    Pod,
    Tank,
}

// ── Pod input (Solar Jetman NES feel) ─────────────────────────────────────────

pub struct PodInput {
    /// Current facing direction (0–15). Index into ANGLE_TABLE.
    pub facing_angle: u8,

    /// Rotation direction from d-pad: -1 = left, 0 = none, +1 = right.
    pub rotate_dir: i8,

    /// Frames accumulated since last angle step. When this reaches
    /// ROTATION_FRAMES_PER_STEP, the angle advances by 1 and this resets.
    pub rotation_accumulator: u8,

    /// Thrust magnitude from right trigger (0.0 = none, 1.0 = full).
    pub thrust: f32,

    /// Fire (left trigger). True = firing this frame.
    pub fire: bool,

    /// Shield active (d-pad up or down). Also cuts tow cable.
    pub shield_active: bool,
}

/// How many frames between angle steps when d-pad is held.
/// 4 frames at 60fps = 15 angle-changes/second. Fast but feels like inertia.
pub const ROTATION_FRAMES_PER_STEP: u8 = 4;

impl PodInput {
    pub fn new() -> PodInput {
        PodInput {
            facing_angle: 0,
            rotate_dir: 0,
            rotation_accumulator: 0,
            thrust: 0.0,
            fire: false,
            shield_active: false,
        }
    }

    /// Advance the facing angle based on held rotation direction.
    /// Call once per frame.
    pub fn tick_rotation(&mut self) {
        if self.rotate_dir != 0 {
            self.rotation_accumulator += 1;
            if self.rotation_accumulator >= ROTATION_FRAMES_PER_STEP {
                self.rotation_accumulator = 0;
                // wrapping_add with mask keeps angle in 0..15.
                // In C: facing = (facing + dir) & 0xF
                self.facing_angle = self.facing_angle
                    .wrapping_add(self.rotate_dir as u8)
                    & 0xF;
            }
        } else {
            self.rotation_accumulator = 0;
        }
    }

    /// Get the thrust vector for the current facing angle, scaled by thrust magnitude.
    #[allow(dead_code)] // used when pod physics come online
    pub fn thrust_vector(&self) -> (f32, f32) {
        let (dx, dy) = ANGLE_TABLE[self.facing_angle as usize];
        (dx * self.thrust, dy * self.thrust)
    }
}

impl Default for PodInput {
    fn default() -> Self { Self::new() }
}

// ── Tank input (Scorched Earth feel) ──────────────────────────────────────────

pub struct TankInput {
    /// Horizontal movement (-1.0 = full left, +1.0 = full right).
    /// Keyboard gives ±1.0, stick gives analog.
    pub move_x: f32,

    /// Ballistic arm elevation angle. Controlled by right stick Y.
    /// Range: 0.0 (horizontal) to 1.0 (vertical up).
    pub aim_elevation: f32,

    /// Fire (right trigger). Hold to charge for Pressure Shot tier.
    pub fire_held: bool,

    /// Drill (left trigger). Hold in current direction.
    pub drill_held: bool,

    /// Payload cycle: -1 = previous, 0 = none, +1 = next.
    pub payload_cycle: i8,

    /// Tow cable toggle (B button). Manual for tank.
    pub tow_toggle: bool,

    /// Interact/confirm (A button).
    pub interact: bool,
}

impl TankInput {
    pub fn new() -> TankInput {
        TankInput {
            move_x: 0.0,
            aim_elevation: 0.5, // start at 45°
            fire_held: false,
            drill_held: false,
            payload_cycle: 0,
            tow_toggle: false,
            interact: false,
        }
    }
}

impl Default for TankInput {
    fn default() -> Self { Self::new() }
}

// ── Walker input ──────────────────────────────────────────────────────────────

/// Input slice consumed by the walker entity each frame.
///
/// Filled from keyboard (arrows/space) and gamepad (left stick X + A).
/// Independent of active_vehicle — the walker always accepts input so the
/// player can move around regardless of which vehicle they're operating.
pub struct WalkerInput {
    /// Horizontal move: -1.0 = full left, 0.0 = stopped, 1.0 = full right.
    pub move_x: f32,

    /// Set true on the single frame that jump is pressed. Cleared in begin_frame().
    /// This is a "just pressed" flag — holding the key does NOT re-trigger a jump.
    pub jump_just_pressed: bool,
}

// ── Unified InputState ────────────────────────────────────────────────────────
//
// The game loop reads this. Both keyboard and gamepad write into it.

pub struct InputState {
    pub active_vehicle: ActiveVehicle,
    pub pod:  PodInput,
    pub tank: TankInput,

    /// Walker input — always populated regardless of active_vehicle.
    pub walker: WalkerInput,

    /// Camera look-ahead offset from right stick (both vehicles).
    /// In world-pixel units, scaled by some look-ahead distance.
    pub camera_offset_x: f32,
    pub camera_offset_y: f32,

    /// Zoom delta this frame (from d-pad up/down in tank, or +/- keys).
    pub zoom_delta: f32,

    /// Request to switch vehicle (Y / North button, just pressed).
    pub switch_vehicle_pressed: bool,

    /// Pause requested (Start button or Escape).
    pub pause_pressed: bool,

    // ── Internal keyboard tracking ────────────────────────────────────────
    // Track which keys are currently held for continuous input.
    // Gamepad axes are polled directly each frame so they don't need this.
    keys_held: KeysHeld,
}

/// Track held state for keys that produce continuous input.
#[derive(Default)]
struct KeysHeld {
    up: bool,
    down: bool,
    left: bool,
    right: bool,
    fire: bool,
    drill: bool,
}

/// Recommended deadzone for Xbox controllers. Common slight stick drift.
const DEADZONE: f32 = 0.15;

impl InputState {
    pub fn new() -> InputState {
        InputState {
            active_vehicle: ActiveVehicle::Pod,
            pod:  PodInput::new(),
            tank: TankInput::new(),
            walker: WalkerInput { move_x: 0.0, jump_just_pressed: false },
            camera_offset_x: 0.0,
            camera_offset_y: 0.0,
            zoom_delta: 0.0,
            switch_vehicle_pressed: false,
            pause_pressed: false,
            keys_held: KeysHeld::default(),
        }
    }

    /// Clear per-frame transient flags. Call at the start of each frame
    /// before processing input events.
    pub fn begin_frame(&mut self) {
        self.switch_vehicle_pressed = false;
        self.pause_pressed = false;
        self.zoom_delta = 0.0;
        self.pod.fire = false;
        self.pod.shield_active = false;
        self.tank.payload_cycle = 0;
        self.tank.tow_toggle = false;
        self.tank.interact = false;
        // Walker jump is a "just pressed" flag — clear it at frame start so
        // holding the key doesn't keep triggering jumps.
        self.walker.jump_just_pressed = false;
    }

    // ── Keyboard input ────────────────────────────────────────────────────

    /// Process a key press/release. Maps raw keys to logical actions.
    pub fn handle_key(&mut self, key: KeyCode, pressed: bool) {
        match key {
            KeyCode::KeyW | KeyCode::ArrowUp    => self.keys_held.up    = pressed,
            KeyCode::KeyS | KeyCode::ArrowDown  => self.keys_held.down  = pressed,
            KeyCode::KeyA | KeyCode::ArrowLeft  => self.keys_held.left  = pressed,
            KeyCode::KeyD | KeyCode::ArrowRight => self.keys_held.right = pressed,
            KeyCode::Space => {
                self.keys_held.fire = pressed;
                // Walker jump fires on the press edge only, not while held.
                // begin_frame() clears jump_just_pressed each frame, so this
                // single assignment is the entire "just pressed" mechanism.
                if pressed {
                    self.walker.jump_just_pressed = true;
                }
            }
            KeyCode::KeyE                       => self.keys_held.drill = pressed,
            KeyCode::KeyQ if pressed            => self.switch_vehicle_pressed = true,
            KeyCode::Escape if pressed          => self.pause_pressed = true,
            KeyCode::Equal | KeyCode::NumpadAdd      if pressed => self.zoom_delta += 1.0,
            KeyCode::Minus | KeyCode::NumpadSubtract if pressed => self.zoom_delta -= 1.0,
            _ => {}
        }
    }

    /// Apply held keyboard state to the active vehicle's input.
    /// Call once per frame after all key events have been processed.
    pub fn apply_keyboard_held(&mut self) {
        // Walker move_x is always set from left/right keys — independent of
        // which vehicle is active. The walker is always controllable.
        self.walker.move_x = match (self.keys_held.left, self.keys_held.right) {
            (true, false) => -1.0,
            (false, true) =>  1.0,
            _ => 0.0,
        };

        match self.active_vehicle {
            ActiveVehicle::Pod => {
                // D-pad left/right → rotation direction.
                self.pod.rotate_dir = match (self.keys_held.left, self.keys_held.right) {
                    (true, false) => 1,   // left key = rotate counter-clockwise
                    (false, true) => -1,  // right key = rotate clockwise
                    _ => 0,
                };
                // Up/down → shield.
                self.pod.shield_active = self.keys_held.up || self.keys_held.down;
                // Space → thrust (binary from keyboard, 0 or 1).
                self.pod.thrust = if self.keys_held.fire { 1.0 } else { 0.0 };
                // E → fire.
                self.pod.fire = self.keys_held.drill;
            }
            ActiveVehicle::Tank => {
                // Left/right → move.
                self.tank.move_x = match (self.keys_held.left, self.keys_held.right) {
                    (true, false) => -1.0,
                    (false, true) => 1.0,
                    _ => 0.0,
                };
                // Space → fire.
                self.tank.fire_held = self.keys_held.fire;
                // E → drill.
                self.tank.drill_held = self.keys_held.drill;
                // Up/down → aim elevation adjustment.
                if self.keys_held.up {
                    self.tank.aim_elevation = (self.tank.aim_elevation + 0.02).min(1.0);
                }
                if self.keys_held.down {
                    self.tank.aim_elevation = (self.tank.aim_elevation - 0.02).max(0.0);
                }
            }
        }
    }

    // ── Gamepad input ─────────────────────────────────────────────────────

    /// Process gamepad events from gilrs. Call this each frame.
    pub fn poll_gamepad(&mut self, gilrs: &mut Gilrs) {
        // Drain the event queue (gilrs requires this even if we poll axes directly).
        while let Some(GilrsEvent { event, .. }) = gilrs.next_event() {
            match event {
                gilrs::EventType::ButtonPressed(btn, _) => self.handle_button(btn, true),
                gilrs::EventType::ButtonReleased(btn, _) => self.handle_button(btn, false),
                _ => {}
            }
        }

        // Poll analog axes directly from the first connected gamepad.
        // This is more reliable for sticks than events (which only fire on change).
        if let Some((_id, gamepad)) = gilrs.gamepads().next() {
            // ── Right stick → camera look-ahead (both vehicles) ───────────
            let (rsx, rsy) = apply_deadzone(
                gamepad.value(Axis::RightStickX),
                gamepad.value(Axis::RightStickY),
                DEADZONE,
            );
            // Scale to a look-ahead distance in world pixels.
            // 100.0 = max offset when stick is fully deflected.
            self.camera_offset_x = rsx * 100.0;
            self.camera_offset_y = -rsy * 100.0; // invert Y (stick up = world up = -y)

            // ── Walker: left stick X (always, regardless of vehicle) ──────
            // Walker input is independent of active_vehicle. Even while flying
            // the pod the player's feet are somewhere.
            let (lsx, _) = apply_deadzone(
                gamepad.value(Axis::LeftStickX),
                gamepad.value(Axis::LeftStickY),
                DEADZONE,
            );
            self.walker.move_x = lsx;

            match self.active_vehicle {
                ActiveVehicle::Pod => {
                    // Triggers → thrust and fire.
                    // gilrs reports triggers as 0.0 (released) to 1.0 (fully pressed).
                    let rt = gamepad.value(Axis::RightZ).max(0.0);
                    let lt = gamepad.value(Axis::LeftZ).max(0.0);
                    if rt > 0.05 { self.pod.thrust = rt; }
                    if lt > 0.5  { self.pod.fire = true; }

                    // D-pad → rotation + shield (handled in button events).
                }
                ActiveVehicle::Tank => {
                    // Left stick X → movement.
                    let (lsx, _) = apply_deadzone(
                        gamepad.value(Axis::LeftStickX),
                        gamepad.value(Axis::LeftStickY),
                        DEADZONE,
                    );
                    self.tank.move_x = lsx;

                    // Right stick Y → aim elevation.
                    // Map -1..1 stick to 0..1 elevation (stick up = high angle).
                    if rsy.abs() > 0.05 {
                        self.tank.aim_elevation = (self.tank.aim_elevation - rsy * 0.03).clamp(0.0, 1.0);
                    }

                    // Triggers → fire and drill.
                    let rt = gamepad.value(Axis::RightZ).max(0.0);
                    let lt = gamepad.value(Axis::LeftZ).max(0.0);
                    self.tank.fire_held = rt > 0.1;
                    self.tank.drill_held = lt > 0.1;
                }
            }
        }
    }

    /// Handle a digital button press/release from the gamepad.
    fn handle_button(&mut self, btn: Button, pressed: bool) {
        match btn {
            // Y (North) = switch vehicle.
            Button::North if pressed => self.switch_vehicle_pressed = true,

            // Start = pause.
            Button::Start if pressed => self.pause_pressed = true,

            // D-pad → pod rotation + shield, tank payload cycle + zoom.
            Button::DPadLeft => {
                match self.active_vehicle {
                    ActiveVehicle::Pod => {
                        self.pod.rotate_dir = if pressed { 1 } else { 0 };
                    }
                    ActiveVehicle::Tank if pressed => {
                        self.tank.payload_cycle = -1;
                    }
                    _ => {}
                }
            }
            Button::DPadRight => {
                match self.active_vehicle {
                    ActiveVehicle::Pod => {
                        self.pod.rotate_dir = if pressed { -1 } else { 0 };
                    }
                    ActiveVehicle::Tank if pressed => {
                        self.tank.payload_cycle = 1;
                    }
                    _ => {}
                }
            }
            Button::DPadUp | Button::DPadDown => {
                match self.active_vehicle {
                    ActiveVehicle::Pod => {
                        self.pod.shield_active = pressed;
                    }
                    ActiveVehicle::Tank if pressed => {
                        self.zoom_delta += if btn == Button::DPadUp { 1.0 } else { -1.0 };
                    }
                    _ => {}
                }
            }

            // A (South) = jump (walker always) + interact (tank).
            // Walker gets jump regardless of which vehicle is active.
            Button::South if pressed => {
                self.walker.jump_just_pressed = true;
                if self.active_vehicle == ActiveVehicle::Tank {
                    self.tank.interact = true;
                }
            }

            // B (East) = tow (tank only).
            Button::East if pressed => {
                if self.active_vehicle == ActiveVehicle::Tank {
                    self.tank.tow_toggle = true;
                }
            }

            _ => {}
        }
    }

    /// Call once per frame after all input has been processed.
    /// Advances pod rotation, handles vehicle switch.
    pub fn end_frame(&mut self) {
        self.pod.tick_rotation();

        if self.switch_vehicle_pressed {
            self.active_vehicle = match self.active_vehicle {
                ActiveVehicle::Pod  => ActiveVehicle::Tank,
                ActiveVehicle::Tank => ActiveVehicle::Pod,
            };
        }
    }
}

impl Default for InputState {
    fn default() -> Self { Self::new() }
}

// ── Deadzone ──────────────────────────────────────────────────────────────────

/// Apply a circular deadzone to a 2D stick input.
/// Returns (0, 0) if the stick magnitude is below the deadzone threshold.
/// Rescales the output so the deadzone edge maps to 0.0 (not a jump).
///
/// Circular deadzone (not per-axis) prevents diagonal drift — a common
/// Xbox controller issue where slight X drift appears only on Y movement.
pub fn apply_deadzone(x: f32, y: f32, deadzone: f32) -> (f32, f32) {
    let magnitude = (x * x + y * y).sqrt();
    if magnitude < deadzone {
        return (0.0, 0.0);
    }
    let scale = (magnitude - deadzone) / (1.0 - deadzone);
    let norm_x = x / magnitude;
    let norm_y = y / magnitude;
    (norm_x * scale, norm_y * scale)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn angle_table_has_16_entries() {
        assert_eq!(ANGLE_TABLE.len(), 16);
    }

    #[test]
    fn angle_table_unit_vectors() {
        // Each entry should be approximately unit length.
        for (i, &(dx, dy)) in ANGLE_TABLE.iter().enumerate() {
            let len = (dx * dx + dy * dy).sqrt();
            assert!((len - 1.0).abs() < 0.01,
                "angle {i} vector ({dx}, {dy}) has length {len}, expected ~1.0");
        }
    }

    #[test]
    fn pod_rotation_wraps() {
        // Clockwise (-1) from angle 0 should wrap to 15.
        let mut pod = PodInput::new();
        pod.facing_angle = 0;
        pod.rotate_dir = -1; // clockwise
        pod.rotation_accumulator = ROTATION_FRAMES_PER_STEP - 1;
        pod.tick_rotation();
        // 0 + 0xFF (which is -1 as u8) = 255, & 0xF = 15
        assert_eq!(pod.facing_angle, 15, "should wrap from 0 to 15 going clockwise");
    }

    #[test]
    fn pod_rotation_wraps_backwards() {
        let mut pod = PodInput::new();
        pod.facing_angle = 0;
        pod.rotate_dir = 1; // counter-clockwise
        pod.rotation_accumulator = ROTATION_FRAMES_PER_STEP - 1;
        pod.tick_rotation();
        // 0 + 1 (as u8) & 0xF = 1. But CCW from 0 wrapping should give 15?
        // Actually: wrapping_add(1 as u8) = 1, & 0xF = 1.
        // That's correct — rotate_dir=1 means CCW which goes 0→1→2→...
        // D-pad left = +1 = CCW, d-pad right = -1 = CW.
        // 0 wrapping_add(0xFF as u8) (which is -1 as u8) = 255, & 0xF = 15. ✓
        assert_eq!(pod.facing_angle, 1);
    }

    #[test]
    fn deadzone_filters_small_input() {
        let (x, y) = apply_deadzone(0.05, 0.05, 0.15);
        assert_eq!(x, 0.0);
        assert_eq!(y, 0.0);
    }

    #[test]
    fn deadzone_passes_large_input() {
        let (x, y) = apply_deadzone(0.8, 0.0, 0.15);
        assert!(x > 0.0, "deadzone should pass x=0.8");
        assert_eq!(y, 0.0);
    }

    #[test]
    fn thrust_vector_scales_by_magnitude() {
        let mut pod = PodInput::new();
        pod.facing_angle = 0; // right
        pod.thrust = 0.5;
        let (dx, dy) = pod.thrust_vector();
        assert!((dx - 0.5).abs() < 0.01);
        assert!(dy.abs() < 0.01);
    }

    #[test]
    fn vehicle_switch_toggles() {
        let mut input = InputState::new();
        assert_eq!(input.active_vehicle, ActiveVehicle::Pod);
        input.switch_vehicle_pressed = true;
        input.end_frame();
        assert_eq!(input.active_vehicle, ActiveVehicle::Tank);
        input.switch_vehicle_pressed = true;
        input.end_frame();
        assert_eq!(input.active_vehicle, ActiveVehicle::Pod);
    }
}
