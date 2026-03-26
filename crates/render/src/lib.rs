// render crate — stub
//
// Architecture: CPU simulation, GPU rendering only.
//   - Physics sim runs on CPU (Noita approach: ghost ring chunking, checkerboard parallel)
//   - Each visible chunk uploads its front_slice() as a 512×512 GPU texture each frame
//   - Fragment shader reads cell values and outputs color from physics ratios
//     (high water + low mineral + high temp = blue-white vapor, etc.)
//   - GPU compute is used ONLY for the lighting pass (not for sim physics)
//
// Will eventually own: wgpu Device + Queue, swap chain, per-chunk textures,
// the grid fragment shader, and the lighting compute shader.

use verdant_sim::chunk::Chunk;
use verdant_sim::chunk_manager::ChunkManager;

/// Placeholder renderer. Replace with a real wgpu context when the window system is wired.
pub struct Renderer;

impl Renderer {
    pub fn new() -> Renderer {
        Renderer
    }

    /// Upload visible chunks and draw the frame.
    /// Currently a no-op stub.
    pub fn present(&self, _manager: &ChunkManager) {}

    /// Present a single chunk directly (useful for isolated chunk tests).
    pub fn present_chunk(&self, _chunk: &Chunk) {}
}

impl Default for Renderer {
    fn default() -> Self {
        Renderer::new()
    }
}
