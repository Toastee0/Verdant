// camera.rs — orthographic camera for the chunked pixel world
//
// The camera tracks a position in world-pixel coordinates and a zoom level.
// It produces an orthographic projection matrix that the vertex shader uses
// to transform chunk quads from world space to clip space.
//
// World coordinates: (0,0) is the top-left of chunk (0,0).
//   world_x = chunk_cx * 512 + local_x
//   world_y = chunk_cy * 512 + local_y
//
// Zoom levels: 1 cell = 1, 2, 4, or 8 screen pixels.
// At zoom=1, a 1920×1080 window shows ~1920×1080 cells (roughly 4×2 chunks).
// At zoom=4, you see ~480×270 cells — close-up view of a single chunk region.

/// Orthographic camera for the 2D world.
pub struct Camera {
    /// Center of the viewport in world-pixel coordinates.
    pub x: f32,
    pub y: f32,

    /// Pixels per cell. 1.0 = 1:1, 2.0 = 2× zoom in, etc.
    /// Clamped to [0.25, 8.0] when set via zoom methods.
    pub zoom: f32,

    /// Viewport dimensions in screen pixels (set from window size).
    pub viewport_width:  f32,
    pub viewport_height: f32,
}

impl Camera {
    pub fn new(viewport_width: f32, viewport_height: f32) -> Camera {
        Camera {
            x: 256.0,  // center of origin chunk
            y: 256.0,
            zoom: 2.0, // start at 2× so individual cells are visible
            viewport_width,
            viewport_height,
        }
    }

    /// Resize the viewport (call when the window resizes).
    pub fn resize(&mut self, width: f32, height: f32) {
        self.viewport_width = width;
        self.viewport_height = height;
    }

    /// Pan the camera by (dx, dy) in screen pixels.
    /// Divides by zoom so panning feels consistent regardless of zoom level.
    pub fn pan(&mut self, dx: f32, dy: f32) {
        self.x -= dx / self.zoom;
        self.y -= dy / self.zoom;
    }

    /// Multiply zoom by `factor`. Clamped to [0.5, 32.0].
    pub fn zoom_by(&mut self, factor: f32) {
        self.zoom = (self.zoom * factor).clamp(0.5, 32.0);
    }

    /// Set zoom to an exact level. Clamped to [0.5, 32.0].
    pub fn set_zoom(&mut self, z: f32) {
        self.zoom = z.clamp(0.5, 32.0);
    }

    /// Half-width of the visible area in world pixels.
    #[inline]
    fn half_w(&self) -> f32 {
        self.viewport_width / (2.0 * self.zoom)
    }

    /// Half-height of the visible area in world pixels.
    #[inline]
    fn half_h(&self) -> f32 {
        self.viewport_height / (2.0 * self.zoom)
    }

    /// Build a column-major 4×4 orthographic projection matrix.
    ///
    /// Maps world-pixel coordinates to clip space [-1, 1].
    /// The camera center (self.x, self.y) maps to (0,0) in clip space.
    ///
    /// In C: you'd build this as a float[16] and upload it to a UBO.
    /// In Rust: same thing — [f32; 16] in column-major order (what wgpu expects).
    pub fn view_proj_matrix(&self) -> [f32; 16] {
        let hw = self.half_w();
        let hh = self.half_h();

        let left   = self.x - hw;
        let right  = self.x + hw;
        let top    = self.y - hh;
        let bottom = self.y + hh;

        // Orthographic projection, column-major layout.
        // Near=0, Far=1 (2D — depth doesn't matter).
        //
        // Standard ortho formula:
        //   sx = 2 / (right - left)     tx = -(right + left) / (right - left)
        //   sy = 2 / (bottom - top)     ty = -(bottom + top) / (bottom - top)
        //   sz = -1 / (far - near)      tz = -near / (far - near)
        //
        // wgpu uses clip space z in [0, 1] (not [-1, 1] like OpenGL).
        let sx = 2.0 / (right - left);
        let sy = -2.0 / (bottom - top); // negate: world Y-down → clip Y-up
        let tx = -(right + left) / (right - left);
        let ty =  (bottom + top) / (bottom - top);

        // Column-major: columns are contiguous in memory.
        // Column 0: [sx, 0, 0, 0]
        // Column 1: [0, sy, 0, 0]
        // Column 2: [0, 0, 1, 0]   (z passthrough for 2D)
        // Column 3: [tx, ty, 0, 1]
        [
            sx,  0.0, 0.0, 0.0,   // col 0
            0.0, sy,  0.0, 0.0,   // col 1
            0.0, 0.0, 1.0, 0.0,   // col 2
            tx,  ty,  0.0, 1.0,   // col 3
        ]
    }

    /// Returns (min_cx, min_cy, max_cx, max_cy) — the range of chunk coords
    /// that overlap the current viewport. Used to cull off-screen chunks.
    pub fn visible_chunk_range(&self) -> (i32, i32, i32, i32) {
        let hw = self.half_w();
        let hh = self.half_h();

        // World-pixel bounds of the viewport
        let left   = self.x - hw;
        let right  = self.x + hw;
        let top    = self.y - hh;
        let bottom = self.y + hh;

        // Convert to chunk coords, rounding outward (floor for min, ceil-1 for max)
        let min_cx = (left   / 512.0).floor() as i32;
        let min_cy = (top    / 512.0).floor() as i32;
        let max_cx = (right  / 512.0).floor() as i32;
        let max_cy = (bottom / 512.0).floor() as i32;

        (min_cx, min_cy, max_cx, max_cy)
    }
}
