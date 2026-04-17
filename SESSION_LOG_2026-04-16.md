# VERDANT Physics Sim — Design Session Log

**Date:** 2026-04-16
**Session:** Jacobi staged auction architecture + debug harness bring-up
**Participants:** Adrian Neill (toastee), Claude (Opus 4.7)
**End target:** CUDA/C++ native kernel on RTX 3090 under Windows 11

---

## Context carried in from prior sessions

- VERDANT is a terraforming pixel-physics survival game (Solar Jetman + ONI DNA)
- Engine: Rust + wgpu, CPU-based simulation for bring-up, eventual GPU port
- Prior design work: hex disc grid (polar-hex from line 1, no cartesian), gen 4 spec document exists
- Auction-as-physics-primitive established: parallel bids, phase-based cull, 3-7 cycles per phase as viscosity param
- 480×270 grid fits trivially on 3090 (uses ~4% of frame budget)
- Bring-up substrate is 91-cell hex disc in Python with SVG-per-tick output
- Existing gen 4 doc: `verdant_sim_design_gen4.md` (handoff to Claude Code)

---

## Session arc — architecture decisions

### 1. Staged Jacobi auction (mass / energy / type)

**Problem:** Running mass, energy, and type as one unified auction breaks because they have different propagation speeds and conservation rules. Coupling artifacts emerge (e.g., hot solid "wins" a mass bid because its energy pressure leaked into its mass bid cost).

**Decision:** Stage them. Each stage is its own full Jacobi sweep to convergence, running on the settled state from the previous stage.

**Pipeline per cycle:**
```
Stage 1: Phase resolve (type check)              — render gate, 1 pass
Stage 2: Read neighbors, compute gradients       — pure local pass
Stage 3: Cells outside dead-band bid             — auction clears
         Losers culled until next Stage 1
```

**Key insight:** Stage 1 is the render sync point. Gas runs the pipeline 3× per frame, liquid 5×, solid 7×. Cycle count per phase is simultaneously **viscosity** (aggression toward equilibrium) and **temporal resolution** (sub-ticks per frame). These are the same physical thing, correctly unified.

### 2. Dead-band seeking replaces winner-takes-all

**Problem:** Original auction model had discrete "winners" and "losers" competing for movement. Mechanically incorrect for fluid distribution.

**Decision:** Cells outside their dead-band distribute excess *proportionally* across all eligible downhill neighbors:

```
excess = my_pressure - dead_band_center
eligible_neighbors = [n for n in 6_neighbors if n.pressure < my_pressure]
gradients = [my_pressure - n.pressure for n in eligible_neighbors]
distribution = excess × normalize(gradients)   # proportional split
```

This recovers Fick's law from the economic framing. Real diffusion, not arbitrated packets.

**Cull rule reframe:** A cell is culled when it has *zero* eligible neighbors (no downhill path). Sits out until next Stage 1. Matches Dean & Barroso tail-latency discipline — don't wait on pressure-locked stragglers within a frame.

### 3. No clearing — cavitation as feature

**Problem:** If we "clear" bids against recipient capacity, we lose overshoot behavior.

**Decision:** All bids honor on submission. Every bidder read the recipient's state legally at Stage 2, so every bid is a valid allocation. Recipients simply **accumulate**. Overshoot creates pressure spikes — which is cavitation, physically correct.

**Kernel shape simplified:**
```
Stage 2:  each cell reads 6 neighbors, computes gradients
Stage 3a: each bidder computes proportional distribution,
          writes to neighbors' incoming accumulator
Stage 3b: each cell sums incoming + self residual,
          writes new pressure value. Cull if no eligible neighbors.
```

Three sub-passes, no clearing round, no atomics beyond per-neighbor-direction scatter-gather.

**Safeguards for extreme accumulation:**
- Phase transitions absorb excess (gas over-pressure → condenses to liquid; liquid over-heat → vaporizes)
- Extreme excess bleeds to energy field (shockwave heating)

### 4. Solid compression — 10 Mohs stages with ratcheting

**Decision:** Solids have a continuous compression state variable (`mohs_level: u4`, 1-10) that only ever increases within a frame. Ratcheting is one-way — matches real metamorphic geology.

**Solid behavior:**
- **As recipients (always):** receive incoming mass bids, accumulate pressure. Cross ratchet threshold → `mohs_level++`, excess absorbed into new band's dead-band width, compression work dumped into energy field (ratcheting is exothermic — metamorphic rock is hot)
- **As bidders (conditionally):** only when fractured (exceeded Mohs max tensile limit, or shocked). Fractured solid becomes downward bidder to empty/lower neighbors — avalanches, debris.
- **7 cycles/frame for solids:** most cycles idle (cells inside dead-band, quiet). Pressure-wave propagation through rock is 7× fluid rate — matches reality (sound through granite > water flow through granite).

