use verdant_render::Renderer;
use verdant_sim::chunk::ChunkCoord;
use verdant_sim::chunk_manager::ChunkManager;

fn main() {
    // Active radius 1 = 3×3 chunks around the player. Expand as needed.
    let mut world = ChunkManager::new(1);
    let renderer  = Renderer::new();

    // Place the player at the origin chunk. This triggers discovery of the
    // surrounding 3×3 zone — the first 9 chunks generate procedurally.
    let origin = ChunkCoord::new(0, 0);
    world.set_player_chunk(origin);

    println!(
        "Verdant — {} chunks loaded, {} bytes/chunk (double-buffered)",
        world.loaded_count(),
        verdant_sim::chunk::CHUNK_AREA * std::mem::size_of::<verdant_sim::cell::Cell>() * 2,
    );

    // Stub game loop: one high-frequency tick + one daily pass for smoke-testing.
    world.tick_high_frequency();
    world.tick_daily_pass();
    renderer.present(&world);

    println!("OK — sim loop runs, {} chunks still loaded", world.loaded_count());
}
