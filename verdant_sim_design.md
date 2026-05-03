# VERDANT Physics Sim — Design

**Author:** Adrian Neill (toastee)
**Target hardware:** RTX 3090 / 3090 Ti class (24 GB VRAM) + 32 GB+ system RAM, Windows 11
**Engine:** Rust host, wgpu/CUDA compute kernels

---

## Status

**This document is the canonical physics design specification for VerdantSim
(gen5 and forward).** When this document and any other doc disagree, this
document wins.

Hierarchy of authority (most → least authoritative):

1. **`verdant_sim_design.md`** *(this file)* — physics design intent.
2. **`gen5_implementation_spec.md`** — the architectural commitment catalog
   derived from this doc; resolves implementation ambiguities into concrete
   numbers and structures the reference simulator must satisfy.
3. **`gen5_roadmap.md`** — milestone sequencing for the implementation.
4. **`HANDOFF.md`** — current session state and live progress.
5. **Live code** in `reference_sim_v2/` — ground truth for what is actually
   implemented today; may lag this doc when new design ideas are recorded
   here ahead of implementation.

Anything in `wiki_archive/` is superseded by this document and must not be
cited as authoritative.

When the design here changes, the change lands in this doc first; the
implementation spec, roadmap, and code are then brought into alignment in
subsequent commits.

---

## Core principle: the hardware is the substrate

**VRAM and compute cores are the fabric of this simulated universe.**

This is not an optimization strategy. It is the architectural intent. Every physical law in VERDANT is designed such that its fundamental operation is a natural operation of the hardware substrate. The sim does not implement physics *on top of* a GPU; the GPU's native operations *are* the physics, given physics-shaped names.

Parallel local computation over small stencils with high memory bandwidth is what a modern GPU does natively. It is also what the laws of physics appear to be: local, parallel, conservation-respecting. The design thesis is that this match is not coincidence — the fabric of the real universe and the fabric of VRAM+compute share a deep structural isomorphism, and a universe built from hardware-native primitives will produce recognizable physics for free because both run on the same shape of computation.

Every design decision in this document follows from this principle:

- **Jacobi diffusion over Gauss-Seidel** — Jacobi is pure-parallel with no data dependencies, matching how thousands of GPU cores execute without coordination.
- **Cells indivisible at Planck length** — The hardware's natural unit of parallel work is the thread. Each thread owns one cell. No sub-cell geometry means no per-thread branching on internal structure.
- **Blind flux summation** — Atomic contention is the enemy of GPU throughput. Blind summation eliminates atomics; each contribution adds independently.
- **Overlapping regions compute independently** — GPU memory bandwidth is abundant; synchronization is expensive. Reading the same cell from multiple threads beats making threads wait.
- **42 as the fundamental unit** — 6 × 7 is the native arithmetic of hex topology. Divisions are exact; values stay in integer-friendly ranges that map cleanly to GPU integer ops.
- **Border-seeded gravity diffusion** — Diffusion is another Jacobi sweep. It maps natively to SIMT execution; Barnes-Hut octrees do not.
- **Scenario bounds enforced at setup** — Kernels optimized for a known numerical envelope run faster than kernels defending against arbitrary input. The GPU rewards code that makes promises about its inputs.
- **Tiered memory (hot VRAM → warm VRAM → cold sysRAM)** — The hardware is tiered. The sim's memory model mirrors the hardware's directly rather than pretending otherwise.
- **Concurrent phases with Tail-at-Scale culling** — GPUs execute in warps. Making warps wait for stragglers is the worst possible pattern. Culling is hardware-aware discipline.

The emergent behaviors VERDANT produces — surface tension from cohesion, hydrostatic pressure from gravity-on-motion, rock metamorphism from Mohs ratcheting, precipitation from phase-diagram transitions, aquifers from under-dense trapped water, lagrange points from multi-source gravity diffusion — are not "we encoded real physics into the simulation." They are "we built the substrate correctly according to its hardware nature, and real-physics-like behavior fell out because the real universe appears to also run on local, parallel, conservation-respecting primitives."

When a design question arises, the question to ask is: *what is the hardware naturally good at, and can the physics be expressed as that operation?* If yes, it belongs. If no, either the design needs to be reshaped to match the hardware, or the hardware is the wrong substrate for this physics.

Everything that follows in this document is an application of this principle.

---

## Target hardware

The sim is developed and validated against three specific GPUs:

- **RTX 3090 (GA102, Ampere).** 24 GB VRAM, 936 GB/s bandwidth, 10,496 CUDA cores. The largest testbed.
- **RTX 4060 Ti 16 GB (AD106, Ada Lovelace).** 16 GB VRAM, 288 GB/s raw bandwidth, 4,352 CUDA cores, **32 MB L2 cache**. Mid-size testbed with Ada's cache-heavy architecture.
- **RTX 4060 8 GB (AD107, Ada Lovelace).** 8 GB VRAM, 272 GB/s bandwidth, 3,072 CUDA cores, 24 MB L2. The smallest testbed; validates the bottom end of the scaling envelope.

These three span a useful range: Ampere vs Ada, wide bus vs cache-heavy, 24 GB down to 8 GB. Kernels and scenarios are verified on all three. Other sm_86+ GPUs (Ampere / Ada / later) should work since they share the architectural features the kernels rely on, but only these three are actively validated.

Pre-Ampere GPUs (Pascal and earlier) are not supported. They lack features the kernels assume (doubled FP32 per SM, third-generation tensor cores, async copy instructions), and NVIDIA has dropped driver support for Pascal in current production branches.

Architectural changes that would distort the physics to accommodate any specific card are not made. Degradations that scale scenario scope (grid size, cycle rate, active area sizing) while preserving the physics are acceptable.

### Hardware specifications

| | RTX 3090 | RTX 4060 Ti 16 GB | RTX 4060 8 GB |
|---|---|---|---|
| Die | GA102 (Ampere) | AD106 (Ada) | AD107 (Ada) |
| Compute capability | sm_86 | sm_89 | sm_89 |
| GPCs | 6 | 3 | 3 |
| SMs | 82 | 34 | 24 |
| CUDA cores | 10,496 | 4,352 | 3,072 |
| Boost clock | ~1,695 MHz | ~2,535 MHz | ~2,460 MHz |
| FP32 peak | 35.6 TFLOPS | 22.1 TFLOPS | 15.1 TFLOPS |
| VRAM | 24 GB GDDR6X | 16 GB GDDR6 | 8 GB GDDR6 |
| Memory bus | 384-bit | 128-bit | 128-bit |
| Raw bandwidth | 936 GB/s | 288 GB/s | 272 GB/s |
| Effective bandwidth (cache-aware) | ~936 GB/s | ~554 GB/s (NVIDIA figure) | ~520 GB/s (est.) |
| L2 cache | 6 MB | **32 MB** | **24 MB** |
| L1/shared per SM | 128 KB | 128 KB | 128 KB |
| Register file per SM | 256 KB | 256 KB | 256 KB |
| Warps resident per SM | 48 | 48 | 48 |
| Max threads per SM | 1,536 | 1,536 | 1,536 |
| PCIe link | 4.0 × 16 (~25 GB/s) | 4.0 × 8 (~16 GB/s) | 4.0 × 8 (~16 GB/s) |
| TGP | 350 W | 165 W | 115 W |

**FP64 is 1/64th of FP32 on all three cards** — unusable. All sim math is f32 throughout. Accidental f64 constants in kernels are a performance trap to watch for.

### Per-SM structure (common across all targets)

Each SM contains:
- **4 processing blocks**, each with its own warp scheduler and dispatch unit
- **32 FP32 CUDA cores per block** (half of which also handle INT32), so 128 FP32 or 64 INT32 ops per SM per clock
- **1 Tensor Core per block** (3rd-gen on Ampere, 4th-gen on Ada) for fp16/bf16/tf32 matmul; not used by core physics kernels but available
- **1 RT Core per SM**; not used by core physics kernels
- **128 KB L1/shared memory**, configurable — compute-mode typically gives up to 100 KB explicit shared memory per SM
- **256 KB register file** (64 KB per processing block)