**Phase diagram becomes 2D:** pressure × temperature → (phase, initial_mohs_level). Slow cool under pressure = granite (Mohs 6-7). Fast cool at surface = basalt (Mohs 5-6). Emergent, not hardcoded.

**Ratcheting triggers — peak AND duration:**
- Peak-triggered: single-cycle spike (shock metamorphism)
- Duration-gated: N cycles above threshold (regional metamorphism)
- Single u8 counter per cell handles both

### 5. Material identity = periodic table

**Problem:** "Solid center point" needs compositional variation — iron, granite, ice, and chalk are all solids with radically different pressure characteristics.

**Decision:** Material ID indexes into periodic table. 118 elements fit in u8 with 137 slots for compound aliases (water, SiO₂, CaCO₃, etc.).

**Gas and liquid centers shared across elements** (ideal gas law is compositionally weak — 1000 for gas, 10,000 for liquid). **Solid centers diverge by composition.** Gas center per element scales by molar mass (heavy gases pool low, light gases rise — atmospheric stratification for free).

**Rough material centers:**
```
Material          Gas    Liquid   Solid (Mohs 1)   Solid (Mohs 10)
Water             1000   10,000   12,000 (ice)     ~Mohs 2 cap
Iron              1000   10,000   50,000           500,000
Rock/silicate     1000   10,000   30,000           300,000
Organic/carbon    1000   10,000   20,000           2,000,000 (diamond)
```

**Log-scale pressure encoding (u16):**
```
bits 0-11: mantissa (0-4095)
bits 12-15: phase_offset / mohs_level

decode:
  gas:    mantissa × 1              (0-4095, centered 1000)
  liquid: mantissa × 8              (0-32760, centered 10000)
  solid:  mantissa × 8 × 1.5^level  (exponential up Mohs ladder)
  plasma: mantissa × 64
```

Resolution concentrated where it matters (fine-grained within phase, coarse across phases).

### 6. Composition vectors — mixture-capable cells

**Decision:** Each cell has a 4-slot composition vector: `[(element_id: u8, fraction: u8) × 4]`. Fractions sum to 255 (normalized).

**Why 4 slots:** covers ~95% of real materials. Water is H+O (2). Granite is Si+O+Al+K (4). Steel is Fe+C (2). Air is N+O+Ar+CO₂ (4).

**Cell struct (~14 bytes):**
```
cell = {
  phase:       u2
  pressure:    u16    (within current phase's log scale)
  energy:      u16
  mohs_level:  u4     (solid only)
  flags:       u4
  composition: [(u8, u8) × 4]   // 8 bytes
}
```

At 480×270 that's 1.8 MB — fits in L2 on 3090 with room.

**Mixing math at Stage 3b:** per-cell reduction over ≤7 inputs (self + 6 neighbors) × 4 elements = 28 element-weight pairs max. Register-local merge-sort. Cheap.

**Per-cell phase centers computed once per frame from composition-weighted averages of elemental constants.** Cast iron melts lower than pure iron — falls out automatically. Salt water freezes lower than fresh water — also automatic. Zero special-case code.

### 7. Periodic table data strategy

**Decision:** Ship full 118-element reference data from day one. Gate world-gen by manifest, not by table contents. Scenario files list `allowed_elements`.

**Bring-up ladder:**
- **Tier 0:** Si only (solid, all Mohs levels) — prove ratcheting, fracture, conservation
- **Tier 1:** + H₂O compound (H + O) — prove phase transitions
- **Tier 2:** + C, Fe — prove mixing (cast iron melt point < pure Fe)
- **Tier 3:** + N — atmosphere stratification by molar mass
- **Tier 4:** + Al, K, Ca, Mg, Na — realistic silicate rock
- **Tier 5+:** full palette as needed

**Reference data source:** NIST + Wikipedia for molar mass, melt/boil points, Mohs, density, thermal conductivity. No hand-tuned fudge numbers. Real Fe melts at 1811 K; sim must match or the bug is in us.

**Compound IDs 200+:** macros that expand to composition vectors on cell init (e.g., water = `[(H, 114), (O, 141)]`). Content convenience, not kernel feature.

### 8. Debug harness (built this session)

**Principle:** Build the self-confirming infrastructure *before* the real sim. Schema is the contract between Python reference and CUDA port.

