# dt and Units

## Tick rate

**1 tick = 1/128 s of sim time = 7.8125 ms.**

Why 1/128:
- Power of 2. Tick counters and sub-ticks are bit-shifts, not divisions. "Run this every 16 ticks" is `tick & 0xF`.
- Decouples sim rate from display rate. Display polls at 64 Hz (every 2nd tick) by default; sim can run 128 Hz, 64 Hz, 32 Hz, 256 Hz by configuration without touching rendering.
- Physics budget: ~2 ms estimated compute at 250k cells on RTX 3090 (see [`pipeline.md`](pipeline.md)). At 7.8 ms/tick budget, ~4× headroom.

## Units: SI throughout

The element table and all derived quantities use SI:

| Quantity | Unit |
|---|---|
| Mass density | kg/m³ |
| Pressure | Pa |
| Energy | J |
| Temperature | K |
| Time | s |
| Length | m |
| Specific heat | J/(kg·K) |
| Thermal conductivity | W/(m·K) |
| Elastic modulus | Pa |
| Magnetic field B | T |

**Scenario config sets cell size** (meters per cell). Typical: 0.01 m (1 cm). For larger grids simulating geology or ecosystem-scale phenomena, cell size may be 0.1 m or 1 m.

Derived timestep:
```
dt = 1/128 s
cell_volume = cell_size³        (conceptually; hex cells are slightly different)
```

## u16 encoding of pressure and energy

Pressure and energy are stored as u16 for memory efficiency. Log-scale encoding for pressure (see [`cell-struct.md`](cell-struct.md) — `pressure_raw`):

```
encoded = round(mantissa × ...)
decoded = mantissa × phase_scale × mohs_multiplier^(mohs-1)  [for solids]
```

Phase-dependent scales are per-element. See [`element-table.md`](element-table.md).

Energy uses linear encoding scaled per element — again, per-element `energy_scale` fits realistic ranges into u16.

## CFL-like stability considerations

For purely diffusive flows (which the auction approximates), stability requires:

```
D × dt / (cell_size)² ≲ 0.5
```

Where D is the diffusivity (different for mass, energy, strain). Each flow pass respects this through its per-phase iteration budget — more iterations = smaller effective sub-dt per iteration.

For elastic wave propagation, real sound speed in solids demands `dt_acoustic ≪ cell_size / v_sound`:

```
granite: v_sound ≈ 4000 m/s
cell_size = 0.01 m
dt_acoustic ≲ 2.5 µs
```

Our dt is 7.8 ms = 3000× too coarse for real-time acoustic propagation. The sim's 7-iteration elastic sub-cycle propagates strain at ~1 cell per iteration = ~9 m/s sim-apparent sound. This is acceptable because most scenarios don't require accurate acoustic speeds.

Scenarios that demand realistic sound (seismology, impact physics) would need either smaller dt (whole sim slows) or a dedicated fast-path acoustic solver (future work).

## NIST-sourced constants

Material constants come from reference data:
- Molar mass, atomic number: IUPAC values (stable elements 1–94)
- Density, specific heat, thermal conductivity, elastic modulus, tensile strength: NIST / CRC Handbook reference values at standard T/P
- Melt/boil points, heat of fusion/vaporization: NIST
- Emissivity, albedo: material handbooks
- Curie temperature, magnetic susceptibility: material handbooks

Priority for Tier 0–4 elements: Si, H, O, C, Fe, N, Al, K, Ca, Mg, Na.

See [`element-table.md`](element-table.md) for the required column set and sourcing.

## Fudge factors vs. real values

Many real physical effects are extremely slow compared to what we want to observe in a sim:

- Stalactite growth: ~0.1 mm/year in reality. At 1 cm cell size, real-time would take ~100 years of sim time.
- Cave formation: ~10 cm/1000 years. Billions of sim ticks.
- Metamorphic ratcheting: kiloyears.

Each of these has a **scenario-tunable rate multiplier** that can be cranked up for observational purposes:

- `precipitation_rate_multiplier` (scenarios can scale × 10⁸)
- `dissolution_rate_multiplier` (similar)
- `G_sim` (gravity tunable for visible stratification at sim timescales)

Rate multipliers are **scenario parameters**, not element-table constants. Element-table values are "physical truth"; scenario multipliers are "how fast do we want to watch it."

## Cross-validation units

For Python↔CUDA cross-validation:
- Both implementations use identical SI values (read from same element table TSV).
- Both implementations use identical dt.
- Both implementations use identical `G_sim`, rate multipliers, etc., from the scenario config.
- JSON emissions contain decoded (SI) values alongside raw (u16) values — diff on decoded with tolerance, diff on raw exactly.

## Changing dt mid-sim

Not supported by default. Scenarios pick a dt at init and stick with it.

If a scenario absolutely needs variable dt (e.g., slow-motion mode during a player action), the cleanest approach is: multiply all rate-dependent quantities (G_sim, precipitation_rate, etc.) by an inverse factor, not dt itself. Keeps the physics invariants intact.

## Display rate

Display polls every Nth tick for rendering:

- 128 Hz sim, 128 Hz display: 1:1, maximum smoothness (high-end).
- 128 Hz sim, 64 Hz display: 2:1 default. Each frame shows state after 2 ticks.
- 128 Hz sim, 30 Hz display: sub-minimum display rate. Use if GPU-bound on rendering.

Display rate is configurable independently of sim rate. The JSON emission is tied to sim ticks, not display frames — the viewer picks which tick's JSON to render.