**Warp size is 32 threads** on all targets, executing in SIMT lockstep. Divergent branches within a warp serialize execution.

### The Ada cache bet

Ada Lovelace made a fundamental strategic shift from Ampere: instead of scaling memory bandwidth with die size, Ada pays for a **very large L2 cache** and bets that well-designed workloads will hit L2 instead of GDDR. For VERDANT this is consequential:

- **32 MB of L2** (4060 Ti) or **24 MB** (4060) holds hundreds of thousands of cells at ~120 bytes/cell — easily the active working set of a focused gameplay region.
- **Overlapping regions are L2-friendly by construction.** Our design already relies on shared cells being re-read from L2 by overlapping tiles rather than re-fetched from GDDR. Ada's 4-5× larger L2 (vs the 3090's 6 MB) means this amortization is dramatically more effective.
- **For small-to-medium active regions**, Ada cards can approach 3090 effective throughput despite having ~30% of the raw bandwidth, because memory traffic stays inside L2. NVIDIA's "288 GB/s ≈ 554 GB/s effective" claim on the 4060 Ti captures this.
- **For large active regions that exceed L2**, the Ada cards fall back to their modest raw bandwidth and become bottlenecked. Crossover on the 4060 Ti is around active-working-sets of ~520×520 hex cells; on the 4060 8 GB, around 450×450.

The design does not need special-case kernels per card. The same stencil kernels that are L2-friendly on the 3090 are more L2-friendly on Ada. The difference is in scenario sizing: a game running on Ada cards should be designed with active play areas that fit in L2 and treat distant regions as cold-tier or reduced-fidelity.

### Universe-size allocation

At initialization the engine measures available VRAM, reserves 2 GB for host OS and other applications, and claims the remainder as the sim substrate. At ~120 bytes per cell packed and double-buffered:

| Card | Substrate | Hot-tier capacity | Approx. hex grid |
|---|---|---|---|
| RTX 3090 24 GB | ~22 GB | ~90 M cells | **~9,500 × 9,500** |
| RTX 4060 Ti 16 GB | ~14 GB | ~58 M cells | **~7,600 × 7,600** |
| RTX 4060 8 GB | ~6 GB | ~25 M cells | **~5,000 × 5,000** |

Larger grids are accommodated by memory tiering, at the cost of PCIe bandwidth for hot/cold transitions. PCIe 4.0 × 8 (on both Ada cards) is half the 3090's × 16 link, so cold-tier traffic is more expensive on Ada; designs should keep more content hot on those cards.

### Mapping VERDANT onto the hardware

**Regions map to thread tiles, not individual threads.** A single 7-cell region mapped to 7 threads would waste an SM (one warp minimum is 32 threads). Instead, a **CTA processes a tile of many regions** — one thread per cell, 32 threads per warp, multiple warps per CTA. Within a CTA, shared memory holds the tile's cells plus flux scratch for one sub-pass, and warp-level primitives (`__shfl_sync`, warp reductions) move neighbor data efficiently between threads.

**Warp discipline — phase homogeneity matters.** SIMT warps execute in lockstep; divergent branches within a warp serialize execution. Since phases have different transport rules, **dispatch groups regions by dominant phase** so a warp contains all-gas or all-liquid or all-solid cells. Tail-at-Scale culling benefits the same way: a warp can cull as a unit when all its cells are below the noise floor.

**Overlapping regions are L2-friendly by construction.** A cell read by region A's tile gets fetched from GDDR; when the same cell is read by an overlapping region B's tile, it hits L2 at much higher effective bandwidth. This pays off especially well on Ada where L2 is 4-5× larger.

**Shared memory bank layout.** The 100 KB of shared memory per SM is organized into 32 banks. Cell data layout within a tile is chosen so that simultaneous neighbor reads across a warp hit different banks, avoiding bank conflicts. Kernel-implementation detail resolved at code-write time.

### Per-cycle throughput envelope

Memory-bandwidth-bound stencil workloads scale with effective bandwidth — GDDR when the working set exceeds L2, L2 bandwidth when it fits.

**Working set exceeds L2 (large grids, diffuse activity):**

| Card | Theoretical | Achieved (~75%) | 5k×5k grid, 5 sub-passes | 1k×1k grid |
|---|---|---|---|---|
| RTX 3090 | 7.3 B cells/s | 5.5 B cells/s | ~44 cycles/s | ~1,100 cycles/s |
| RTX 4060 Ti 16 GB | 2.3 B cells/s | 1.7 B cells/s | ~14 cycles/s | ~340 cycles/s |
| RTX 4060 8 GB | 2.1 B cells/s | 1.6 B cells/s | ~13 cycles/s | ~320 cycles/s |

**Working set fits in L2 (focused gameplay, active area under ~520×520 on Ti, ~450×450 on 4060):**

- L2 bandwidth is several terabytes/sec — effectively compute-bound rather than bandwidth-bound
- Ada cards can approach or match 3090 for these scenarios
- For interactive gameplay with a focused play area, this is the practical regime on Ada

Render cadence decouples from sim cadence. At 60 Hz render, the larger grids see multiple sim cycles per render frame.

### Scaling discipline

**Fixed across all targets** (not compromised for hardware):
- Phase density equilibrium centers (42 / 1,764 / 74,088)
- Pass counts per phase (3 / 5 / 7)
- Cohesion semantics
- Gravity diffusion architecture
- Tiering model (hot VRAM / warm VRAM / cold sysRAM)
- f32 working precision with packed canonical state

**Scales gracefully with hardware:**
- Grid size (derived from VRAM measurement at init)
- Cycles per second (proportional to effective memory bandwidth)
- Scenario complexity (bounded by the throughput envelope)
- Cold-tier use (constrained by PCIe bandwidth; less aggressive on narrower links)
- Active play area (sized to fit in L2 on cache-rich targets for best performance)

**Compute capability floor: sm_86.** Ampere and later. Earlier architectures lack doubled FP32 per SM, third-generation tensor cores, and async copy instructions that the kernels assume. Pascal / Turing support is not planned.

---

## Implementation patterns

The sim commits to a specific set of CUDA/PTX-level patterns that the kernels are designed around. These are architectural choices — they affect how state is laid out in memory, how kernels are structured, and how work is scheduled — not micro-optimizations. They are documented here so that kernel implementation can proceed without re-deriving each choice.

### Multistage asynchronous-copy pipeline

The sim uses `cp.async` (hardware-accelerated global→shared copy, sm_80+) with async split-barriers to overlap memory movement with compute. While a CTA is computing on tile N of the canonical grid, the next tile (N+1) is being prefetched into a separate shared-memory buffer. When compute finishes, the CTA arrives-and-waits on the prefetch barrier, swaps buffers, and begins computing on N+1 while N+2 prefetches.

This hides GDDR read latency entirely when the pipeline is deep enough. Pre-Ampere architectures staged data through registers on the load path, consuming registers and blocking the warp; `cp.async` bypasses registers entirely.

Cell state is aligned to 16 bytes where possible to enable the fastest `cp.async` variant. The per-cell packed layout is designed around this alignment.

### Warp shuffles for neighbor access

Flux computation at region boundaries uses warp shuffle primitives (`__shfl_sync`, `__shfl_up_sync`, `__shfl_down_sync`, `__shfl_xor_sync`) rather than shared-memory halo reads. Threads within a warp read each other's registers directly at single-instruction latency — no shared-memory round-trip, no bank conflict concerns, no synchronization overhead.

Hex-topology neighbor access maps to shuffles as follows:
- N/S neighbors: shuffle with row-stride offset
- NE/SW and NW/SE: shuffle with offsets that depend on row parity (pointy-top hex geometry)
- Region-level reductions (flux summation, cohesion aggregation): `__shfl_xor_sync` butterfly reduction

