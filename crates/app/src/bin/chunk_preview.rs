// chunk_preview.rs — visual inspection tool for worldgen output
//
// Generates a handful of chunks at interesting coordinates, converts each
// cell to RGBA via cell_color::cells_to_rgba(), and writes 512×512 PNGs to
// test_output/ in the repo root.
//
// Run with:
//   cargo run --bin chunk_preview
//
// Output: test_output/chunk_<cx>_<cy>_<sector>.png

use std::path::Path;

use verdant_render::cell_color::cells_to_rgba;
use verdant_sim::chunk::ChunkCoord;
use verdant_sim::worldgen;

fn main() {
    let out_dir = Path::new("test_output");
    std::fs::create_dir_all(out_dir).expect("failed to create test_output/");

    // Chunks to preview — chosen to cover distinct geological contexts.
    //
    // Sectors (horizontal bands, WORLD_WIDTH_CHUNKS = 128):
    //   cx 0–15:  Origin        cx 16–31: Volcanic   cx 32–47: MineralRich
    //   cx 48–63: DeepWater     cx 64–79: Debris      cx 80–95: Volcanic
    //
    // Depth profile (cy):
    //   cy < 0: sky            cy 0: surface          cy 1: machine pocket (cx=0 only)
    //   cy 2–4: rock/cave      cy 5–10: deep rock      cy 10+: geothermal
    //   cy >= 16: lava core
    let chunks: &[(i32, i32, &str)] = &[
        (0,  0,  "origin_surface"),     // Sky/surface transition; origin sector
        (0,  1,  "machine_pocket"),     // Elliptical cavity at (cx=0,cy=1)
        (20, 2,  "volcanic_shallow"),   // Volcanic sector, shallow rock/cave layer
        (5,  8,  "deep_rock"),          // Origin sector, deep rock with heat
        (40, 4,  "mineral_rich_mid"),   // MineralRich sector, ore-bearing mid-depth
        (0,  16, "lava_core"),          // Below the sim — solid max-heat sentinel
    ];

    let mut generated = Vec::new();

    for &(cx, cy, label) in chunks {
        let coord = ChunkCoord::new(cx, cy);
        let chunk = worldgen::generate(coord);
        let cells = chunk.front_slice();

        // Cell → RGBA pixel buffer (512 * 512 * 4 bytes)
        let pixels = cells_to_rgba(cells);

        let filename = format!("chunk_{cx:+}_{cy:+}_{label}.png");
        let filepath = out_dir.join(&filename);

        image::save_buffer(
            &filepath,
            &pixels,
            512,
            512,
            image::ColorType::Rgba8,
        ).unwrap_or_else(|e| panic!("failed to write {filename}: {e}"));

        let abs = std::fs::canonicalize(&filepath)
            .unwrap_or_else(|_| filepath.clone());
        println!("wrote {}", abs.display());
        generated.push(abs);
    }

    println!("\n{} PNGs written to test_output/", generated.len());
}
