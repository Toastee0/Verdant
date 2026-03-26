// render crate — wgpu-based pixel renderer for Verdant's chunked world
//
// Architecture: CPU simulation, GPU rendering only.
//   - Physics sim runs on CPU (Noita approach: ghost ring chunking, checkerboard parallel)
//   - Each visible chunk uploads its front_slice() as a 512×512 GPU texture each frame
//   - Cell→pixel conversion is CPU-side (cell_color::cell_to_rgba) for easy palette iteration
//   - GPU does the ortho projection + texture sampling
//
// Module layout:
//   camera     — orthographic camera, view/projection matrix, viewport culling
//   cell_color — CPU-side Cell→RGBA conversion (the core aesthetic function)
//   lib.rs     — Renderer struct, wgpu init, pipeline, chunk texture management

pub mod camera;
pub mod cell_color;

use std::collections::HashMap;
use verdant_sim::cell::Cell;
use verdant_sim::chunk::{ChunkCoord, CHUNK_WIDTH, CHUNK_HEIGHT};

pub use camera::Camera;

// Re-export winit types the app crate needs.
pub use wgpu;
pub use winit;

// ── Vertex layout for chunk quads ─────────────────────────────────────────────

/// Per-vertex data for the unit quad. Two triangles = 6 vertices.
/// position: corner of the unit quad [0,1]×[0,1]
/// uv: texture coordinate matching that corner
#[repr(C)]
#[derive(Clone, Copy, Debug, bytemuck::Pod, bytemuck::Zeroable)]
struct QuadVertex {
    position: [f32; 2],
    uv:       [f32; 2],
}

/// Unit quad: two triangles covering [0,0] to [1,1].
/// Vertex shader scales this to 512×512 and offsets per-chunk.
const QUAD_VERTICES: &[QuadVertex] = &[
    // Triangle 1: top-left, top-right, bottom-left
    QuadVertex { position: [0.0, 0.0], uv: [0.0, 0.0] },
    QuadVertex { position: [1.0, 0.0], uv: [1.0, 0.0] },
    QuadVertex { position: [0.0, 1.0], uv: [0.0, 1.0] },
    // Triangle 2: top-right, bottom-right, bottom-left
    QuadVertex { position: [1.0, 0.0], uv: [1.0, 0.0] },
    QuadVertex { position: [1.0, 1.0], uv: [1.0, 1.0] },
    QuadVertex { position: [0.0, 1.0], uv: [0.0, 1.0] },
];

/// Per-instance data: the world-pixel offset of this chunk's top-left corner.
#[repr(C)]
#[derive(Clone, Copy, Debug, bytemuck::Pod, bytemuck::Zeroable)]
struct ChunkInstance {
    chunk_offset: [f32; 2],
}

// ── Per-chunk GPU resources ───────────────────────────────────────────────────

/// GPU-side storage for one chunk: a 512×512 RGBA texture + its bind group.
struct ChunkGpuData {
    texture:    wgpu::Texture,
    bind_group: wgpu::BindGroup,
}

// ── Renderer ──────────────────────────────────────────────────────────────────

/// The main rendering context. Owns the wgpu device, pipeline, and per-chunk
/// textures. Created once at startup; lives for the duration of the game.
pub struct Renderer {
    surface:             wgpu::Surface<'static>,
    device:              wgpu::Device,
    queue:               wgpu::Queue,
    config:              wgpu::SurfaceConfiguration,
    render_pipeline:     wgpu::RenderPipeline,
    vertex_buffer:       wgpu::Buffer,
    camera_buffer:       wgpu::Buffer,
    camera_bind_group:   wgpu::BindGroup,
    chunk_bind_layout:   wgpu::BindGroupLayout,
    sampler:             wgpu::Sampler,
    /// Per-chunk GPU textures, keyed by chunk coordinate.
    chunk_textures:      HashMap<ChunkCoord, ChunkGpuData>,
    /// Reusable pixel buffer to avoid per-frame allocation.
    /// 512 * 512 * 4 = 1,048,576 bytes.
    pixel_buf:           Vec<u8>,
    /// Reusable instance buffer for chunk offsets. Holds a single
    /// ChunkInstance (8 bytes) and gets rewritten for each chunk
    /// draw call. Creating buffers per-frame was the #1 perf hazard.
    instance_buffer:     wgpu::Buffer,
}