Shared memory still holds the canonical working state for the tile; shuffles handle the six-way neighbor reads during the computation phase. This keeps shared memory traffic coalesced and avoids bank conflicts for neighbor exchange.

### Shared memory carveout: 100 KB per SM

Each SM's 128 KB of L1/shared memory is configurable at runtime via `cudaFuncSetAttribute`. The sim requests the maximum shared memory allocation (100 KB per CTA after the 1 KB CUDA reserve), minimizing L1 cache in exchange. This is appropriate because:

- The tile-of-regions working set is explicitly managed in shared memory; L1 caching of the same data would be redundant
- The canonical grid is accessed with coalesced patterns that benefit more from L2 than from L1
- Register pressure is the constraint on occupancy, not L1 hit rate

Static shared-memory allocations above 48 KB require the explicit opt-in (dynamic allocation with the appropriate attribute). The sim uses dynamic allocation for the tile buffers.

### Structure-of-Arrays cell layout

Canonical grid state is laid out as Structure-of-Arrays across cells rather than Array-of-Structures. Each cell-state field (composition slot 0, composition slot 1, ..., pressure, temperature, energy, mohs, petal 0 stress, ...) gets its own contiguous array in global memory, indexed by cell coordinate.

This means when a warp of 32 threads reads the pressure field for 32 contiguous cells, the hardware coalesces the access into a single 128-byte transaction. Reading all cell state in Array-of-Structures layout would instead scatter across ~32 separate transactions, collapsing effective bandwidth.

Within a cell's state arrays, field order is chosen so that fields accessed together in the kernel are also adjacent in the SoA layout (improves L2 line utilization).

### Phase-homogeneous warp dispatch

SIMT warps execute in lockstep; divergent branches within a warp serialize execution. Since each phase has its own transport rule (gas averages, liquid gravity-biases, solid yields discretely, plasma thermally dominates), regions are grouped by dominant phase at dispatch time so that a warp processes 32 cells of the same phase. A warp processing all-gas cells takes only the gas branch; the solid, liquid, and plasma branches contribute zero divergence cost.

Mixed-phase cells (wet sand, foam, magma) are handled at a coarser scheduling granularity — they run each phase's sub-pass schedule independently, and the warp running the "solid fraction of wet sand" pass is phase-homogeneous for that pass even if the cell itself is not.

Tail-at-Scale culling benefits from the same grouping: a warp whose cells are all at equilibrium can cull together as a unit rather than some lanes culling and others continuing.

### Dual-architecture compilation

The sim is compiled as cubin for both target architectures (sm_86 for GA102, sm_89 for AD106/AD107), plus a PTX fallback for forward compatibility with future Ampere / Ada / Hopper / Blackwell / successor architectures. The build invocation:

```
-gencode arch=compute_86,code=sm_86   # 3090 native
-gencode arch=compute_89,code=sm_89   # 4060 and 4060 Ti native
-gencode arch=compute_89,code=compute_89   # PTX forward-compat
```

sm_89 cubin gives the 4060/Ti the 2× FP32-per-cycle-per-SM rate introduced at compute capability 8.6+ (applies to both sm_86 and sm_89; omitting the explicit gencode would fall back to the sm_80 path which lacks this).

### Deferred optimizations

Patterns that may yield further gains but are not architectural commitments at this stage:

- **L2 persistence windows** (`cudaAccessPolicyWindow`). The 4060 Ti's 32 MB L2 and 4060's 24 MB L2 could be configured to pin frequently-read global fields (gravity vector field, overburden field, border metadata) for prioritized retention. On the 3090's 6 MB L2, persistence likely evicts more than it retains and is not useful. Revisit after baseline profiling.
- **Tensor Core offload** for matmul-shaped subproblems. Gravity Jacobi iteration can be framed as sparse matmul; cohesion aggregation could use fp16 tensor ops. Not architecturally required; explore if profiling identifies a bottleneck.
- **Thread block clusters** (sm_90+). Not available on any target card.

---

## State representation

### Grid

Pointy-top hex grid in axial coordinates `(q, r)`. Each cell has six neighbors in the directions N, NE, SE, S, SW, NW.

### Per-cell state

A cell holds, at minimum:

- **Composition vector:** fractional presence of material species. 16-slot `[(element_id: u8, fraction: u8), × 16]`. Fractions normalized to sum to 255. Targets the full periodic table (118 elements) with graceful degradation: when more than 16 species are present, smallest fractions merge into the nearest existing slot by element similarity, or into a final "trace" slot.
- **Phase distribution:** fractional presence of phases (solid, liquid, gas, plasma). Fractions sum to ≤ 1.0. The vacuum fraction is the complement: `vacuum_fraction = 1.0 - (solid + liquid + gas + plasma)`. Vacuum is not stored; it is the absence of phase content. Mixed-phase cells are supported directly (wet sand = solid+liquid, foam = liquid+gas, magma = liquid+solid).
- **Phase-fraction masses:** per-phase mass content within the cell. This is the quantity each phase fraction seeks to hold near its phase density equilibrium center (see below).
- **Pressure:** log-scale u16 encoding. Decoded to absolute pressure for arithmetic, re-encoded to u16 for grid-resident storage. Working state in region kernels is f32; encoding happens at integration boundaries. **Pressure is expressed as deviation from the phase density equilibrium center** — positive pressure is above center (mass wants to flow out), negative pressure is below center (mass wants to flow in).
- **Temperature:** absolute K, f32 working / u16 encoded.
- **Energy:** internal energy scalar. Temperature is derived from energy + heat capacity + composition.
- **Mohs level:** per-cell, per-solid-component. Starts from phase-diagram initial assignment; ratchets up under sustained compression.
- **Sustained-overpressure magnitude:** f32 integrator. Each cycle the cell is above its phase-density equilibrium threshold, the magnitude accumulates (pressure_excess × cycle_time). Below threshold, the magnitude decays toward zero. Ratcheting fires when the integrated magnitude crosses a trigger value. This representation encodes both how far over threshold and how long, without separate counters.
- **Petal data (6 slots per cell, one per neighbor direction):** persistent directional state. Each petal carries directional stress (the tax field), cohesion value toward that neighbor, accumulated velocity/momentum along that direction, and any other directional history the cell needs to maintain between cycles.

Working copies of cell state inside region kernels use f32 throughout. Canonical grid-resident state uses packed/log-encoded representations for VRAM efficiency.

No integer counters are used anywhere in the cell state. All persistent accumulation is expressed as magnitudes (f32 values) with explicit accumulation and decay rules. This aligns with how GPU hardware natively stores and updates state — multiply-accumulate is the fundamental GPU operation.

### Phases and density equilibrium centers

Four phases participate in the simulation: **solid, liquid, gas, plasma.** Each has a density equilibrium center — the mass density the phase seeks to maintain when unperturbed.

Values are hex-grid-native: **42 is the fundamental unit** because 42 = 6 × 7 divides cleanly by both the neighbor count (6) and the region cell count (7). Every averaging, redistribution, and region-kernel arithmetic operation is exact with 42-based values. No rounding residue, no conservation leaks to floating-point drift. (42 is also the Answer to Life, the Universe, and Everything. This is not accidental.)

| Phase   | Equilibrium center      | Pass count | Opportunistic | Notes |
|---------|-------------------------|-----------|----------------|-------|
| Plasma  | 42 mass units           | 3         | Yes            | Ionized state; aggressive thermal coupling to neighbors |
| Gas     | 42 mass units           | 3         | Yes            | Fills vacuum fast; composition mixes readily |
| Liquid  | 42 × 42 = 1,764         | 5         | Yes            | Gravity-biased flow; surface tension via cohesion |
| Solid   | 42 × 42 × 42 = 74,088   | 7         | No             | Stress-transmitting; yields discretely under compression |

Plasma and gas share their mass density center because ionization changes a gaseous medium's temperature and electromagnetic behavior but not its bulk density. Plasma is distinguished by its thermal signature (very high temperature, dominating local energy budget) and its interaction rules (transfers heat aggressively to neighbors, ablates adjacent solids, evaporates adjacent liquids), not by its mass density.

