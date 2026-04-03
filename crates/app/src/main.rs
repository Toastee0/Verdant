// app — Verdant binary entry point
//
// Wires sim + render together and drives the game loop.
//
// Ownership:
//   app owns ChunkManager (sim state), Renderer (GPU state), Gilrs (gamepad),
//   and InputState (unified input). Each frame: poll input → sim tick →
//   renderer uploads visible chunks → present.
//
// The event loop is winit-based. Both keyboard and gamepad write into
// InputState; the game loop reads only InputState. See input.rs.

mod input;

use input::InputState;
use verdant_render::{Camera, Renderer};
use verdant_sim::chunk::ChunkCoord;
use verdant_sim::chunk_manager::ChunkManager;

use gilrs::Gilrs;
use winit::application::ApplicationHandler;
use winit::event::{ElementState, KeyEvent, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::keyboard::PhysicalKey;
use winit::window::{Window, WindowId};

use std::sync::Arc;

/// Active game state — created once the window opens.
struct GameState {
    window:   Arc<Window>,
    renderer: Renderer,
    camera:   Camera,
    world:    ChunkManager,
    input:    InputState,
    gilrs:    Gilrs,

    /// The player-controlled walker entity.
    walker: verdant_sim::walker::Walker,

    /// World-space bounds of all authored content combined.
    /// Camera is clamped so the viewport never exits these bounds.
    content_left:     f32,
    content_right:    f32,
    content_center_y: f32,

    /// Locked zoom level — derived from window height, updated on resize.
    min_zoom: f32,

    /// Animation state for the walker sprite.
    /// frame_col: current column in the atlas (0..8 for walking)
    /// frame_tick: counts up to ANIM_SPEED then advances frame_col
    anim_col:  u32,
    anim_tick: u32,
}

/// Application handler that winit calls into.
/// Before the window is created, `state` is None.
struct App {
    state: Option<GameState>,
}

impl App {
    fn new() -> App {
        App { state: None }
    }
}

/// Scan upward from `wy` until the walker body (WALKER_H cells) is entirely
/// in non-solid cells.  Returns the first safe feet-position found, or the
/// original `wy` if none is found within the tile height.
///
/// Needed because the spawn point in a .cave file can land inside solid
/// geometry (e.g. inside the M-cell machine block at the tile's left edge).
/// The walker then falls naturally to the nearest floor below.
fn find_safe_spawn_wy(wx: f32, wy: f32, world: &ChunkManager) -> f32 {
    use verdant_sim::walker::WALKER_H;
    use verdant_sim::worldgen::maptiles::TILE_SIZE;
    let cx = wx.floor() as i32;
    let mut check_wy = wy;
    // Search at most one tile height upward.
    for _ in 0..(TILE_SIZE as i32) {
        let feet  = check_wy as i32;
        let head  = (check_wy - WALKER_H) as i32;
        let clear = (head..=feet).all(|row| {
            world.get_cell_world(cx, row)
                .map(|c| !c.is_solid())
                .unwrap_or(false) // unloaded = not confirmed clear
        });
        if clear { return check_wy; }
        check_wy -= 1.0;
    }
    wy // fallback — gravity will sort it out
}

/// Compute the fixed zoom and camera target from window dimensions and
/// content bounds.  Zoom fills the viewport height with exactly 128 cells
/// (one tile tall), content is never clipped vertically.
fn zoom_for_height(viewport_h: f32) -> f32 {
    use verdant_sim::worldgen::maptiles::TILE_SIZE;
    viewport_h / TILE_SIZE as f32
}

/// Clamp camera X so the viewport [cam_x ± half_w] stays inside
/// [content_left, content_right].  Returns the clamped target.
fn clamp_cam_x(player_x: f32, half_w: f32, left: f32, right: f32) -> f32 {
    // If the content is narrower than the viewport, lock to content center.
    let lo = left  + half_w;
    let hi = right - half_w;
    if lo >= hi {
        (left + right) * 0.5
    } else {
        player_x.clamp(lo, hi)
    }
}

impl ApplicationHandler for App {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        // Create the window on first resume (or re-create after suspend).
        if self.state.is_some() { return; }

        // Open maximized so the window fills the display at startup.
        let attrs = Window::default_attributes()
            .with_title("Verdant")
            .with_maximized(true);

        let window = Arc::new(event_loop.create_window(attrs).expect("failed to create window"));

        let size = window.inner_size();
        let renderer = Renderer::new(window.clone());
        let mut camera = Camera::new(size.width as f32, size.height as f32);

        // Gamepad init. Gilrs::new() scans for connected controllers.
        // If no controller is found, everything still works — keyboard only.
        let gilrs = Gilrs::new().unwrap_or_else(|e| {
            log::warn!("gilrs init failed (gamepad unavailable): {e}");
            panic!("gilrs init failed: {e}");
        });

        for (_id, gamepad) in gilrs.gamepads() {
            log::info!("Gamepad connected: {} ({})", gamepad.name(), gamepad.os_name());
        }

        let input = InputState::new();

        // Sim setup: active radius 2 = 5×5 = 25 chunks around the player.
        let mut world = ChunkManager::new(2);
        world.set_player_chunk(ChunkCoord::new(0, 0));

        // Build content bounds from the authored screen list.
        use verdant_sim::worldgen::maptiles::{all_screens, base_cave_spawn, tutorial_cave_spawn,
                                               TUTORIAL_CAVE_OFFSET, TUTORIAL_CAVE_CHUNK};
        let screens = all_screens();
        let content_left  = screens.iter().map(|s| s.min_x).fold(f32::MAX, f32::min);
        let content_right = screens.iter().map(|s| s.max_x).fold(f32::MIN, f32::max);
        let content_top   = screens.iter().map(|s| s.min_y).fold(f32::MAX, f32::min);
        let content_bot   = screens.iter().map(|s| s.max_y).fold(f32::MIN, f32::max);
        let content_center_y = (content_top + content_bot) * 0.5;

        // Zoom: one tile height (128 cells) fills the viewport height exactly.
        let min_zoom = zoom_for_height(size.height as f32);
        camera.set_zoom(min_zoom);

        // Walker spawn: prefer base tile spawn flag, then tutorial cave, then fallback.
        let (spawn_wx, spawn_wy) = base_cave_spawn()
            .or_else(tutorial_cave_spawn)
            .unwrap_or_else(|| {
                let wx = TUTORIAL_CAVE_CHUNK.0 * 512 + TUTORIAL_CAVE_OFFSET.0 as i32 + 64;
                let wy = TUTORIAL_CAVE_CHUNK.1 * 512 + TUTORIAL_CAVE_OFFSET.1 as i32 + 64;
                (wx as f32, wy as f32)
            });

        // Camera starts on the spawn screen, clamped to content bounds.
        let half_w = camera.viewport_width / (2.0 * min_zoom);
        camera.x = clamp_cam_x(spawn_wx, half_w, content_left, content_right);
        camera.y = content_center_y;

        let safe_wy = find_safe_spawn_wy(spawn_wx, spawn_wy, &world);
        let walker = verdant_sim::walker::Walker::new(spawn_wx, safe_wy);

        log::info!("Verdant — {} chunks loaded", world.loaded_count());

        self.state = Some(GameState {
            window,
            renderer,
            camera,
            world,
            input,
            gilrs,
            walker,
            content_left,
            content_right,
            content_center_y,
            min_zoom,
            anim_col:  0,
            anim_tick: 0,
        });
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        let Some(state) = self.state.as_mut() else { return };

        match event {
            WindowEvent::CloseRequested => {
                event_loop.exit();
            }

            WindowEvent::Resized(size) => {
                state.renderer.resize(size.width, size.height);
                state.camera.resize(size.width as f32, size.height as f32);
                // Recalculate locked zoom whenever the window resizes.
                state.min_zoom = zoom_for_height(size.height as f32);
                state.camera.set_zoom(state.min_zoom);
            }

            // ── Keyboard input (press + release both captured) ────────────
            WindowEvent::KeyboardInput {
                event: KeyEvent {
                    physical_key: PhysicalKey::Code(key),
                    state: key_state,
                    ..
                },
                ..
            } => {
                let pressed = key_state == ElementState::Pressed;
                state.input.handle_key(key, pressed);
            }

            // ── Redraw: poll input → sim tick → render frame ──────────────
            WindowEvent::RedrawRequested => {
                // ── 1. Input ──────────────────────────────────────────────
                state.input.begin_frame();
                state.input.poll_gamepad(&mut state.gilrs);
                state.input.apply_keyboard_held();

                // Pause / exit.
                if state.input.pause_pressed {
                    event_loop.exit();
                    return;
                }

                state.input.end_frame();

                // ── 2. Sim tick ───────────────────────────────────────────
                state.world.tick_high_frequency();

                // ── 3. Walker tick ────────────────────────────────────────
                let walker_input = verdant_sim::walker::WalkerInput {
                    move_x:            state.input.walker.move_x,
                    jump_just_pressed: state.input.walker.jump_just_pressed,
                };
                state.walker.tick(&walker_input, &state.world);

                // ── 4. Camera — smooth proportional follow, content-clamped ──
                //
                // Zoom is fixed at min_zoom (viewport_height / 128 cells).
                // Camera X follows the player, clamped so the viewport never
                // exits [content_left, content_right].  No panning past edges
                // that have no authored tile.
                // Camera Y is fixed at content center (tiles share one Y band).
                let zoom   = state.min_zoom;
                let half_w = state.camera.viewport_width / (2.0 * zoom);

                let target_x = clamp_cam_x(
                    state.walker.wx, half_w,
                    state.content_left, state.content_right,
                );
                let target_y = state.content_center_y;

                // Lerp toward target.  0.08 at 60 Hz ≈ smooth ~15-frame pan.
                const CAM_LERP: f32 = 0.08;
                state.camera.x += (target_x - state.camera.x) * CAM_LERP;
                state.camera.y += (target_y - state.camera.y) * CAM_LERP;
                state.camera.set_zoom(zoom); // keep zoom locked

                // ── 5. Update player chunk ────────────────────────────────
                {
                    use verdant_sim::chunk::{CHUNK_WIDTH, CHUNK_HEIGHT, ChunkCoord};
                    let player_cx = (state.walker.wx as i32).div_euclid(CHUNK_WIDTH  as i32);
                    let player_cy = (state.walker.wy as i32).div_euclid(CHUNK_HEIGHT as i32);
                    state.world.set_player_chunk(ChunkCoord::new(player_cx, player_cy));
                }

                // ── 6. Upload visible chunks to GPU ───────────────────────
                let (min_cx, min_cy, max_cx, max_cy) = state.camera.visible_chunk_range();
                for (&coord, chunk) in state.world.iter_chunks() {
                    if coord.cx >= min_cx && coord.cx <= max_cx
                        && coord.cy >= min_cy && coord.cy <= max_cy
                    {
                        state.renderer.update_chunk(coord, chunk.front_slice());
                    }
                }

                // ── 7. Animate + render ───────────────────────────────────
                //
                // Walking: atlas row 0, cols 0-7 (8 frames).
                // Idle:    atlas row 0, col 0.
                // Frame advances every ANIM_SPEED ticks (~8 fps at 60 Hz).
                const WALK_FRAMES: u32 = 8;
                const ANIM_SPEED:  u32 = 8; // ticks per frame

                let is_moving = state.walker.vx.abs() > 0.01;
                if is_moving {
                    state.anim_tick += 1;
                    if state.anim_tick >= ANIM_SPEED {
                        state.anim_tick = 0;
                        state.anim_col = (state.anim_col + 1) % WALK_FRAMES;
                    }
                } else {
                    state.anim_col  = 0;
                    state.anim_tick = 0;
                }

                // Sprite size in world cells: 6 wide × 10 tall, feet-anchored.
                // FOOT_INSET: the lemming sheet has ~3 blank (black) pixels at the
                // bottom of each frame (~20px tall).  Discard in the shader makes
                // the visible feet float ~1.5 cells above wy.  Shift the rect down
                // by that amount so the visible feet land exactly on wy.
                const SPR_W:      f32 = 6.0;
                const SPR_H:      f32 = 10.0;
                const FOOT_INSET: f32 = 1.5; // cells to shift rect downward
                let spr_x = state.walker.wx - SPR_W / 2.0;
                let spr_y = state.walker.wy - SPR_H + FOOT_INSET;

                let sprite = verdant_render::SpriteFrame {
                    col:    state.anim_col,
                    row:    0, // row 0 = walking animation
                    x:      spr_x,
                    y:      spr_y,
                    w:      SPR_W,
                    h:      SPR_H,
                    flip_x: state.walker.facing < 0.0,
                };
                state.renderer.present(&state.camera, &[], &[sprite]);

                // Request next frame immediately (continuous redraw).
                state.window.request_redraw();
            }

            _ => {}
        }
    }
}

fn main() {
    env_logger::init();

    let event_loop = EventLoop::new().expect("failed to create event loop");
    event_loop.set_control_flow(ControlFlow::Poll);

    let mut app = App::new();
    event_loop.run_app(&mut app).expect("event loop error");
}