impl Renderer {
    /// Create the renderer. Blocks on GPU adapter/device creation (via pollster).
    ///
    /// Takes ownership of nothing from the sim — render only reads sim data
    /// through the update_chunk() method.
    pub fn new(window: std::sync::Arc<winit::window::Window>) -> Renderer {
        // Block on async wgpu init. pollster::block_on is fine for startup.
        pollster::block_on(Self::new_async(window))
    }

    async fn new_async(window: std::sync::Arc<winit::window::Window>) -> Renderer {
        let size = window.inner_size();

        // ── wgpu instance + surface ───────────────────────────────────────
        let instance = wgpu::Instance::new(&wgpu::InstanceDescriptor {
            backends: wgpu::Backends::PRIMARY,
            ..Default::default()
        });

        let surface = instance.create_surface(window.clone()).unwrap();

        // ── Adapter: pick the best GPU available ──────────────────────────
        let adapter = instance
            .request_adapter(&wgpu::RequestAdapterOptions {
                power_preference: wgpu::PowerPreference::HighPerformance,
                compatible_surface: Some(&surface),
                force_fallback_adapter: false,
            })
            .await
            .expect("failed to find a GPU adapter");

        log::info!("GPU adapter: {:?}", adapter.get_info().name);

        // ── Device + queue ────────────────────────────────────────────────
        let (device, queue) = adapter
            .request_device(&wgpu::DeviceDescriptor {
                label: Some("verdant-device"),
                required_features: wgpu::Features::empty(),
                required_limits: wgpu::Limits::default(),
                ..Default::default()
            }, None)
            .await
            .expect("failed to create GPU device");

        // ── Surface config ────────────────────────────────────────────────
        let surface_caps = surface.get_capabilities(&adapter);
        let format = surface_caps.formats.iter()
            .find(|f| f.is_srgb())
            .copied()
            .unwrap_or(surface_caps.formats[0]);

        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format,
            width: size.width.max(1),
            height: size.height.max(1),
            present_mode: wgpu::PresentMode::AutoVsync,
            alpha_mode: surface_caps.alpha_modes[0],
            view_formats: vec![],
            desired_maximum_frame_latency: 2,
        };
        surface.configure(&device, &config);