Equilibrium centers are **not hard caps in the physics** — a gas cell can exceed 42 units under compression (the overpressure drives outflow flux); a liquid cell can fall below 1,764 under tension (the undertension drives inflow flux). The deviation from center *is* the pressure.

However, the canonical packed encoding (log-scale u16) has a fixed representable range, so values that would exceed the encoded ceiling or fall below the encoded floor are clamped at the encoding boundary. This is overflow protection, not physics: the encoding simply cannot represent values outside its range, so the sim treats the boundary as a wall. Scenario bounds (see §Scenario bounds and validation) are set to prevent normal simulation from hitting these limits; when the limits are hit, it means either a scenario pushed past its design envelope or an instability emerged that needs debugging.

### Vacuum

Vacuum is **pressure at the encoding floor, not a separate phase.** A cell whose pressure has dropped to the lowest representable value is in vacuum, regardless of what phase fractions it nominally holds. Typically a vacuum cell has near-zero mass in all phase fractions (there is nothing left to push back), but the defining property is the pressure floor, not the absence of phase content.

This unifies vacuum handling with the rest of the pressure field. Vacuum is not a special case; it is the low-pressure limit of the normal pressure dynamics.

Pressure above the floor but very low represents *near-vacuum* states (thin atmospheres, rarefied regions). The transition from "thin" to "vacuum" is smooth — it is simply pressure continuing to drop until it hits the encoding floor.

### Per-element density scaling

The 42 / 1,764 / 74,088 values are phase-class defaults. Per-element composition scales them: iron is denser than water, water is denser than oil, helium is less dense than nitrogen. Per-element scaling factors are tabulated in the element table and applied multiplicatively to the phase-class center.

Gas density scales by molar mass — heavy gases (CO₂, SF₆) settle low; light gases (H₂, He) rise. Atmospheric stratification emerges without special code. Liquid densities are tabulated per element. Solid densities diverge strongly by composition and additionally scale by Mohs level (compressed rock is denser than uncompressed rock of the same composition).

Per-element scaling factors should be chosen to remain hex-arithmetic-friendly where possible (multiples of 6, 7, or 42), though strict adherence is unnecessary because per-element scaling is a per-cell constant multiplication, not a neighbor-averaging operation.

### Cross-phase dynamics (emergent behaviors)

The combination of phase density equilibrium centers, concurrent sub-passes per phase, cohesion, and computed-majority identity produces correct cross-phase behaviors without special-case code:

**Humid air.** A gas cell at ~42 units total, with (say) 15 of those units being water in gas phase. Majority is gas. Identity is humid air. Water composition rides along but does not dominate.

**Condensation.** Water vapor content in a gas cell, when phase diagram says "should be liquid at this P/T," flips to liquid phase during the phase-transition check. The converted liquid fraction is severely under-dense relative to liquid's 1,764 center. Liquid cohesion pulls the dispersed droplets toward cells with existing liquid water (high cohesion attractor), leaving the original cell with humid gas and the receiving cell with growing liquid mass. Droplet formation emerges.

**Evaporation.** Liquid cell next to a gas cell. During liquid's sub-passes, liquid computes diffusion fluxes. Gradient toward gas cell is high (water concentration much higher in liquid). Some water mass fluxes across as vapor (it's gas-phase water once in the gas cell). Liquid cell loses mass; gas cell gains water content. Humidity rises.

**Precipitation.** Accumulated water in gas cells reaches condensation threshold via phase diagram → flips to liquid → droplets coalesce via cohesion → eventually forms liquid cells or falls via momentum flux. Rain emerges.

**Ionization.** Gas cells reaching temperatures high enough for ionization (phase diagram lookup) transition to plasma. Plasma dumps its high thermal content into neighbors via aggressive energy flux, heating surroundings, potentially cascading further phase transitions.

**Ablation / plasma etch.** Plasma adjacent to solid: plasma's energy flux heats the solid aggressively; solid cells at the boundary reach melt/vaporize temperatures and transition to liquid or gas, then get absorbed into the plasma body or flow away. Solid mass is lost to the plasma over cycles.

**Trapped water / aquifers.** Rock cell at 74,088 solid units with ~1,764 units of liquid water fraction. Water is at its liquid-phase equilibrium density. The water is held by the surrounding rock matrix but will pressure outward seeking lower-pressure liquid paths during liquid sub-passes. If neighbor cells have similar liquid fractions, flux is small (low gradient). If neighbor is a gas-majority cell or another rock with less water, flux flows. Groundwater migration along low-resistance paths emerges.

**Springs / seepage.** An aquifer cell next to a surface (gas cell exterior) sees a steep gradient during liquid's sub-passes — water flows out. If the flow is sustained, the gas cell accumulates enough water to flip majority; now it's a liquid cell at the surface. A spring has appeared.

**Oil shale / volatiles in regolith.** Same mechanism as trapped water, with different species in the composition vector. Works identically.

None of these require special-case code. All emerge from phase density centers, cohesion, computed identity, diffusion during phase sub-passes, and phase-diagram transitions.

### Cell identity is computed, not stored

A cell does not have a "type" flag. Its identity — the phase + composition description used for cohesion calculations, rendering, and identity-dependent rules — is **computed each cycle from the phase-fraction masses and composition vector.** The majority phase by mass wins; the majority element within that phase wins; the result is the cell's current identity.

Identity transitions are smooth and continuous. A rock cell accumulating water shifts gradually; at the 50% threshold its computed majority flips from solid to liquid within a single cycle, but the underlying composition was already continuous approaching it. No state-change event to manage, no flag to invalidate, no "this cell is now water" transition code. The cell just answers "what's my majority" from current state each cycle.

### Flux records (scratch, per-cycle)

A flux record describes the boundary-integrated transport across one hex edge during one cycle. There are six edges per cell; conceptually each cell has six outgoing flux records, but actual storage is whatever is most efficient (edge-centric, cell-centric SoA, whatever the kernel needs — the layout is an implementation choice, not a required abstraction).

A flux record carries, at minimum:

- **Mass flux** — per species, per phase (so a single edge can carry H₂O liquid *and* CO₂ gas simultaneously; composition transport is preserved).
- **Momentum flux** — the velocity/momentum being transported with the mass, plus the pressure-work contribution (pressure × area × cycle-time).
- **Energy flux** — kinetic + thermal + pressure work.
- **Stress flux** — directional stress being transmitted across this edge this cycle. Updates the petal stress values on both sides during integration.
- **Phase-identity metadata** — enough information about what's flowing to allow the receiving cell's kernel to integrate it correctly (a gas flux arriving at a solid-dominated cell behaves differently than one arriving at a vacuum-dominated cell).

Flux records are computed fresh each cycle by the region kernels. They are zero-initialized at the start of the cycle, accumulated by blind summation from all contributing regions, and consumed by the integration kernel. **They do not persist across cycles.** The persistent effects (changed composition, updated petal stress, updated velocity) live in the cell state; the flux records themselves are scratch.

**"Carry as much information as we need to do the job."** The flux record schema is not fixed ceremonially; it expands to include whatever conserved quantities the physics requires. Fields not needed for a given configuration are zero and cost only their packed storage.

### Petal data (persistent, per-cell directional state)

Distinct from flux records. Each cell has six petals — one per neighbor direction — holding persistent directional state that survives between cycles:

- **Accumulated directional stress (tax)** — stress propagating through solids accumulates in the petal pointing toward the neighbor the stress is flowing toward. Stress relieves by onward transmission, yield events, or decay over time.
- **Directional velocity/momentum** — the cell's mass has directional motion along the six axes; petal velocity is the per-direction component of that motion.
- **Topology metadata** — flags and cached values describing the neighbor in that direction (is it a border, what kind of border, is it an inert region, etc.). Discovered on first contact; invariant thereafter for static topology.

Cohesion is *not* petal state — it is recomputed each cycle from current composition, so it exists only as a working value inside the region kernel during flux computation. Nothing derivable from the current canonical state needs to be persisted.

