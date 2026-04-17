# Mass Flow (Stage 3)

The unified flow pass for all element movement. Diffusion, gravity-driven settling, buoyancy, precipitation, dissolution, cohesion-locked solids, magnetic attraction — **all one pass, one rule, one set of code.**

## The rule

Mass flows down the gradient of **chemical potential μ** per element, not down pressure alone. Each element in each cell has its own μ.

For cell A bidding into neighbor B, for element E:

```
if μ(A, E) > μ(B, E) and bond is open:
    A bids a proportional share of its excess of E toward B
```

The direction of flow is determined by μ, not by pressure. This is the key insight that lets one pass handle all the seemingly-different phenomena.

## The μ formula

```
μ(cell, element) = P(cell)                              // pressure contribution
                 + ρ_element × Φ(cell)                   // gravitational potential energy
                 + f(concentration, solubility_limit)    // Fickian + solubility discontinuity
                 + cohesion_barrier(cell, element)       // ∞ if in bonded intact solid
                 − m_element · B(cell)                   // magnetic term (only for ferromagnetic elements)
```

Each term is a **contribution to the chemical potential** — higher μ means "this element wants to leave here." Flow goes from high μ to low μ.

### Term 1 — pressure

Just the cell's own pressure. A high-pressure cell wants to bleed out. Classic pressure-gradient diffusion.

### Term 2 — gravitational (ρ_element × Φ)

`Φ` is the gravitational potential at this cell (from Stage 0a — see [`gravity.md`](gravity.md)).

`ρ_element` is the mass density of this element in this cell's phase. Iron is dense (7874 kg/m³), hydrogen gas is not (0.09 kg/m³).

Effect: heavier elements have higher μ where Φ is high (up a gravity well). They prefer to move toward low Φ (down). Lighter elements get pushed the other direction.

This one term gives you:

- **Stratification** — gases layer by molar mass; heavy atmosphere at bottom, light at top.
- **Sedimentation** — dense suspended particles settle.
- **Buoyancy** — lighter fluid rises, denser sinks, iron sinks in water, air bubbles rise.

No special-case buoyancy code. Iron vs. water emerges from each having a different `ρ_element × Φ` contribution to their respective μs. Iron's μ is higher at the water's location than at the rock's location below; water's μ is lower where the iron is. Both flows happen simultaneously: Fe flows down, H₂O flows up, through the same bond.

### Term 3 — concentration / solubility

Fickian diffusion: an element with low concentration in A has lower μ than in a cell B where it's more concentrated. Flow goes down the concentration gradient.

But real systems have **solubility limits**. Salt dissolves in water only up to a saturation point. Beyond that, adding more salt just deposits as precipitate. The μ curve has a discontinuity at the solubility limit:

```
fraction_below_limit → μ ∝ fraction       (normal diffusion)
fraction_at_limit    → μ jumps            (phase transition via precipitation)
fraction_above_limit → μ ∝ fraction − limit, but very steep   (super-saturated, fire precipitation)
```

This term alone gives:

- **Fickian diffusion** within solubility limits.
- **Precipitation** at solubility discontinuity — triggers Stage 1 precipitation event next tick.
- **Dissolution** reverse direction — liquid with under-solubility concentration near a solid surface draws element from the solid.

See [`precipitation.md`](precipitation.md).

### Term 4 — cohesion barrier

If element E is part of a cohesively bonded intact solid, and we're considering flowing E across a cohesion bond, μ is effectively ∞. No mass leaves a bonded rock in that direction. Rocks stay rocks.

If the neighbor in that direction is NOT cohesively bonded (different material, fluid, void), the barrier drops to 0 and the other μ terms dominate.

If the cell is `FRACTURED`, the cohesion_barrier is always 0 — fractured debris bids freely. Avalanche behavior.

This is how intact solids resist diffusion, buoyancy, concentration gradients. The barrier dominates other terms; the cell is unmovable. Fracture releases it.

See [`cohesion.md`](cohesion.md).

### Term 5 — magnetic

For elements with `is_ferromagnetic = true`:

```
magnetic_term = − m_element · (B(cell) · n̂_direction)
```

Lower μ in the direction of the field (aligned moments want to be where B is strongest). Ferromagnetic composition flows up the B-field gradient — iron filings migrate toward magnets automatically.

Zero term for non-ferromagnetic elements.

See [`magnetism.md`](magnetism.md).

## Compute strategy — dense μ scratch buffer

Before Stage 3 runs:

```
Stage 0e:
for each cell in parallel:
    for each element slot:
        compute μ[cell][element_slot]
```

Result is a dense scratch buffer, size = 250k × 4 slots × 4 B = 4 MB.

Stage 3 reads μ from the buffer at each bond. No per-bond recomputation. Clean separation of concerns. See [`pipeline.md`](pipeline.md) Stage 0e.

Between sub-iterations of Stage 3, Stage 0e is re-run so μ reflects the updated state. This is crucial for Jacobi convergence — a stale μ wastes sub-iterations.

## The delta buffer

Stage 3 writes to a per-direction, per-element delta buffer:

```
deltas[cell][direction ∈ 0..5][element_slot ∈ 0..3]
```

Size: 250k × 6 × 4 × 2 B (i16, signed) = 12 MB. Scratch, reused each sub-iteration.

