# Auction Mechanics

The rules by which mass (and structurally the other flow primitives) moves between cells. This is a **staged Jacobi auction**: every cell acts independently as a bidder, reading only its local 7-stencil (self + 6 neighbors), writing to a scratch delta buffer. No cell ever sees another cell's actions within the same sub-iteration.

## The rule in one paragraph

Every cell looks at its 6 neighbors. For each element it carries, it computes excess above the dead-band center (the phase's equilibrium pressure). If there is excess, and any neighbor has lower μ, the cell distributes the excess proportionally across downhill neighbors — one-shot, no negotiation. Each bid is a "buy" — executed immediately in the delta buffer, debited from source, credited to target. At end of sub-iteration, deltas are reconciled. Repeat until convergence or budget.

## The Jacobi discipline

State is snapshotted per sub-iteration:

```
state[sub_iter]     — frozen, read-only
deltas[sub_iter]    — scratch, write-only, per-direction
```

Every bidder reads from `state`. Every bid writes to `deltas`. At end of sub-iteration:
```
state[sub_iter + 1] = state[sub_iter] + reconcile(deltas)
```

No read-after-write hazards. Perfectly parallel. Zero atomics beyond per-direction scatter (which is naturally conflict-free — each cell writes to its own direction slot).

## Bidder rules

Per cell, per sub-iteration of Stage 3:

```
1. Read self state + 6 neighbors' state + μ scratch buffer.

2. For each element in self.composition:
     excess = (self.fraction_of_element) - dead_band_center[element, self.phase]
     if excess <= 0: continue  # no excess, no bid

3. For each of 6 neighbors:
     if bond is NO_FLOW-gated: skip
     if self.μ[element] > neighbor.μ[element]:
         # neighbor is downhill in μ for this element
         Δμ = self.μ[element] - neighbor.μ[element]
         eligible_neighbors.append((direction, Δμ))

4. If eligible_neighbors is empty:
     # pressure-locked; no downhill path
     flags.CULLED = true  # carries to next sub-iteration
     continue

5. Distribute excess proportionally to Δμ across eligible neighbors:
     total_Δμ = Σ Δμ across eligible
     for each (direction, Δμ):
         bid_amount = excess × (Δμ / total_Δμ)
         # respect per-bidder capacity check:
         if target_can_accept(neighbor, bid_amount):
             deltas[self][direction][element] -= bid_amount
             deltas[neighbor][opposite_direction][element] += bid_amount

6. Repeat for each element with excess.
```

## "Bidder-ignorant capacity check" — the deliberate race

Step 5 has `target_can_accept(neighbor, bid_amount)`. This checks whether *my bid alone*, added to the neighbor's current state, would exceed that neighbor's physical or numeric limits.

Critically, this check is **ignorant of other bidders.** If cells A and C are both bidding into B, A doesn't know C is also bidding. Both pass their individual checks. Both bids execute. B over-accumulates.

This is intentional.

- Overshoot is cavitation — next sub-iteration, B becomes a bidder outward.
- If overshoot exceeds numeric limits, the overflow cascade catches it ([`overflow.md`](overflow.md)).
- Without bidder-ignorance, we'd need atomic reservations or a clearing round — expensive, serializing, breaks parallelism.

## Dead-band vs equilibrium

A cell is "at rest" if it's within its phase's dead-band:
```
|self.pressure - dead_band_center(phase, composition)| < dead_band_width
```

Within dead-band: no bid. No excess to distribute. Silent and cheap.

Outside dead-band: excess = (self.pressure - dead_band_center). Can be positive (cell is over-pressured) or negative (cell is under-pressured, below equilibrium).

For positive excess: cell bids mass *out*. For negative excess: cell doesn't bid; it will *receive* bids from neighbors with positive excess whose μ gradient points here.

Most cells most of the time are dead-band-compliant. Bids are sparse. The auction cost is proportional to how far from equilibrium the system is — equilibrium scenarios run almost free.

## Sub-iteration convergence

Within a single Stage 3 (mass flow), sub-iterations repeat until either:

1. Max delta across any cell in this phase's work set is below `convergence_threshold`, or
2. Sub-iteration count reaches the phase's budget cap.

Per-phase caps:
- gas: 3 sub-iterations
- liquid: 5 sub-iterations
- solid: 7 sub-iterations

Unconverged cells at budget exhaustion: `CULLED` flag set. They keep their current state and are re-tried next tick. See [`convergence.md`](convergence.md).

## Between sub-iterations

Between two sub-iterations of the same Stage 3:

1. Delta buffer reconciled → new state.
2. μ recomputed (Stage 0e re-run).
3. Next sub-iteration reads the refreshed state and μ.

This matters: μ is refreshed each sub-iteration. Staleness between sub-iterations wastes the convergence budget.

Between ticks (across Stage 5 reconcile):

1. All sub-iterations have run to budget.
2. Accumulated composition / pressure / energy deltas applied in Stage 5.
3. Overflow cascade runs.
4. New tick starts with fresh derive stage.

Cavitation is preserved **between ticks** — the next tick's Stage 0 recomputes μ from the cavitated state, and that state is what the next tick's bidders see. Temporary overshoot is real.

## Multi-element bids in one sub-iteration

A single cell can have excess in multiple element slots simultaneously. Each element's bid is independent:
- Iron cell surrounded by water: Fe composition has no excess (solubility says Fe in H₂O = 0), so no Fe bid. H₂O composition may or may not bid depending on its own gradient.
- Cell with both N₂ and O₂ gases: both may bid, potentially in different directions (N₂ up to lighter gas, O₂ down to denser) — determined by each gas's own ρ_element × Φ contribution.

The delta buffer is per-direction *and* per-element: `deltas[cell][direction][element]`. Memory cost is 250k × 6 × 4 × 2 B = 12 MB (scratch, reused per sub-iter). Fine on 24 GB VRAM.

## Special bidder rules

### Solids as bidders

Intact solids (not `FRACTURED`) have ∞ cohesion_barrier term in their μ — they cannot be outbid in mass flow. Their composition stays put. Stalactites hold together. Rocks don't diffuse.

### Fractured solid bidders

`FRACTURED` cells have zero cohesion_barrier. They bid like fluids, except they only bid *outward* (fracture cannot bid into itself — that's just compaction, handled by the elastic stage). Avalanches emerge from this.

### Fixed-state cells

`FIXED_STATE` cells never bid and never accept bids. Their state is scenario-held. Walls, fixed-T sources, drains all use this.

### Excluded cells

`EXCLUDED` cells neither bid nor accept bids. They wait for neighborhood to calm. See [`overflow.md`](overflow.md).

## Debug observability

Each cell's per-sub-iteration bids and receipts can be dumped for debugging:
```json
"bids_sent": [
  {"to_direction": 2, "element": "Si", "amount": 14.3, "sub_iter": 0},
  ...
],
"bids_received": [
  {"from_direction": 5, "element": "Si", "amount": 14.3, "sub_iter": 0},
  ...
]
```

These are in the emission schema but may be stripped in release/high-volume builds (debug-only). See [`debug-harness.md`](debug-harness.md).

## Invariants the verifier checks

- **Bid conservation:** for every bid A sent to B, B's received-bid list must contain a matching entry. Catches scatter-gather bugs.
- **Delta conservation:** Σ source deltas = -Σ target deltas across the full delta buffer (mass conserved through the sub-iteration, modulo refunds).
- **No bid sourced from an EXCLUDED cell.**
- **No bid targeted at a NO_FLOW-gated boundary.**

See `checker/verify.py`.