Flux records update petal values during integration. A flux record carrying stress across an edge increments both sides' petal stress. A flux record transporting momentum updates both sides' petal velocity components appropriately. Petals are the persistent home for directional per-cell state; flux records are the per-cycle transport mechanism that keeps them current.

### Cohesion (per-cell, per-direction damping)

Each cell computes, per neighbor direction, a **cohesion value** that measures how strongly it "wants to stay connected to" that neighbor based on composition similarity and its own purity. Cohesion is consumed by the cell's own flux computation as a damping coefficient: high cohesion across an edge suppresses outgoing mass/momentum flux (the cell resists tearing shared material apart).

**Formula (simplest form):**

```
cohesion(self, dir) = f(shared_majority_match(self.comp, neighbor.comp))
                    × g(self.purity)
```

Cohesion is **cell-local and blind**: a cell computes cohesion from its own composition, its own purity, and the neighbor's canonical composition read from the grid. It does not know the neighbor's cohesion value. There is no reciprocity constraint and no shared cohesion variable between cells.

**Asymmetric behavior emerges from the blind sum.** A pure water cell and an impure-water cell share majority composition, so both compute "high cohesion toward my neighbor" — but the pure cell's purity multiplier makes its cohesion stronger than the impure cell's. When their independent flux dampings sum across the edge, the asymmetry falls out correctly without any cell needing knowledge of the other's cohesion.

**Physical behaviors that emerge from cohesion:**

- **Surface tension.** Water cells next to air cells have low cohesion across the boundary (no shared majority composition). Flux across the water-air boundary is damped from both sides. The boundary holds; droplets round up; menisci form.
- **Immiscibility.** Two fluids with different majority compositions (oil vs water) have low inter-cohesion. Flux across their boundary is suppressed; they stay separate by default. Miscible liquids share composition more and mix readily.
- **Cleavage planes in solids.** A layered rock with alternating mineral bands has high intra-band cohesion and low inter-band cohesion. Stress fractures preferentially along low-cohesion planes. Mica-like cleavage is automatic.
- **Precipitation and crystal growth.** A solute-saturated liquid next to an existing nucleus of the same solid produces a cohesion gradient pulling solute mass toward the nucleus. Crystals grow from their own edges.
- **Blob maintenance.** A liquid droplet stays as a droplet because every interior water cell has high cohesion toward its water neighbors; interior fluxes are damped strongly, the blob holds shape against perturbation.

Cohesion is a per-cell per-direction scalar (6 values per cell), recomputed each cycle from local observation. It is a transient working value inside the region kernel — never stored in persistent cell state, never transported across edges. It exists only as a damping coefficient on the cell's own outgoing fluxes during that cycle's flux computation.

---

## Region kernels

### What a region is

A region is a local stencil kernel centered on one cell and covering that cell plus its six neighbors — a 7-cell hex flower.

Regions are *overlapping*: every cell in the grid is the center of its own region, and is a peripheral member of six others. Each cell participates in up to 7 different regions' computations per cycle (as center of its own, and as one of the six petals of each neighbor's region).

Regions do not share state during computation. Each region kernel reads the canonical grid state at the start of the cycle, computes its contribution in private working memory, and emits flux records to the scratch flux buffer. No coordination between regions during compute. This is the parallelism.

### What a region computes

For its center cell and its six neighbor directions, a region kernel:

1. Reads the canonical state of the center and all cells in its reach.
2. Computes local gradients: pressure, temperature, composition concentration, phase distribution, stress.
3. Determines, per direction and per conserved quantity, how much should flow across that edge this cycle given the gradients, the phase-dependent transport rules, and the material properties.
4. Writes its contribution to the flux records for the six edges it touches (additively — see §Flux summation).

Multiple overlapping regions contribute to the same edge. Their contributions sum. This is the blind-sum discipline.

### Concurrent phase sub-passes within a cycle

Gas, liquid, and solid phases run **concurrently** within a cycle, each advancing at its own pass budget. They do not run sequentially — the cycle does not wait for all gas passes to complete before liquid starts.

**Pass budgets per phase:**

- Gas: 3 sub-passes per cycle
- Liquid: 5 sub-passes per cycle
- Solid: 7 sub-passes per cycle

The cycle window is the longest budget (7 sub-passes for solid). Within that window, each phase advances on its own schedule:

- Sub-passes 1–3: all three phases active.
- Sub-pass 4–5: gas has hit its budget and frozen; liquid and solid still active.
- Sub-passes 6–7: liquid has hit its budget and frozen; solid still active.

When a phase hits its pass budget, it stops updating. Its state freezes for the remainder of the cycle. Further sub-passes by slower phases see that phase as static in subsequent reads.

**Why this is physically correct.** Gas equilibrates fastest in reality (sound speed in air is ~340 m/s; a pocket of air reaches local equilibrium in milliseconds). On the timescale of one sim cycle, gas is done relaxing well before slower materials have made any meaningful move. Freezing gas after 3 sub-passes doesn't lose physics — the gas wouldn't be doing anything meaningful in later sub-passes anyway. Pass count encodes relaxation time in sub-steps. Liquid at 5 captures water's intermediate rate. Solid at 7 captures the fine temporal resolution needed for stress propagation and yield events.

**Cross-phase boundaries update live.** Where a gas cell meets a liquid cell, or liquid meets solid, the flux between them can exchange while both sides are still computing (within their respective budgets). Cross-phase interaction is not gated on phase completion.

### Mixed-phase cells

A cell with mixed phase distribution (wet sand = solid+liquid, foam = liquid+gas, magma = liquid+solid) participates in multiple phase sub-passes per its composition. Each phase fraction of the cell updates on its phase's schedule. The solid fraction of a wet sand cell gets 7 sub-passes of solid dynamics; the liquid fraction gets 5 sub-passes of liquid dynamics. They couple through intra-cell transitions (phase transitions, temperature coupling) and through shared flux records at the boundaries.

### Phase-dependent transport rules

Each phase has its own flux computation rule:

- **Plasma:** averages toward same-phase neighbors like gas, but with strongly amplified thermal coupling: plasma's energy flux to non-plasma neighbors is large, driving rapid heating of adjacent cells. Plasma ablates adjacent solids by raising their temperature past melt/vaporize thresholds; plasma evaporates adjacent liquids via the same mechanism. Opportunistic.
- **Gas:** averages toward same-phase neighbors, mass-conserving. Pressure equilibrates fast. Composition mixes. Opportunistic — fills low-pressure regions aggressively.
- **Liquid:** same-phase averaging with reduced rate; gravity-directed bias via the sorting ruleset. Opportunistic. Surface tension emerges from the cohesion mechanism, not from a separate surface-tension rule.
- **Solid:** non-opportunistic for mass transport (does not fill low-pressure regions spontaneously). Transmits stress via petal stress updates. Moves in discrete displacement events triggered by yield-threshold exceedance. Move-before-compress priority: when yield is exceeded, first check for fluid/gas neighbor to displace into (brittle/spalling); if none, compress and harden (ductile).

Vacuum is not a separate phase rule; it is the low-pressure limit of normal pressure dynamics. Cells at vacuum have pressure at the encoding floor; incoming fluxes from neighbors are accepted normally, which is what causes vacuum to fill when phases from adjacent cells have somewhere to go.

Phase transitions (freeze/melt/evaporate/condense/ionize/recombine) are decisions the region kernel makes per cell per cycle based on the 2D phase diagram lookup `(pressure, temperature) → (phase, initial_mohs)`. When a phase transition occurs, the cell's phase distribution updates; the kernel writes appropriate flux records for any mass/energy redistribution the transition requires.

### Tail at Scale: straggler culling

A region producing fluxes below the noise floor ε (all six directional contributions below threshold) is at local equilibrium and can cull itself from subsequent sub-passes within the cycle. If gas has equalized across a region by pass 2, the region skips pass 3 — no compute spent.

