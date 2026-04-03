// sprite.wgsl — textured sprite rendering
//
// Draws a single sprite from an atlas texture.
// No vertex buffer — 6 vertices built from @builtin(vertex_index).
//
// Group 0: camera uniform (shared with chunk + entity pipelines)
// Group 1: per-sprite uniform (rect, uv_rect, flip_x)
// Group 2: sprite atlas texture + sampler
//
// Background pixels in the lemming sheet are near-black (#000 or close).
// The fragment shader discards them, giving alpha-cut transparency.

struct CameraUniform {
    view_proj: mat4x4<f32>,
}
@group(0) @binding(0) var<uniform> camera: CameraUniform;

struct SpriteUniform {
    /// World-space rect: x, y = top-left corner; z, w = width, height (in cells)
    rect:    vec4<f32>,
    /// Atlas UV rect: x, y = top-left; z, w = bottom-right (in [0..1] space)
    uv_rect: vec4<f32>,
    /// 1.0 = flip horizontally (mirror sprite for left-facing)
    flip_x:  f32,
    // Explicit scalar padding — vec3 would force 16-byte alignment (48→64 bytes)
    _pad0:   f32,
    _pad1:   f32,
    _pad2:   f32,
}
@group(1) @binding(0) var<uniform> spr: SpriteUniform;

@group(2) @binding(0) var atlas_tex: texture_2d<f32>;
@group(2) @binding(1) var atlas_smp: sampler;

struct VertOut {
    @builtin(position) clip_pos: vec4<f32>,
    @location(0)       uv:       vec2<f32>,
}

@vertex
fn vs_main(@builtin(vertex_index) vi: u32) -> VertOut {
    // Two triangles, CCW winding, covering the [0,1]×[0,1] unit quad.
    var corners = array<vec2<f32>, 6>(
        vec2(0.0, 0.0), vec2(1.0, 0.0), vec2(0.0, 1.0),
        vec2(1.0, 0.0), vec2(1.0, 1.0), vec2(0.0, 1.0),
    );
    let c = corners[vi];

    // World position
    let wx = spr.rect.x + c.x * spr.rect.z;
    let wy = spr.rect.y + c.y * spr.rect.w;

    // UV — optionally mirror U for left-facing
    let u_lo = spr.uv_rect.x;
    let u_hi = spr.uv_rect.z;
    let u    = select(u_lo + c.x * (u_hi - u_lo),
                      u_hi - c.x * (u_hi - u_lo),
                      spr.flip_x > 0.5);
    let v    = spr.uv_rect.y + c.y * (spr.uv_rect.w - spr.uv_rect.y);

    var out: VertOut;
    out.clip_pos = camera.view_proj * vec4(wx, wy, 0.0, 1.0);
    out.uv       = vec2(u, v);
    return out;
}

@fragment
fn fs_main(in: VertOut) -> @location(0) vec4<f32> {
    let col = textureSample(atlas_tex, atlas_smp, in.uv);
    // Discard background (near-black pixels in the lemming sheet)
    if col.r < 0.04 && col.g < 0.04 && col.b < 0.04 { discard; }
    return col;
}
