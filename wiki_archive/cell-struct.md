# Cell Struct

The per-cell stored state. Target size ~16 bytes, tuned for packing and for round-trip between Python (numpy structured arrays) and CUDA (C struct).

## Fields

```
cell = {
    composition:    [(u8 element_id, u8 fraction) × 4]   //  8 B
    phase:          u2                                    //
    mohs_level:     u4                                    //  sub-byte
    flags:          u8                                    //  1 B  — see flags.md
    pressure_raw:   u16                                   //  2 B  — log-scale encoded
    energy:         u16                                   //  2 B  — SI: joules (post-dt scaling)
    elastic_strain: i8                                    //  1 B  — signed; + = compressed, − = stretched
    magnetization:  i8                                    //  1 B  — signed scalar moment
}
// ~16 bytes total with natural packing
```

## Field-by-field

### `composition: [(u8, u8) × 4]`
Four element slots. Each is `(element_id, fraction)` where `fraction` is out of 255 across all four slots (must sum exactly to 255 when resolved).

- **element_id 0–199:** periodic table (H=1, He=2, … U=92, up through 118; element 0 = "void" / placeholder).
- **element_id 200+:** compound aliases, expanded to underlying elements at cell init (e.g. 200 = water → `[(H, 114), (O, 141)]`). Not a runtime kernel concept.

Trailing empty slots are `(0, 0)` — element 0 is placeholder, fraction 0 is "slot unused." Composition normalization (sum = 255) happens at boundaries of operations that can change totals (mass flow, precipitation).

**Why 4 slots:** covers ~95% of real materials. Water = 2 slots. Granite = 4. Steel = 2. Air = 4 (N, O, Ar, CO₂). When a scenario exceeds 4, trace elements merge into the smallest slot or are dropped — decision lives in the mass-flow handler.

### `phase: u2`
`0 = solid, 1 = liquid, 2 = gas, 3 = plasma.`

Determined at Stage 1 (phase resolve) from `(pressure, energy, composition)` via per-element phase diagram. See [`phase-transitions.md`](phase-transitions.md).

### `mohs_level: u4`
Solids only (1–10). For non-solids, set to 0 and ignored. Monotonically non-decreasing within a tick (plastic ratchet is one-way). See [`phase-transitions.md`](phase-transitions.md) for ratcheting rules.

Solid center pressure scales by `mohs_multiplier^(mohs_level - 1)` — log-scale pressure encoding makes this efficient.

### `flags: u8`
See [`flags.md`](flags.md). Eight bits covering wall behaviors, transient states (CULLED, RATCHETED, FRACTURED, EXCLUDED), and material behaviors (NO_FLOW, INSULATED, RADIATES, FIXED_STATE).

### `pressure_raw: u16`
Log-scale encoded pressure:
```
bits 0–11: mantissa (0–4095)
bits 12–15: phase_offset × mohs_level_scale (implicit — phase + mohs carry this info)
```
Decoded value depends on `phase` and `mohs_level`:
```
gas:    mantissa × gas_center_scale         (ideal gas pressures)
liquid: mantissa × liquid_center_scale       (~10× gas)
solid:  mantissa × solid_base × mohs_mult^(level-1)  (exponential up the Mohs ladder)
plasma: mantissa × plasma_center_scale       (~64× gas)
```
See [`element-table.md`](element-table.md) for the per-material scale factors.

Resolution is concentrated where it matters (fine within phase, coarse across phases). Ideal for the huge dynamic range between gas (~10³ Pa) and diamond (~10⁶ Pa deep crust pressure).

### `energy: u16`
Cell's thermal energy content. SI joules, scaled by the per-element energy_scale to fit in 16 bits.

Temperature is derived, not stored: `T = f(energy, composition, phase)`. See [`derived-fields.md`](derived-fields.md).

**Strict non-negative.** Underflow (below phase's minimum U) triggers the P↔U coupling in reverse — draws from pressure — see [`overflow.md`](overflow.md).

### `elastic_strain: i8`
Solids only (−128 to +127). Signed: positive = compressed, negative = stretched.

Zero = at rest. |strain| at `elastic_limit` = saturated; further loading triggers plastic ratchet (compression side) or bond break (tension side).

Decays back to zero when load is removed — this is springback. See [`elastic-flow.md`](elastic-flow.md).

### `magnetization: i8`
Signed scalar magnetic moment per cell. Only non-zero for ferromagnetic elements (Fe, Co, Ni, Gd — per element-table `is_ferromagnetic` flag).

Zeroed above the Curie temperature. Re-acquires remanence when cooled in an applied B field. See [`magnetism.md`](magnetism.md).

**Scalar for now.** Upgrade path: promote to 2D vector (`i8 × 2`, +1 B) if scenarios need directional magnetization (compass needles, anisotropic magnets).

## Packing

Target layout, 16 B with natural alignment (little-endian, struct-of-arrays on GPU side):

```
offset  field
 0–7    composition          [8 B]
 8      phase(2) + mohs(4) + reserved(2)   [1 B]   packed bit-field
 9      flags                [1 B]
10–11   pressure_raw         [2 B]
12–13   energy               [2 B]
14      elastic_strain       [1 B]
15      magnetization        [1 B]
```

On the GPU, this is stored as a SoA (struct-of-arrays) layout — each field becomes a separate buffer, so kernels load only the fields they need. The AoS layout above is for JSON round-tripping and the CPU reference sim.

## What isn't in the cell

Not stored per-cell, on purpose:

- **Position.** A cell's index is its position. Redundant to store.
- **Temperature.** Derived from `energy`. Storing both risks drift.
- **Gravitational potential Φ.** Derived per-tick from full mass distribution.
- **Magnetic field B.** Derived per-tick from full magnetization distribution.
- **Chemical potential μ.** Derived per-tick, per-element, held in scratch buffer for Stage 3.
- **Neighbor list.** Implicit from grid geometry.
- **Bids sent/received at runtime.** Per-direction delta scratch buffer during a tick; not persistent. Debug emission copies these for the JSON; kernel doesn't persist them.

## Why this size matters

At 16 B/cell: a 500×500 grid (250k cells) is 4 MB. Fits in the 3090's 6 MB L2 cache with headroom. Fits 128k cells (~360×360) in a single SM's register file spill budget for tiled kernels.

Per-tick scratch buffers add ~8×cell_size at worst (per-direction delta for each of mass composition, energy, and strain). Still trivial at any grid size we'd practically run.

## Upgrade path

When the framework grows, fields are added in this priority order:

1. **Charge** — if electrodynamics is implemented, i8 charge + electric potential as a derived field.
2. **Magnetization vector** — if scalar proves insufficient, upgrade to `i8 × 2`.
3. **Tensor strain** — if anisotropic fracture is needed, upgrade strain to `i8 × 3`.

All upgrades grow the cell struct by 1–3 bytes. 24 B/cell is still well within cache-friendly for any realistic grid.