This is the Tail at Scale pattern applied locally: don't wait for stragglers, don't compute trivial updates. Regions drop out of the active set as they reach equilibrium and rejoin when a flux event wakes them. The cycle does not block on every region completing every sub-pass; it dispatches sub-passes to the regions that have non-trivial work to do.

This connects directly to the hot/warm/cold tier management: a region culled for N consecutive cycles demotes to warm tier. A region woken by incoming flux promotes back to hot and rejoins sub-pass dispatch.

**The noise floor ε is a tunable parameter.** Smaller ε → more sensitive, more active regions, higher fidelity. Larger ε → coarser, more aggressive culling, more performance. This is the quality/performance slider — one number, tuned per target hardware.

### Mohs ratcheting

Solid cells track their Mohs level. Under sustained over-threshold compression (measured by the `cycles_above_threshold` counter), the cell ratchets: `mohs_level++`, excess pressure absorbed into the new level's dead-band, compression work dumped into the energy channel as heat. Ratcheting is exothermic — metamorphic rock is hot, and the sim gets this for free.

Ratcheting is triggered by peak-excess OR duration-gate. The duration counter is a single `u8` per cell.

Each ratchet step raises the cell's yield threshold geometrically (Mohs maps exponentially to wallet equivalent, ~1.6× per level). The ceiling is diamond at Mohs 10; nothing in ambient conditions can push past.

**Overburden field.** A scalar field maintained alongside the cell state, storing the cumulative mass of all cells above each cell. Updated incrementally by the integration kernel (O(changes), not O(grid)). Regions read the overburden field as input to their compression and ratcheting logic — a cell's effective sustained pressure includes not only its immediate neighbor gradients but also the weight of everything above it.

### Material identity

Species identified by element ID (u8, periodic table). Compound materials resolve to their element composition vector at cell initialization (water = `[(H, 114), (O, 141)]`). The 118 real elements fit in u8 with 137 slots for compound aliases if aliasing is useful.

Per-element constants (phase centers, Mohs max, melt/boil points, density, conductivity, molar mass) live in an element table loaded at sim start. The hash of the element table is recorded in save files for reproducibility.

Gas phase centers scale by molar mass (heavy gases pool low, light gases rise — atmospheric stratification emerges without special code). Liquid phase centers are approximately compositionally uniform (ideal-ish). Solid phase centers diverge strongly by composition and Mohs level.

---

## Cells are indivisible; boundary interactions apply a sorting ruleset

A cell is the Planck length of the simulation. It has no internal spatial substructure — a mixed cell with 30% liquid and 70% gas does not store "the liquid is at the bottom." Storage is a single bag of composition / phase fractions / masses / energy / etc. Rendering displays the cell as a single shaded hex.

When flux is computed across a boundary, the kernel acts **as if the cell's contents were sorted by a ruleset** at that edge. The sorting is a pure function applied at flux-compute time, not stored state.

**The sorting function takes as input:**
- Cell's composition vector and phase-fraction masses
- The edge direction being computed (which of the six)
- Local gravity vector at the cell (from the gravity field, see below)

**And outputs:**
- Effective per-phase "exposure weight" for this edge, modulating flux computation
- Which phase is most exposed to the neighbor across this edge

**What this produces:**
- Oil floats on water: mixed-cell with oil + water exposes oil on its upward edges, water on its downward edges. Flux flows accordingly. Over cycles, oil accumulates in upper cells, water in lower cells. Separation emerges.
- Bubbles rise: gas-in-liquid mixed cell exposes gas at top edge; gas fluxes upward; bubbles rise cell by cell.
- Sediment falls: dense solid particles in liquid expose at bottom edge; solid flux flows down; sediment accumulates.
- Works correctly at any gravity direction because the sorting function reads a per-cell gravity vector.

The sorting is applied to **both outbound flux** (what this cell sends, drawn preferentially from whichever phase is exposed on the outgoing edge) **and inbound flux** (incoming mass joins the cell's composition but is notionally associated with the receiving edge's sorting position, affecting subsequent flux decisions).

**Cohesion also uses the sorted exposure.** A mixed oil-water cell's cohesion toward its top neighbor is computed against oil composition (the phase exposed on that edge), not against the cell's overall composition. Cohesion at the bottom edge is computed against water. This produces correct interface behavior at mixed-cell boundaries.

The sorting ruleset is parameterized on ambient forces — primarily gravity. Zero-gravity scenarios produce uniform exposure (no sorting). Centripetal scenarios (spinning habitat) produce radially-outward sorting. Magnetic sorting for ferromagnetic-composition scenarios is a future extension.

---

## Gravity as a first-class diffused vector field

Gravity is not a configured constant or a single global direction. It is a **combined vector field** maintained alongside the other physics fields, updated via the same Jacobi diffusion architecture as pressure/energy/etc., and read by cells during flux computation.

### Architecture

**One vector per cell**, representing the local gravity vector (magnitude + direction combined). Vectors are more efficient than separate magnitude+direction fields because summing contributions from multiple sources is vector addition, and diffusion propagates directional perturbations correctly.

**Setup phase (layered Jacobi initialization):**
- Scenario defines one or more point sources (planet center, moon, asteroid, etc.). Each has a position and a mass.
- For each border tile, the gravity vector contribution from each point source is computed via Newton's law (`g = GM/d² × direction_to_source`). Contributions from all sources are summed into one vector per border tile.
- Layered Jacobi passes propagate these boundary values inward across the active simulation region, settling the initial gravity field. Same layered-Jacobi pattern used for other setup seeding.
- Border tile shape may be non-uniform but **the overall sim region must be convex.** Non-convex borders produce gradient pathologies at the concave regions (gravity diffusion flows around obstacles incorrectly). Convex is the architectural requirement.