When cell A sends element E to neighbor B in direction d:
```
deltas[A][d][E_slot]     -= amount   (A loses it)
deltas[B][opp(d)][E_slot] += amount   (B gains it, via the opposite direction slot)
```

Per-direction structure enables the Stage 5 overflow cascade to refund bids proportionally — we know exactly which neighbors contributed how much.

## Convergence

Per-phase sub-iteration caps: gas ≤3, liquid ≤5, solid ≤7 (see [`convergence.md`](convergence.md)). Elastic strain gets the same solid cap.

Convergence threshold: `max |delta| / max |state|` across the phase's cells. When below ~1e-3 (configurable), declare converged.

At budget exhaustion, unconverged cells get `CULLED`. They carry current state to next tick.

## Interactions with Stage 1 outputs

Stage 1 (phase resolve) can emit flow sources that Stage 3 consumes in the same tick:

- **Latent-heat shedding:** Stage 1 converted some mass via phase change; the converted mass is *added to the delta buffer* as if a bid had been placed, targeting a fluid neighbor. Stage 3 treats these as pre-queued bids and lets the rest of the pass converge around them.
- **Precipitation deposits:** Stage 1 decided some element exceeded solubility; the composition shift is queued as an internal delta (same cell, no direction). Stage 5 applies.
- **Dissolution pulls:** Stage 1 queues an absorb from a specific neighbor.

This makes Stage 1 "emit flow deltas, don't write state directly" — keeps overflow accounting unified in Stage 5.

## Interactions with Stage 4 (energy)

Mass flow carries energy. When Stage 3 moves composition, it also displaces the thermal energy that composition was carrying. This is **convective heat transport**.

Implementation: Stage 4's per-direction energy delta includes a "convection" component derived from Stage 3's mass delta × source cell's temperature × specific heat. Computed after Stage 3 finishes (so the mass movement is settled), before Stage 4's independent conduction pass.

See [`energy-flow.md`](energy-flow.md).

## What doesn't flow via Stage 3

- **Heat conduction** — handled by Stage 4.
- **Elastic strain** — handled by Stage 2.
- **Phase transitions** — triggered by Stage 1, consumed by Stage 3 as queued deltas.
- **`FIXED_STATE` cell state** — walls, sources, and drains don't move in or out via Stage 3 (they have ∞ barriers / are state-pinned).

## Invariants

- **Mass conservation per element:** Σ composition_delta across all cells = 0 (before refunds; after refunds accounts for any numeric-ceiling losses, which should be zero in a well-behaved scenario).
- **Directional symmetry:** every A→B delta has a matching B-side entry with sign flipped.
- **No flow across NO_FLOW bonds.**
- **No flow from EXCLUDED cells.**
- **Sub-iteration delta monotone:** convergence metric strictly decreases between sub-iterations (or triggers cull).

## Example — iron ball in water tank

Initial: grid of water cells with a 3×3 region of iron composition cells near the top. Gravity `Φ` pulls downward (or toward center, depending on scenario).

First tick, Stage 0e computes μ:
- Iron cells: high ρ_Fe × Φ at their high position → high μ for Fe.
- Water cells below iron: low ρ_Fe × Φ → low μ for Fe (if Fe were there, it would be at lower Φ).
- Simultaneously, for H₂O element: water cells below iron have slight ρ_H₂O × Φ, higher μ than empty water positions elsewhere.

Stage 3 sub-iteration 1:
- Iron cells find Fe μ lower in the downward direction → bid Fe downward.
- Water cells directly below bid H₂O laterally and upward (their H₂O μ is higher than surroundings because gravity locally increased from the incoming Fe).

Over multiple ticks, the Fe composition migrates downward through the water grid. Water composition rises up around it. The spatial pattern looks like "iron ball sinking." But no cell has moved.

Once Fe reaches the bottom (or a solid surface), cohesion bonds form between Fe cells (if allowed by the cohesion rule — see [`cohesion.md`](cohesion.md)), and the iron ball is now a resting bonded solid at the bottom.

## Example — stalactite hanging

Initial: cave ceiling (solid Si), drop of water with trace dissolved Si at a ceiling cell.

Stage 0e: water cell's Si composition is below solubility limit for liquid-H₂O, so Si μ is normal. But adjacent to a solid-Si neighbor above, where Si is "home."

Stage 3: no special flow — water's Si stays in solution.

Over ticks, evaporation (phase transition in Stage 1) removes H and O from the water cell. Its Si *fraction* rises. Eventually it exceeds solubility limit. Stage 1 fires precipitation: Si shifts to the composition slot of the stone above (cohesion-bonded recipient), tiny increment. Stone grows mass-wise; water drips off.

Repeat millions of times = stalactite grows.

## Cost

At 250k cells, per sub-iteration:
- Stage 0e recompute: 250k × 4 slots × ~10 FLOPs ≈ 10 MFLOP
- Stage 3 sweep: 250k × 6 bonds × ~10 FLOPs ≈ 15 MFLOP
- Delta reconcile: ~5 MFLOP

~30 MFLOP per sub-iteration × up to 7 sub-iterations = 210 MFLOP per tick. On a 35 TFLOP GPU, this is microseconds.

Memory bandwidth is the real constraint: 4 MB μ buffer + 12 MB delta buffer = 16 MB read-write per sub-iteration. At 936 GB/s, ~17 µs per sub-iteration. Easy.