**Three artifacts produced:**

1. **`sim_stub.py`** — ~200 lines of Python emitting schema-v1 JSON for `t0_silicon_drop` scenario (dropped Mohs 1 Si cell on Mohs 5 Si floor, 3 ticks, ratchet event at tick 3). Not the real sim — the schema reference.

2. **`verify.py`** — standalone invariant checker. Independently verifies composition sums, mass conservation, pressure decoding, Mohs range, bid conservation, flags consistency. Cross-checks sim's self-report against independent verdict — flags **DIVERGENT** when they disagree. Exit codes: 0 pass, 1 fail, 2 divergent, 3 schema error. Usable from shell, CI, or AI agent.

3. **`viewer.html`** — single-file drag-and-drop hex grid viewer. Phase/pressure color encoding, filterable sidebar (element, phase, Mohs range, flag state), click-to-inspect cell details, live invariant panel. No build step, no deps.

**All three agree on one JSON schema** — see `ARCHITECTURE.md` for full schema-v1 spec.

**Verified behavior:** 4 clean sample ticks → PASS. 1 deliberately broken tick → DIVERGENT (caught composition violation AND sim's self-report inconsistency).

**CUDA port payoff:** Python emits JSON at tick N, CUDA emits JSON at tick N, diff them. Any differing cell is a porting bug, localized. Known within minutes whether port is correct.

---

## Open threads for next session

1. **Element table TSV for Tier 0-4** (Si, H, O, C, Fe, N) — derive pressure centers from real atomic data. NIST values. ~6 rows.

2. **`diff_ticks.py`** — 30-line tool to compare two JSON files cell-by-cell for cross-validation between Python reference and CUDA port.

3. **Stub checker is Si-only** — `verify.py` hardcodes Si constants in `check_pressure_decoding`. Needs element table access to decode per-material. ~10-line change when Tier 0-4 table ships.

4. **Per-element gas center scaling by molar mass** — implement as `element.gas_center = 1000 × (molar_mass / 28)` (N₂ reference). Makes atmospheric stratification emergent.

5. **Phase diagram lookup table** — 2D `(pressure, energy) → (phase, initial_mohs)`. Per-element, loaded from data file. Needed for Stage 1.

6. **Ratcheting duration counter** — add `cycles_above_threshold: u8` to cell struct. Triggers ratchet on either peak excess OR duration gate.

7. **Compound expansion at cell init** — material ID 200 (water) → `[(H, 114), (O, 141)]`. Simple lookup.

8. **The gen 4 doc** (`verdant_sim_design_gen4.md`) predates these architectural decisions. Next revision should incorporate:
   - Staged mass/energy/type auction structure
   - Accumulator model (no clearing, cavitation permissive)
   - Mohs ratcheting with compression work → energy
   - Periodic table material IDs with composition vectors
   - Log-scale pressure encoding

---

## File locations

**This session's output:** `/mnt/user-data/outputs/verdant_debug/`

```
verdant_debug/
├── ARCHITECTURE.md              — debug harness architecture + schema
├── viewer/
│   └── viewer.html              — single-file hex grid viewer
├── checker/
│   └── verify.py                — independent invariant checker
├── reference_sim/
│   └── sim_stub.py              — schema-reference stub simulator
└── sample_data/
    ├── tick_00000_initial.json
    ├── tick_00001_post_stage_3b.json
    ├── tick_00002_post_stage_3b.json
    ├── tick_00003_post_stage_3b.json       — ratchet event, PASS
    └── tick_00099_post_stage_3b_violation.json  — deliberate break, DIVERGENT
```

**On PC when resumed:** extract to `m:\verdant\debug_harness\` or wherever the VERDANT project lives.

**Prior assets (from earlier sessions):**
- `verdant_sim_design_gen4.md` — gen 4 bring-up spec (pre-staged-auction)
- Full GDD and engine research docs (earlier work)

---

## Quick-resume one-liner

> We've architected the staged Jacobi auction for VERDANT (mass/energy/type as separate stages, Stage 1 is render gate, dead-band seeking replaces winner-take-all, cavitation via accumulator without clearing, Mohs ratcheting for solids with compression work → heat, periodic table as material ID with composition vectors, log-scale pressure encoding). Built the debug harness first: stub sim, checker, viewer all agree on schema-v1 JSON. Ready to either (a) write element_table.tsv for Tier 0-4, (b) fold the new architecture into a gen 5 design doc, or (c) start on the real Python reference sim using the schema we've locked in.