**Runtime phase (concurrent Jacobi diffusion):**
- Border values stay frozen (external planetary context is static from the slice's perspective).
- Active cells contribute their own mass to the local gravity diffusion — mass concentrations inside the slice perturb the field near them.
- Gravity diffusion is a first-class concurrent sub-pass alongside pressure, energy, etc. Because gravity changes slowly relative to flow dynamics, its sub-pass count can be low (1 per cycle, or 1 per N cycles, tunable).
- Diffusion naturally propagates **weak directional perturbations** — a dense local mass pulls the surrounding vectors slightly toward itself, and the tilt weakens with distance via diffusion falloff. Local gravity anomalies, gravitational shadows, and tidal-like effects across extended bodies all emerge.

**Optional refreshable point sources:**
- Point source positions/masses can be stored as formulas. If sources move (orbiting moon, mobile spacecraft), the border can be recomputed periodically and re-seeded into the gravity diffusion.
- Refresh frequency is tunable. Static planets: never refresh. Dynamic systems: refresh every N cycles.

### Applying gravity to cell motion

Gravity is applied as an **acceleration contribution** to each cell's petal data **only when the cell has non-zero motion.** Cells at rest do not have gravity applied.

Per cycle, for each cell with motion above the noise floor ε:

```
cell.acceleration += gravity_vector_at_cell
```

Static behaviors (a rock settled on bedrock, a column of air in hydrostatic equilibrium) emerge because settled cells have zero motion and gravity does not apply to them. A rock sitting on the ground is not being perpetually pushed down and opposed by an equal upward force; it is simply at rest, with gravity inactive.

Dynamic behaviors (falling rocks, convection, buoyancy) emerge because any cell with non-zero motion accrues gravity acceleration per cycle. When motion returns to zero (the cell has come to rest), gravity stops applying. Hydrostatic stratification settles naturally: mass moves under gravity until gradients stop producing motion, at which point the system is at rest and stays there.

The motion threshold uses the noise floor ε (same ε used elsewhere for straggler culling). This avoids tiny numerical jitter perpetually triggering gravity application and re-injecting noise.

### Multi-source gravity

The border-seed mechanism handles multiple gravity sources without special code. At setup, contributions from each point source are vector-summed into the border values. At runtime, the diffusion carries the combined field inward. A scenario with a planet and a moon is authored by supplying both point sources to the border calculation; everything else is the same.

Lagrange points, tidal forces, and gradient-field features across extended bodies all emerge from the vector field without additional mechanism.

### Precision and bounds

Vector magnitudes at the border are bounded by the phase-density-consistent operating range. For Earth-like planets (surface gravity ~9.8 m/s²), border values are ordinary f32 numbers well inside precision limits. The "stupendous values" concern dissolves because the sim never stores or sums planetary mass directly — it stores the *vector contribution* at the border, which is always a modest number regardless of the mass generating it.

Genuinely extreme gravity scenarios (neutron star surface, gas giant interior) push numbers higher but stay manageable with f32 throughout the operating envelope.

---

## Scenario bounds and validation

The sim has hard operating bounds on scenario parameters, enforced at setup. Scenarios requesting conditions outside the bounds are rejected with a clear error message before any cycle runs. This is the discipline: pick a precision envelope that covers every scenario VERDANT wants to support, engineer the sim to be rock-solid within that envelope, and reject scenarios that would push past it.

### Bounds enforced at setup

- **Gravity vector magnitude** — bounded so that per-cycle acceleration additions stay within precision budget.
- **Slice spatial extent vs. point source distance** — bounded so direction computations retain adequate precision.
- **Mass per cell** — bounded by phase-density ceilings (Mohs ratchet caps at diamond; no unbounded compression).
- **Flux magnitudes per cycle** — bounded by per-cycle transport limits (mass can't move more than a fraction of a cell's contents per cycle; prevents numerical explosions from pathological gradients).
- **Temperature and pressure ranges** — bounded by the element table's phase-diagram domain and the log-scale pressure encoding range.
- **Simulation region convexity** — non-convex regions rejected at setup; convexity is required for gravity diffusion to behave correctly.
- **Border configuration consistency** — contradictory per-channel border settings (e.g., "fully sealed" AND "fixed pressure 10 atm") rejected at setup.

### Why this is the right philosophy

Robust-at-all-scales arithmetic is genuinely hard and expensive: multi-precision integers, adaptive scaling, fallback kernels. That's space-physics-simulator territory and not where VERDANT should spend engineering effort. Bounded scenarios let the inner kernels assume safe operating conditions — no runtime precision failures, no silent NaN propagation, no "why did my gravity field blow up after 10,000 cycles."

Players and designers get a clear "this scenario exceeds simulation bounds: [specific violation]" message at setup and can adjust.

### Tunable safety margins

The bounds themselves are parameters in the sim configuration, not hardcoded in kernels. As target hardware improves and larger numerical ranges become cheap, bounds can widen without kernel changes. Different use cases (game, research, benchmark) can tune bounds differently within the same sim implementation.

---

## Flux summation

At the end of each cycle's region-compute phase, the flux scratch buffer contains contributions from every overlapping region that touched each edge. The flux-sum kernel aggregates these contributions per edge.

### Sum, don't arbitrate

Each region computes genuinely different partial physics for the edges it touches: pressure-driven transport, gravity bias, gradient diffusion, stress transmission, momentum carryover, thermal conduction. These are different forces acting simultaneously, not competing estimates of the same force. They sum because forces are additive — this is first-principles Newton. There is no conflict resolution, no voting, no winner selection among region contributions. The physics is vector summation.

### Veto stage for hard constraints

Summation alone does not enforce *impossible* transports. A proposed flux across a grid border edge, into an inert designated region, or otherwise violating a hard constraint must be rejected rather than summed in.

This is the residual useful idea from the auction metaphor: some proposed state changes must be rejected. But rejection is not competition between regions; it is the physics saying "that transport cannot happen regardless of who proposed it." The veto stage runs between region compute and summation, filtering proposed fluxes against hard constraints. Surviving fluxes proceed to summation.

In practice, the veto rarely fires after the first cycle, because of topology caching (see below).

### Topology caching in petal metadata

Cells learn their **immutable local topology** on first contact with it. Border type, grid-edge status, and designated-inert neighbors are discovered on first attempt and cached in the petal metadata for that direction.

**Only immutable topology is cached.** Dynamic neighbor state (composition, phase, temperature, pressure, Mohs level) is NOT cached in petal metadata. It is read fresh from the canonical grid state every cycle.

This is correct because every cell sees a **frozen snapshot** of its neighborhood within a single pass. Jacobi double-buffering guarantees this: all reads come from the previous-pass buffer, all writes go to the current-pass buffer, and no cell observes another cell mid-update. The "neighbors are static" property is an architectural invariant, not something the kernel enforces manually — there is no race to lose because the read and write buffers are physically separate.

Petal metadata therefore stays small: a handful of flags for immutable topology (`is_border`, `border_type_index`, `is_inert`, `is_grid_edge`), plus any cached parameters the border properties table needs. Everything else is read live from the grid.

This is more efficient than per-cycle validation (no rejection logic in the hot path after topology is learned) and more physical (cell behavior reflects its actual local topology). Borders, inert regions, and locked boundary conditions all use the same mechanism without kernel special cases.

### Edge-consistency convention

Consistency across an edge is enforced by the region kernels' physics: the flux from cell A to cell B computed by any region equals the negative of the flux from B to A computed by any region, up to numerical precision. When summed from both sides, they represent the same transport and do not double-count, because each region contributes through a fixed authorship convention.

If edge-centric flux storage is used, double-counting is impossible by construction (only one flux record per edge). If cell-centric storage is used, the convention is that cell A owns its 6 outgoing records, and cell B reads A's relevant record as incoming. The implementation chooses whichever layout is most efficient; the metaphor does not constrain the storage.

---

## Borders and boundary conditions

Grid borders are not a single behavior. VERDANT must support multiple experimental boundary conditions: sealed chambers that trap heat, radiative environments that dissipate to space, pressure-relief walls, insulated-but-conductive flask walls, fixed-temperature boundaries, fixed-flux boundaries. Different scenarios require different borders, and the ability to test both "does my bottle trap a heat wave correctly" and "does my radiator dissipate it correctly" is a first-class requirement.

### Per-channel configurable behavior

Each transport channel — mass, momentum, energy, stress — has its own border behavior flag independently. A border can be:

- Thermally insulating but mass-permeable (pressure relief valve)
- Mass-sealed but thermally conductive (vacuum flask wall)
- Fully sealed (isolated experiment chamber)
- Radiatively coupled to a fixed ambient temperature (space-facing exterior)
- Held at fixed temperature regardless of incoming flux (heated plate, cold sink)
- Held at fixed flux (solar input, geothermal input)
- Reflective (momentum inverts; wall bounce)
- Absorbing (momentum dissipates; soft wall)

### Border properties table

Border behaviors are stored in a lookup table loaded at sim start, indexed by border-type tag. Per-channel parameters live in the table: target temperatures, flux magnitudes, absorption coefficients, reflection coefficients, etc.

Analogous to the element table pattern: one lookup per border-contact at topology-cache time, no per-cycle cost.

### Planetary-scale implications

At planetary scale, grid borders represent real physical transitions: the core boundary, the atmospheric top, the day/night terminator for insolation. Each of these has physically meaningful boundary conditions (core heat flux, radiative cooling to space, directional solar input) that map naturally to the configurable-border mechanism. Borders are therefore not just "end of grid"; they are physics-meaningful boundary conditions and the mechanism for injecting scenario-specific environmental coupling into the sim.

---

## Integration step

After flux summation, the integration kernel updates every cell's canonical state from the summed flux field:

```
new_center_state = current_center_state
                 + (sum of incoming fluxes across 6 edges)
                 − (sum of outgoing fluxes across 6 edges)
                 + (intra-cell transitions authored by region kernels,
                    e.g., phase changes, ratcheting events, chemistry)
```

Conservation is automatic: every flux appears once as outgoing (negative) for one cell and once as incoming (positive) for its neighbor. The total of any conserved quantity across the grid is unchanged by flux transport.

The integration kernel also re-encodes working-state f32 values into canonical packed representations (log-scale u16 pressure, temperature encoding, etc.) for grid-resident storage.

Cavitation is permissive: a cell that ends the cycle with (say) less mass than it started with is allowed to do so; the cell simply records lower mass / lower pressure / partial vacuum. The sim does not try to prevent vacuum creation; it represents it directly.

---

## Cycle structure

One simulation cycle consists of:

1. **Promotion pass (tiered memory management)** — regions that were in warm/cold tiers but have neighbors in hot tier with non-trivial fluxes get promoted to hot. Regions in hot tier that are at equilibrium (noise-floor cull condition, all fluxes below ε) get demoted to warm. Promotion/demotion is itself a wave that follows the physics; disturbances propagate into inactive zones by promoting them.
2. **Sub-pass loop, per phase** — gas runs 3 sub-passes, liquid runs 5, solid runs 7. Each sub-pass is a complete Jacobi step:
   - Read canonical state from buffer N
   - All hot-tier regions compute flux contributions in parallel
   - Blind sum of flux contributions
   - Integration applied: new state written to buffer N+1
   - Swap buffers
   - Next sub-pass reads the freshly-updated state
3. **Re-encoding** — working f32 state compressed to canonical packed representation for grid-resident storage after the final sub-pass of the cycle.
4. **Render sync (optional, at display rate)** — renderer reads the current canonical state and produces visual output. Renderer runs at display framerate, which may be less than sim framerate.

### Why each sub-pass gets its own buffer swap

Pressure waves must actually propagate. If gas equalized only once per cycle against frozen start-of-cycle state, a pressure spike could only influence its immediate neighbors per cycle — wave speed would be capped at 1 cell per cycle regardless of pass count. The 3/5/7 pass counts are meaningful precisely because each pass reads the result of the previous pass: gas pressure waves travel 3 cells per cycle, solid stress waves travel 7, because each sub-pass integrates and the next sub-pass sees the updated field.

Double-buffering (A/B ping-pong) is sufficient. You do not need one buffer per sub-pass unless you want to preserve intermediate states for debugging. For the reference Python sim, writing every sub-pass to disk as a debug-harness frame is valuable for validation. For production CUDA kernels, two physics buffers are enough for correctness.

### VRAM budget sanity check

A 2D grid at reasonable scale (4096×4096 hex cells) with per-cell state at ~120 bytes packed ≈ 2 GB per buffer. Double-buffered physics state ≈ 4 GB. Flux scratch buffer adds another ~1-2 GB depending on tile count. Total ~6 GB for physics on a 24 GB card, leaving ample room for larger grids and tiering metadata. The tiered memory architecture exists to manage *active region count during compute*, not raw grid size. Scaling to much larger grids is a question of how many regions must be simultaneously hot, not of total cell count.

---

## Memory tiers

Target hardware: 24 GB VRAM + 32 GB+ system RAM.

### Hot tier (VRAM, full state)

Regions actively computing this cycle. Full f32 working state, full flux records, double-buffered for Jacobi semantics. Memory cost per region is the dominant factor in sizing the active simulation.

### Warm tier (VRAM, compressed state)

Regions at equilibrium (all fluxes below noise floor for N consecutive cycles). Canonical state held in packed/encoded form only. Can be re-promoted to hot tier in one cycle if a neighbor's flux reaches them.

### Cold tier (system RAM)

Regions far from any activity. Compressed state, streamed to VRAM over PCIe when a disturbance propagates into their neighborhood. PCIe 4.0 x16 gives ~32 GB/s — easily enough to promote thousands of regions per cycle at 60 Hz if needed.

### Promotion/demotion discipline

A region promotes itself to hot tier when any of its neighbors (in any tier) have non-trivial incoming flux pointing at the region this cycle. This is detected cheaply: the flux-summation kernel emits a "woke up" flag for any edge with flux above ε where the receiving cell is not in hot tier.

A region demotes to warm tier when all of its fluxes and all of its neighbors' fluxes toward it have been below ε for some number of consecutive cycles (hysteresis to avoid thrashing).

Cold demotion is slower and managed by host logic based on spatial distance from any hot activity.

---

## Rendering interface

The renderer is a consumer of the sim's canonical state. It does not participate in flux computation or integration.

Per cell, the renderer reads:

- Composition vector (what species, in what fractions)
- Phase distribution (what phases, in what fractions)
- Pressure (for visual effects: shock fronts, fog density, compressibility indicators)
- Temperature (for glow, steam, thermal tinting)
- Stress level (for solid deformation visuals, imminent-failure indicators)
- Mohs level (for texture selection within solid phases)

From these, the renderer produces pixel output. The rendering rules are a separate concern from the physics. A cell that is 70% liquid water / 30% gas with temperature just above boiling renders as bubbling water with steam rising; a cell that is 90% solid silica / 10% trapped air at high stress renders as fractured sandstone. The mapping from physics state to pixel is art direction and display logic, not simulation.

**Rendering cadence decouples from sim cadence.** The sim runs at whatever rate the hardware supports (typically hundreds of sub-steps per display frame, since the per-phase pass counts are sub-cycle). The renderer samples the canonical state at display framerate.

---

## Future work

The following are acknowledged extensions, not blockers for initial bring-up:

1. **Damping-to-heat coupling.** The flux summation step has a natural hook: if contributions from different regions disagree by more than numerical precision, the variance is real physical damping and should be dumped into the energy channel as heat. Would give impact metamorphism, frictional heating along fault lines, and ductile-deformation heating at depth for free. Worth implementing early when convenient; design is compatible as-is.
2. **Fragmentation / unmerge.** Solids breaking under stress-anisotropy. Region-level detection: a region whose center is a solid cell checks for stress-gradient anisotropy across its six directions; if anisotropy exceeds local Mohs tensile limit, emit a fragmentation event. Needed to close the rock cycle.
3. **Granular mechanics / angle-of-repose.** Sand, talus, scree. Currently implicit in phase-dependent flux rules (low-Mohs solids get displaced easily). May need explicit treatment if angle-of-repose behavior is visibly wrong.
4. **Temperature-modified Mohs.** Stone softens with heat. `mohs_effective = mohs_max × f(T)`. Small change, big emergent consequence (ductile deformation at depth, rheology gradient with depth).
5. **Multi-scale grid (mipmap regions).** Far-field phenomena (weather systems, mantle convection, ocean currents) could use coarser regions to save compute. Adaptive resolution.

---

**Design discipline:**

- Rules factor rather than accrete. Each primitive does multiple jobs: flux transport gives flow + diffusion + suction + lithification + spalling + rock cycle; cohesion gives surface tension + immiscibility + crystal growth + blob maintenance. If a new rule duplicates work an existing primitive already does, the new rule is wrong.
- The distinction between physics state and visual state is a hard wall. Composition is continuous; rendering makes it look discrete. Never special-case the sim to produce a nice-looking cell. Always fix the renderer.
- When real-physics-like behavior emerges from the sim, it is evidence of correct substrate construction, not proof of encoded realism. Build the substrate right; physics follows.

---

## Shelved questions

Open design questions parked for later resolution. Not blockers; answers are needed eventually but can be decided when the rest of the system is firmer.

1. **Majority-by-mass vs majority-by-fraction-of-equilibrium for cell identity.** A cell with 42 units of gas (at its center) and 100 units of liquid (severely under-dense relative to liquid's 1,764 center) has "majority by mass" = liquid. But the liquid is barely present as a physical phase at that density, while the gas is fully present. Is identity-for-cohesion, identity-for-rendering, identity-for-sorting-rules computed from raw mass majority or from how-close-each-phase-is-to-its-own-center? These may give different answers in borderline cells.
2. **Unified vs per-purpose identity.** Identity is consumed by several subsystems (cohesion, rendering, boundary-sort, phase-transition decisions). Is the identity function one unified computation shared across all consumers, or is identity potentially different per consumer (e.g., rendering uses "visually dominant" which might differ from cohesion's "compositionally dominant")?
