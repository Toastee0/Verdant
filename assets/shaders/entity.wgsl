// entity.wgsl — solid-color rect shader for entities and debug overlays
//
// Uses the same camera uniform (group 0, binding 0) as chunk.wgsl so both
// passes share a single uploaded matrix per frame.
//
// Group 1 = EntityData: the rect position/size and fill color.
// No vertex buffer — positions are computed from vertex_index + rect bounds.

struct Camera {
    view_proj: mat4x4<f32>,
}
@group(0) @binding(0) var<uniform> camera: Camera;

struct EntityData {
    rect:  vec4<f32>,   // x, y, w, h in world cells (pixels at 1:1 zoom)
    color: vec4<f32>,   // RGBA linear (not gamma-encoded)
}
@group(1) @binding(0) var<uniform> ent: EntityData;

// Two-triangle quad from vertex_index (0..5).
// Corners in unit [0,1]×[0,1] space, then scaled to rect size.
@vertex
fn vs_main(@builtin(vertex_index) idx: u32) -> @builtin(position) vec4<f32> {
    // Six vertices: two triangles forming one quad.
    var corners = array<vec2<f32>, 6>(
        vec2(0.0, 0.0), vec2(1.0, 0.0), vec2(0.0, 1.0),  // tri 1
        vec2(1.0, 0.0), vec2(1.0, 1.0), vec2(0.0, 1.0),  // tri 2
    );
    let c  = corners[idx];
    // Scale unit corner to rect size, offset by rect origin.
    let wp = vec2(ent.rect.x + c.x * ent.rect.z,
                  ent.rect.y + c.y * ent.rect.w);
    return camera.view_proj * vec4(wp, 0.0, 1.0);
}

@fragment
fn fs_main() -> @location(0) vec4<f32> {
    return ent.color;
}