        // ── Shader ────────────────────────────────────────────────────────
        let shader_src = include_str!("../../../assets/shaders/chunk.wgsl");
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("chunk-shader"),
            source: wgpu::ShaderSource::Wgsl(shader_src.into()),
        });

        // ── Camera uniform buffer + bind group ────────────────────────────
        let camera_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("camera-uniform"),
            size: 64, // mat4x4<f32> = 16 floats × 4 bytes = 64 bytes
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        let camera_bind_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("camera-bind-layout"),
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::VERTEX,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            }],
        });

        let camera_bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("camera-bind-group"),
            layout: &camera_bind_layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: camera_buffer.as_entire_binding(),
            }],
        });

        // ── Chunk texture bind group layout (group 1) ─────────────────────
        let chunk_bind_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("chunk-bind-layout"),
            entries: &[
                // @binding(0): chunk_texture
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                // @binding(1): chunk_sampler
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });

        // ── Nearest-neighbor sampler (pixel art — no blurring) ────────────
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("chunk-sampler"),
            address_mode_u: wgpu::AddressMode::ClampToEdge,
            address_mode_v: wgpu::AddressMode::ClampToEdge,
            mag_filter: wgpu::FilterMode::Nearest, // pixel-perfect at all zoom levels
            min_filter: wgpu::FilterMode::Nearest,
            ..Default::default()
        });

        // ── Pipeline layout ───────────────────────────────────────────────
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("chunk-pipeline-layout"),
            bind_group_layouts: &[&camera_bind_layout, &chunk_bind_layout],
            push_constant_ranges: &[],
        });

        // ── Vertex buffer layout ──────────────────────────────────────────
        let vertex_buffer_layout = wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<QuadVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &[
                // @location(0): position
                wgpu::VertexAttribute {
                    offset: 0,
                    shader_location: 0,
                    format: wgpu::VertexFormat::Float32x2,
                },
                // @location(1): uv
                wgpu::VertexAttribute {
                    offset: 8,
                    shader_location: 1,
                    format: wgpu::VertexFormat::Float32x2,
                },
            ],
        };

        // ── Instance buffer layout (per-chunk offset) ─────────────────────
        let instance_buffer_layout = wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<ChunkInstance>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Instance,
            attributes: &[
                // @location(2): chunk_offset
                wgpu::VertexAttribute {
                    offset: 0,
                    shader_location: 2,
                    format: wgpu::VertexFormat::Float32x2,
                },
            ],
        };

        // ── Render pipeline ───────────────────────────────────────────────
        let render_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("chunk-pipeline"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_main"),
                buffers: &[vertex_buffer_layout, instance_buffer_layout],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_main"),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    blend: Some(wgpu::BlendState::REPLACE),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState {
                topology: wgpu::PrimitiveTopology::TriangleList,
                strip_index_format: None,
                front_face: wgpu::FrontFace::Ccw,
                cull_mode: None, // 2D quads — no backface culling
                polygon_mode: wgpu::PolygonMode::Fill,
                unclipped_depth: false,
                conservative: false,
            },
            depth_stencil: None, // 2D — no depth buffer
            multisample: wgpu::MultisampleState::default(),
            multiview: None,
            cache: None,
        });

        // ── Vertex buffer (unit quad, never changes) ──────────────────────
        let vertex_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("quad-vertex-buffer"),
            size: (std::mem::size_of::<QuadVertex>() * QUAD_VERTICES.len()) as u64,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        queue.write_buffer(&vertex_buffer, 0, bytemuck::cast_slice(QUAD_VERTICES));

        // Reusable instance buffer — holds one ChunkInstance per draw call.
        // Rewritten each chunk instead of allocating a new buffer.
        let instance_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("chunk-instance-buf"),
            size: std::mem::size_of::<ChunkInstance>() as u64,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        Renderer {
            surface,
            device,
            queue,
            config,
            render_pipeline,
            vertex_buffer,
            camera_buffer,
            camera_bind_group,
            chunk_bind_layout,
            sampler,
            chunk_textures: HashMap::new(),
            pixel_buf: vec![0u8; CHUNK_WIDTH * CHUNK_HEIGHT * 4],
            instance_buffer,
        }
    }

    // ── Window resize ─────────────────────────────────────────────────────

    /// Call when the window is resized. Reconfigures the swap chain.
    pub fn resize(&mut self, width: u32, height: u32) {
        if width == 0 || height == 0 { return; }
        self.config.width = width;
        self.config.height = height;
        self.surface.configure(&self.device, &self.config);
    }

    // ── Chunk texture management ──────────────────────────────────────────

    /// Upload a chunk's cell data as a GPU texture. Call once per visible chunk per frame.
    ///
    /// CPU-side cell_to_rgba converts each Cell to RGBA, then we write_texture
    /// to upload the pixel data to the GPU.
    pub fn update_chunk(&mut self, coord: ChunkCoord, cells: &[Cell]) {
        debug_assert_eq!(cells.len(), CHUNK_WIDTH * CHUNK_HEIGHT);

        // CPU-side color conversion: Cell → RGBA pixels
        // Reuse the pixel buffer to avoid allocation.
        for (i, cell) in cells.iter().enumerate() {
            let rgba = cell_color::cell_to_rgba(cell);
            let base = i * 4;
            self.pixel_buf[base    ] = rgba[0];
            self.pixel_buf[base + 1] = rgba[1];
            self.pixel_buf[base + 2] = rgba[2];
            self.pixel_buf[base + 3] = rgba[3];
        }

        // Ensure GPU texture exists for this chunk.
        let gpu_data = self.chunk_textures.entry(coord).or_insert_with(|| {
            let texture = self.device.create_texture(&wgpu::TextureDescriptor {
                label: Some("chunk-texture"),
                size: wgpu::Extent3d {
                    width: CHUNK_WIDTH as u32,
                    height: CHUNK_HEIGHT as u32,
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: wgpu::TextureFormat::Rgba8UnormSrgb,
                usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
                view_formats: &[],
            });

            let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
            let bind_group = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
                label: Some("chunk-bind-group"),
                layout: &self.chunk_bind_layout,
                entries: &[
                    wgpu::BindGroupEntry {
                        binding: 0,
                        resource: wgpu::BindingResource::TextureView(&view),
                    },
                    wgpu::BindGroupEntry {
                        binding: 1,
                        resource: wgpu::BindingResource::Sampler(&self.sampler),
                    },
                ],
            });

            ChunkGpuData { texture, bind_group }
        });

        // Upload pixel data to the texture.
        self.queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &gpu_data.texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &self.pixel_buf,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(CHUNK_WIDTH as u32 * 4),
                rows_per_image: Some(CHUNK_HEIGHT as u32),
            },
            wgpu::Extent3d {
                width: CHUNK_WIDTH as u32,
                height: CHUNK_HEIGHT as u32,
                depth_or_array_layers: 1,
            },
        );
    }

    /// Free the GPU texture for a chunk that has been evicted from sim.
    pub fn evict_chunk(&mut self, coord: ChunkCoord) {
        self.chunk_textures.remove(&coord);
    }

    // ── Frame rendering ───────────────────────────────────────────────────

    /// Render one frame: draw all chunks that have uploaded textures.
    ///
    /// `camera` provides the view-projection matrix and viewport culling.
    /// Chunks outside the viewport are skipped (no draw call emitted).
    pub fn present(&mut self, camera: &Camera) {
        // Upload camera matrix to GPU.
        let matrix = camera.view_proj_matrix();
        self.queue.write_buffer(
            &self.camera_buffer,
            0,
            bytemuck::cast_slice(&matrix),
        );

        // Get the next swap chain texture.
        let output = match self.surface.get_current_texture() {
            Ok(tex) => tex,
            Err(wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated) => {
                self.surface.configure(&self.device, &self.config);
                return; // skip this frame
            }
            Err(e) => {
                log::error!("swap chain error: {e:?}");
                return;
            }
        };

        let view = output.texture.create_view(&wgpu::TextureViewDescriptor::default());

        let mut encoder = self.device.create_command_encoder(&wgpu::CommandEncoderDescriptor {
            label: Some("frame-encoder"),
        });

        {
            let mut render_pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("chunk-render-pass"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        // Clear to dark void — the dead planet background.
                        load: wgpu::LoadOp::Clear(wgpu::Color {
                            r: 0.02,
                            g: 0.02,
                            b: 0.04,
                            a: 1.0,
                        }),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
            });

            render_pass.set_pipeline(&self.render_pipeline);
            render_pass.set_bind_group(0, &self.camera_bind_group, &[]);
            render_pass.set_vertex_buffer(0, self.vertex_buffer.slice(..));

            // Viewport culling: only draw chunks currently on screen.
            let (min_cx, min_cy, max_cx, max_cy) = camera.visible_chunk_range();

            for (&coord, gpu_data) in &self.chunk_textures {
                // Cull chunks outside the viewport.
                if coord.cx < min_cx || coord.cx > max_cx
                    || coord.cy < min_cy || coord.cy > max_cy
                {
                    continue;
                }

                // Write this chunk's world offset into the reusable instance buffer.
                let instance = ChunkInstance {
                    chunk_offset: [
                        (coord.cx as f32) * CHUNK_WIDTH as f32,
                        (coord.cy as f32) * CHUNK_HEIGHT as f32,
                    ],
                };
                self.queue.write_buffer(&self.instance_buffer, 0, bytemuck::bytes_of(&instance));

                render_pass.set_bind_group(1, &gpu_data.bind_group, &[]);
                render_pass.set_vertex_buffer(1, self.instance_buffer.slice(..));
                render_pass.draw(0..QUAD_VERTICES.len() as u32, 0..1);
            }
        }

        self.queue.submit(std::iter::once(encoder.finish()));
        output.present();
    }
}
