# Overflow Cascade

What happens when a cell's proposed new state would exceed physical or numeric limits after a sub-iteration. Three tiers, each with a distinct rule. Handled in Stage 5a ([`pipeline.md`](pipeline.md)).

## The hierarchy

| Tier | Trigger | Response | Frequency |
|---|---|---|---|
| **Tier 1** | `proposed_P > dead_band_center` but still within u16 range | Cavitation — cell becomes a bidder next sub-iteration; overshoot redistributes | Very common; the normal auction mechanism |
| **Tier 2** | `proposed_P > p_max` (approaching u16 ceiling) | P↔U coupling — excess pressure converts to heat via `thermodynamic_coupling` | Occasional; adiabatic compression, extreme loading |
| **Tier 3** | Both `P > p_max` AND `U > u_max` after P→U coupling | Refund unplaceable mass/energy back to bidders; set `EXCLUDED` flag; hold at (p_max, u_max) | Rare; numerical panic signal |

## Tier 1 — cavitation

**Trigger:** cell accumulated mass / energy bringing pressure above the dead-band center, but still well under u16 max.

**Response:** nothing special at Stage 5a. The cell is just "over-pressured." Next tick's Stage 3 makes it a bidder; it redistributes excess outward via normal auction rules.

This is not really an "overflow" in the error sense — it's the designed behavior. Cavitation is a first-class physical phenomenon: two streams converging at a point can temporarily overpressure that point, and the pressure wave dissipates over subsequent ticks. It's correct.

## Tier 2 — P↔U coupling

**Trigger:** proposed_P would exceed p_max (u16 ceiling).

**Response:** convert excess pressure to energy.

```
if proposed_P > p_max:
    overshoot = proposed_P - p_max
    proposed_U += overshoot × thermodynamic_coupling[phase]
    proposed_P = p_max
```

`thermodynamic_coupling` is per-element, per-phase:

| Phase | Coupling | Physical analog |
|---|---|---|
| gas | ~1.0 | Adiabatic heating — compression work → heat (γ-factor for ideal gas) |
| liquid | ~0.1 | Liquids are nearly incompressible; some heating |
| solid (elastic regime) | ~0.01 | Most compression stores as strain, little heat |
| solid (plastic/post-ratchet) | ~0.1 | Ratcheted solid behaves more fluid-like |

This rule **unifies** three previously-distinct mechanisms:

1. **Ratcheting heating** (Mohs ratchet dumps compression work to energy) — when elastic strain saturates, the P→U conversion fires at the elastic limit, not the u16 ceiling.
2. **Adiabatic compression of gases** — gas cell compressed beyond its phase's ceiling converts excess P to heat; temperature rises.
3. **Numerical ceiling protection** — any cell approaching u16 P max converts rather than wrapping.

All three are the same rule at different thresholds. See [`phase-transitions.md`](phase-transitions.md) for the ratchet case.

### Reverse direction — decompression cooling

Symmetric rule for the low-pressure case:

```
if proposed_P < p_min AND proposed_U > 0:
    deficit = p_min - proposed_P
    draw = deficit × thermodynamic_coupling
    if proposed_U >= draw:
        proposed_U -= draw
        proposed_P = p_min
    else:
        # Cell cooling to zero; at the "vacuum" limit
        proposed_P = p_min
        proposed_U = 0
```

Physical analog: expanding gas cools (refrigeration cycle). Also covers the "cell approaches vacuum" case cleanly — pressure clamps at p_min, energy draws down to absorb the decompression, and a cell that's truly at zero pressure with zero energy is essentially void.

## Tier 3 — refund + EXCLUDED

**Trigger:** after P→U coupling, `proposed_U > u_max`. Energy field saturated. Cannot absorb further.

**Response:**

1. Compute mass/energy excess that cannot fit.
2. Look up per-direction incoming bids from scratch `deltas[cell][direction]`.
3. Scatter refund back proportionally:
   ```
   total_incoming = Σ deltas[cell][direction]
   for each direction d:
       source_cell = neighbor in direction -d
       source_share = deltas[cell][d] / total_incoming
       refund[source_cell] += excess × source_share
   ```
4. Set `flags.EXCLUDED` on the saturated cell.
5. Hold cell at `(p_max, u_max)` — doesn't update further this tick.

### Why refunds, not drops

Silently dropping would break conservation. The refund returns mass/energy to the sources that provided it — always conservative. The source cells receive refunds in a second Stage 5b pass, summed into their own deltas.

### Refund cannot cascade

A refund arriving at a source simply adds to that source's state. The source had room to send out the original bid, so it has room to receive back the refund (can at most return to its pre-bid state). Refunds never need further refunds.

### What EXCLUDED means

The cell is *numerically broken*. Its stored state `(p_max, u_max)` is meaningful but unable to participate in physics this tick without immediately overflowing again. Behavior:

- **Doesn't bid** in any flow pass.
- **Doesn't accept new bids** — bonds to this cell are effectively gated.
- **Doesn't conduct heat outward** — its T-report is saturated, not a real gradient source.
- **Doesn't participate in cohesion / elastic propagation.**
- **Is still visible to verify.py** — verifier can count excluded cells as a telemetry signal.

### Rejoin criterion

Each tick, each `EXCLUDED` cell is checked:

```
simulate_stage_0e(cell)  # compute hypothetical μ with current state + neighbors
hypothetical_bids = simulate_stage_3(cell)  # what would this cell bid?
if hypothetical_bids would_not_trigger_refund:
    clear flags.EXCLUDED
```

Cheap — it's a single-cell replay of the would-be logic. Runs at Stage 1 or Stage 5 (TBD — probably Stage 5 after reconciliation so the check uses the settled new state).

When the neighborhood has bled off enough pressure/energy, the cell rejoins automatically.

## Sim response to refunds

**No global slowdown.** Earlier design considered a `sim_speed_multiplier` that would slow the sim on refund — rejected. The problem is local, the solution should be local.

Instead: the refunding cell becomes `EXCLUDED`, removing it from sim participation until the neighborhood resolves. Sim rate stays constant. Scenario runs at its configured dt forever.

Telemetry on refund frequency is a signal, not a control:

- `refunds_per_tick` consistently > 0: scenario design issue — physical inputs are producing values the u16 precision can't encode.
- `refunds_per_tick` spiking during an event (impact, explosion): expected; the excluded region forms, drains outward, rejoins.
- Persistent `EXCLUDED` region: real bug or fundamentally mis-scaled scenario; investigate.

## Invariants

The verifier (`checker/verify.py`) checks:

- **Conservation through refunds:** sum of (original deltas + refund deltas) per element = 0 across the grid. Nothing lost, nothing gained.
- **Every refund-firing cell has EXCLUDED flag.**
- **No cell with EXCLUDED flag sent any bids this tick.**
- **Rejoin check passed for every cleared EXCLUDED flag.**

## Implementation footnote

Stage 5a, 5b, 5c split:

- **5a:** for each cell, compute proposed state, apply P↔U cascade. If Tier 3, queue refund deltas.
- **5b:** for each cell with pending incoming refunds, apply them. (Separate pass because a cell can simultaneously be a refunded source AND a refund recipient from elsewhere; separating reads and writes keeps Jacobi discipline.)
- **5c:** clear scratch for next tick.

Refund scratch buffer: `refund[cell][element]`, reused per tick, zeroed at 5c. Size ~250k × 4 × 2 B = 2 MB.
