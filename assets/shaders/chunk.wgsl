// chunk.wgsl — Verdant chunk rendering shader
//
// Each visible chunk is a textured quad. The texture is a 512×512 Rgba8Unorm
// image where each pixel was CPU-converted from a Cell's continuous values
// (water, mineral, temperature, species, light) into an RGBA color.
//
// This shader does the final screen-space transform and optional light
// modulation. The heavy color derivation happens CPU-side in cell_to_rgba()
// so we can iterate on it without recompiling shaders.

// ── Uniforms ──────────────────────────────────────────────────────────────────

struct CameraUniform {
    // view_proj projects from world-pixel coordinates to clip space.
    // Built as: ortho_projection * translate(-camera_x, -camera_y)
    view_proj: mat4x4<f32>,
};

@group(0) @binding(0)
var<uniform> camera: CameraUniform;

// ── Per-chunk instance data ───────────────────────────────────────────────────
// Passed as vertex attributes from the instance buffer.

struct VertexInput {
    // Per-vertex: the unit quad corner (0,0) (1,0) (0,1) (1,1)
    @location(0) position: vec2<f32>,
    @location(1) uv:       vec2<f32>,
    // Per-instance: world-pixel offset of this chunk's top-left corner
    @location(2) chunk_offset: vec2<f32>,
};

struct VertexOutput {
    @builtin(position) clip_position: vec4<f32>,
    @location(0)       uv:           vec2<f32>,
};

// ── Chunk texture ─────────────────────────────────────────────────────────────

@group(1) @binding(0)
var chunk_texture: texture_2d<f32>;

@group(1) @binding(1)
var chunk_sampler: sampler;

// ── Vertex shader ─────────────────────────────────────────────────────────────

@vertex
fn vs_main(in: VertexInput) -> VertexOutput {
    // Scale the unit quad to chunk size (512×512 world pixels) and offset
    // to this chunk's world position.
    let world_pos = in.position * vec2<f32>(512.0, 512.0) + in.chunk_offset;

    var out: VertexOutput;
    out.clip_position = camera.view_proj * vec4<f32>(world_pos, 0.0, 1.0);
    out.uv = in.uv;
    return out;
}

// ── Fragment shader ───────────────────────────────────────────────────────────

@fragment
fn fs_main(in: VertexOutput) -> @location(0) vec4<f32> {
    // Sample the pre-computed RGBA texture. Color derivation from cell values
    // was done CPU-side in cell_to_rgba(). This keeps the shader simple and
    // lets us iterate on the palette without touching GPU pipeline.
    let color = textureSample(chunk_texture, chunk_sampler, in.uv);
    return color;
}
