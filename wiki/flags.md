# Flags

The `flags: u8` field in the cell struct. Eight bits, each with orthogonal semantics. Combinations create wall kinds and transient-state signals.

## Bit layout

| Bit | Name | Persistent? | Meaning |
|---:|---|:---:|---|
| 0 | `NO_FLOW` | yes | Mass cannot cross this cell's boundary in either direction. |
| 1 | `RADIATES` | yes | This cell emits blackbody radiation to the scenario's `T_space` at Stage 4. |
| 2 | `INSULATED` | yes | Thermal conductivity across this cell's boundary is zero. |
| 3 | `FIXED_STATE` | yes | Cell state is held constant; incoming flows apply to *neighbors* but this cell never updates. |
| 4 | `CULLED` | per-tick | Didn't converge within this sub-iteration's budget. Carries over to next sub-iteration / tick. Cleared at start of each sub-iteration attempt. |
| 5 | `FRACTURED` | yes until healed | Tensile limit exceeded; cell is broken. Can act as a downward bidder (avalanche). Ratcheting or re-compression may heal it. |
| 6 | `RATCHETED` | per-tick | Set when Mohs ratcheting fired this tick. Cleared at tick end. Debug / telemetry only. |
| 7 | `EXCLUDED` | dynamic | Tier 3 overflow hit — numeric saturation on both P and U. Cell is inert until neighborhood resolves enough that rejoining wouldn't immediately re-saturate. See [`overflow.md`](overflow.md). |

## The four persistent bits

These define *what kind of cell it is*. Set at init, rarely change. They determine how flow passes treat this cell.

### `NO_FLOW` (bit 0)
Mass doesn't cross this boundary. In Stage 3 (mass flow), every bond to/from this cell is gated — its μ contribution to neighbors is ∞, so no bidder would win a bid against it; its own bids outward can't leave.

Used for: walls, sealed compartments, incompressible materials.

### `RADIATES` (bit 1)
Stage 4 (energy flow) has a boundary-radiation branch: for each `RADIATES` cell, emit `ε σ T⁴ × dt × area` worth of energy to `T_space` (scenario config). Cooling is proportional to T⁴ — hot things radiate fast.

Used for: grid-edge cells facing vacuum, internal void-interface cells.

### `INSULATED` (bit 2)
Thermal conductivity across this cell's face is zero. Energy flow in Stage 4 skips bonds with either end insulated.

Used for: adiabatic chambers, sealed-insulated walls (with `NO_FLOW`).

### `FIXED_STATE` (bit 3)
Cell state (pressure, energy, composition, strain, magnetization) is never updated by flow passes. Incoming flows to this cell are silently absorbed (or redirected, depending on the pass's rules). The cell's state is whatever the scenario init set.

Used for: fixed-temperature sources/sinks, immutable walls, pinned-boundary cells.

## Composed wall kinds

Every "wall type" from [`walls.md`](walls.md) is a combination of the four persistent bits.

| Wall kind | Flags |
|---|---|
| **Sealed insulated** (perfect sandbox) | `NO_FLOW + INSULATED + FIXED_STATE` |
| **Sealed radiative** (open to space) | `NO_FLOW + RADIATES + FIXED_STATE` |
| **Fixed-T heat source/sink** | `NO_FLOW + FIXED_STATE` (not insulated — neighbors conduct to its held T) |
| **Open-flux drain** | `FIXED_STATE` alone (composition/energy held at ambient; flows in and out constantly but never change this cell) |
| **Hard rigid wall, non-thermal** | `NO_FLOW + INSULATED` (cell state *can* change if scenario injects flows — rare) |

Non-convex bottles, mixed-kind enclosures (hot floor + cold ceiling + radiative sides), and player-placed walls (glass panes, iron plates) all use the same mechanism — just flag different cells differently.

## The four transient / dynamic bits

These reflect *what's happening to the cell this tick/sub-iteration*. Set by flow or resolve passes; cleared by tick ends or by rejoin logic.

### `CULLED` (bit 4)
Set by a sub-iteration that exceeded its convergence budget (gas ≤3, liquid ≤5, solid ≤7). See [`convergence.md`](convergence.md).

Carries to next sub-iteration or tick. The cell keeps its current state and doesn't bid this pass, but it's still *read* as a flow source by its neighbors. Unlike `EXCLUDED`, this isn't a numerical panic — just "we ran out of time this iteration."

Cleared at the start of each new sub-iteration attempt.

### `FRACTURED` (bit 5)
Set when tensile strain exceeded the material's `tensile_limit` in Stage 2 (elastic propagation). The cell is mechanically broken.

Behavior:
- Loses cohesion bonds to all neighbors (cohesion map in Stage 0b checks this flag).
- Becomes eligible as a downward bidder in Stage 3 — fractured debris can avalanche.
- Mohs level may drop by one (damage) or stay the same — TBD scenario-dependent.

Healing: sustained compression (back below fracture threshold) may un-fracture. Ratcheting counts as healing (plastic flow redistributes stress). Specific healing rules live in Stage 1.

### `RATCHETED` (bit 6)
Set when Mohs ratchet fired this tick. Used by the debug emitter and viewer to highlight the event. Not consulted by any physics pass.

Cleared at tick end.

### `EXCLUDED` (bit 7)
The numeric-panic bit. Set when Stage 5a detected that *both* pressure and energy saturated their u16 ceilings simultaneously, refund fired, and the cell is considered numerically untrustworthy.

Behavior:
- Doesn't bid in any flow pass.
- Doesn't accept new bids.
- Doesn't conduct heat outward.
- Doesn't participate in cohesion / elastic propagation.
- State is held at `(p_max, u_max)` — effectively a temporary wall.

Rejoin: each tick, check whether rejoining would immediately re-trigger refund. If not, clear the flag. See [`overflow.md`](overflow.md).

In practice this bit should rarely fire. Persistent `EXCLUDED` regions indicate scenario design problems (physical inputs producing values beyond u16 range) or a bug.

## Invariants and rules

Flags-consistency checks that `verify.py` runs:

- `CULLED` and `EXCLUDED` never set simultaneously (different overflow regimes — `EXCLUDED` supersedes).
- `RATCHETED` implies `phase == solid`.
- `FRACTURED` implies `phase == solid`.
- `FIXED_STATE` cells' stored state never changes between ticks.
- Any cell that has emitted a refund this tick has `EXCLUDED` set.
- A cell with `NO_FLOW` set has exactly zero mass transfers across its boundaries this tick.

## Telemetry

Flag counts are surfaced in `totals` in the JSON emission:

```json
"totals": {
    "cells_culled": 0,
    "cells_ratcheted_this_tick": 3,
    "cells_fractured": 0,
    "cells_excluded": 0,
    "cells_excluded_this_tick": 0,
    "cells_rejoined_this_tick": 0
}
```

Persistent `excluded` count > 0 is a warning in the verifier, not a failure — it's a sign, not a fault.
