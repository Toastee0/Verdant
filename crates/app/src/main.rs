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
use verdant_sim::cell::Cell;
use verdant_sim::chunk::{ChunkCoord, CHUNK_AREA, CHUNK_WIDTH, CHUNK_HEIGHT};
use verdant_sim::chunk_manager::ChunkManager;

use gilrs::Gilrs;
use winit::application::ApplicationHandler;
use winit::dpi::LogicalSize;
use winit::event::{ElementState, KeyEvent, MouseScrollDelta, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::keyboard::{KeyCode, PhysicalKey};
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

    /// Track mouse state for pan-on-drag.
    /// Mouse pan is always available as a dev/debug camera control,
    /// independent of the InputState vehicle system.
    mouse_pressed: bool,
    last_mouse_x:  f64,
    last_mouse_y:  f64,
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

impl ApplicationHandler for App {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        // Create the window on first resume (or re-create after suspend).
        if self.state.is_some() { return; }

        let attrs = Window::default_attributes()
            .with_title("Verdant")
            .with_inner_size(LogicalSize::new(1280.0, 720.0));

        let window = Arc::new(event_loop.create_window(attrs).expect("failed to create window"));

        let size = window.inner_size();
        let renderer = Renderer::new(window.clone());
        let camera = Camera::new(size.width as f32, size.height as f32);

        // Gamepad init. Gilrs::new() scans for connected controllers.
        // If no controller is found, everything still works — keyboard only.
        let gilrs = Gilrs::new().unwrap_or_else(|e| {
            log::warn!("gilrs init failed (gamepad unavailable): {e}");
            // Gilrs::new() only fails on critical platform errors; unwrap is safe
            // in practice. The warning tells the user what happened.
            panic!("gilrs init failed: {e}");
        });

        for (_id, gamepad) in gilrs.gamepads() {
            log::info!("Gamepad connected: {} ({})", gamepad.name(), gamepad.os_name());
        }

        let input = InputState::new();

        // Sim setup: active radius 1 = 3×3 chunks around the player.
        let mut world = ChunkManager::new(1);
        let origin = ChunkCoord::new(0, 0);
        world.set_player_chunk(origin);

        // Seed some test content so the world isn't invisible on first launch.
        // Place water, rock, soil, and a plant in the origin chunk for visual verification.
        if let Some(chunk) = world.get_mut(origin) {
            // A band of rock near the bottom.
            chunk.fill_rect(0, 400, CHUNK_WIDTH, CHUNK_HEIGHT, Cell::rock());
            // Soil layer on top of rock.
            chunk.fill_rect(0, 380, CHUNK_WIDTH, 400, Cell::loose_soil());
            // A pool of water.
            chunk.fill_rect(100, 350, 300, 380, Cell::new_water());
            // Some mud at the waterline.
            chunk.fill_rect(100, 378, 300, 382, Cell::mud());
            // A plant: root + stem + leaves.
            chunk.fill_rect(250, 370, 255, 380, Cell::plant_tile(1, 1, 200, 200, 378, 252)); // root
            chunk.fill_rect(250, 340, 255, 370, Cell::plant_tile(1, 2, 180, 180, 378, 252)); // stem
            chunk.fill_rect(240, 330, 265, 340, Cell::plant_tile(1, 3, 160, 220, 378, 252)); // leaves
        }

        log::info!(
            "Verdant — {} chunks loaded, {} bytes/chunk (double-buffered)",
            world.loaded_count(),
            CHUNK_AREA * std::mem::size_of::<Cell>() * 2,
        );

        self.state = Some(GameState {
            window,
            renderer,
            camera,
            world,
            input,
            gilrs,
            mouse_pressed: false,
            last_mouse_x: 0.0,
            last_mouse_y: 0.0,
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

            // ── Mouse wheel zoom (always works, debug camera) ─────────────
            WindowEvent::MouseWheel { delta, .. } => {
                let scroll = match delta {
                    MouseScrollDelta::LineDelta(_, y) => y as f64,
                    MouseScrollDelta::PixelDelta(pos) => pos.y / 50.0,
                };
                state.camera.zoom_by(1.0 + scroll as f32 * 0.1);
            }

            // ── Mouse drag to pan (always works, debug camera) ────────────
            WindowEvent::MouseInput { state: btn_state, button: winit::event::MouseButton::Left, .. } => {
                state.mouse_pressed = btn_state == ElementState::Pressed;
            }

            WindowEvent::CursorMoved { position, .. } => {
                if state.mouse_pressed {
                    let dx = position.x - state.last_mouse_x;
                    let dy = position.y - state.last_mouse_y;
                    state.camera.pan(dx as f32, dy as f32);
                }
                state.last_mouse_x = position.x;
                state.last_mouse_y = position.y;
            }

            // ── Redraw: poll input → sim tick → render frame ──────────────
            WindowEvent::RedrawRequested => {
                // ── 1. Input ──────────────────────────────────────────────
                // Clear per-frame transients, poll gamepad, apply held keys.
                state.input.begin_frame();
                state.input.poll_gamepad(&mut state.gilrs);
                state.input.apply_keyboard_held();

                // ── 2. Apply input to camera ──────────────────────────────
                // Zoom from keyboard/d-pad.
                if state.input.zoom_delta > 0.0 {
                    state.camera.zoom_by(1.25);
                } else if state.input.zoom_delta < 0.0 {
                    state.camera.zoom_by(0.8);
                }

                // Gamepad right stick → camera look-ahead offset.
                // Applied additively to the camera center each frame.
                // TODO: When vehicles exist, camera should follow vehicle +
                // look-ahead. For now this pans the debug camera.
                let look_x = state.input.camera_offset_x;
                let look_y = state.input.camera_offset_y;
                if look_x.abs() > 0.5 || look_y.abs() > 0.5 {
                    // Scale down for smooth movement (screen-space delta).
                    state.camera.pan(-look_x * 0.1, look_y * 0.1);
                }

                // Pause / exit.
                if state.input.pause_pressed {
                    // TODO: pause menu. For now, exit.
                    event_loop.exit();
                    return;
                }

                // Finalize input (rotation tick, vehicle switch).
                state.input.end_frame();

                // ── 3. Sim tick ───────────────────────────────────────────
                state.world.tick_high_frequency();

                // ── 4. Upload visible chunks to GPU ───────────────────────
                let (min_cx, min_cy, max_cx, max_cy) = state.camera.visible_chunk_range();
                for (&coord, chunk) in state.world.iter_chunks() {
                    if coord.cx >= min_cx && coord.cx <= max_cx
                        && coord.cy >= min_cy && coord.cy <= max_cy
                    {
                        state.renderer.update_chunk(coord, chunk.front_slice());
                    }
                }

                // ── 5. Render frame ───────────────────────────────────────
                state.renderer.present(&state.camera);

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
