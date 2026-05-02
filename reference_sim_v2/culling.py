"""
Tail-at-Scale culling — gen5 §"Tail at Scale: straggler culling".

Cells whose six-direction flux contributions are all below the noise-floor
ε (`world.noise_floor_epsilon`) cull themselves. Within a cycle, culled
cells skip subsequent sub-passes — their flux contributions are zeroed,
so they neither send nor receive transport. Mid-cycle wake-up: a culled
cell that receives non-zero incoming flux (from a still-active neighbour)
gets un-culled.

For M5'.6 the culling decision is made AFTER each sub-pass: any cell whose
own outgoing flux totals were below ε that sub-pass is marked CULLED for
the next sub-pass. The CULLED flag is cleared at the top of each cycle so
quiescence has to be re-established each tick.

This is an optimisation not a correctness mechanism: a sufficiently
careful implementation would produce identical results without culling,
just slower. For Tier 0 with mostly-static scenarios it dramatically
reduces compute; for Tier 1+ active scenes it falls out naturally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .cell import CellArrays
from .flux import FluxBuffer

if TYPE_CHECKING:
    from .scenario import WorldConfig


FLAG_CULLED = 1 << 4


def update_culled_set(
    cells: CellArrays,
    flux: FluxBuffer,
    world: "WorldConfig",
) -> int:
    """Mark cells whose total outgoing flux this sub-pass is below ε as
    CULLED. Returns the number of cells newly culled this call.

    Per gen5 §"Tail at Scale" the threshold ε is a tunable scenario
    parameter (`world.noise_floor_epsilon`); smaller ε = more sensitive,
    fewer culled, higher fidelity; larger ε = coarser, more culled,
    higher throughput.
    """
    eps = float(world.noise_floor_epsilon)
    if eps <= 0:
        return 0

    # Per-cell magnitude across all flux channels and directions
    mass_mag     = np.abs(flux.mass).sum(axis=(1, 2, 3))   # (N,)
    energy_mag   = np.abs(flux.energy).sum(axis=1)         # (N,)
    momentum_mag = np.abs(flux.momentum).sum(axis=(1, 2))  # (N,)
    stress_mag   = np.abs(flux.stress).sum(axis=1)         # (N,)
    total = mass_mag + energy_mag + momentum_mag + stress_mag

    quiet = total < eps
    new_culls = quiet & ((cells.flags & FLAG_CULLED) == 0)
    if new_culls.any():
        cells.flags[new_culls] |= FLAG_CULLED
    return int(new_culls.sum())


def wake_up_culled_cells(
    cells: CellArrays,
    flux: FluxBuffer,
    world: "WorldConfig",
) -> int:
    """M5'.6c mid-cycle wake-up. A culled cell that's about to receive
    INCOMING flux > ε from any neighbour gets its CULLED flag cleared so
    it participates in the next sub-pass. Per gen5 §"Tail at Scale":
    "regions drop out of the active set as they reach equilibrium and
    rejoin when a flux event wakes them."

    Called between region kernels and integrate, after apply_veto. Looks
    at the per-direction outgoing flux of NEIGHBOURS to determine which
    culled cells are about to receive non-trivial incoming.
    """
    eps = float(world.noise_floor_epsilon)
    if eps <= 0:
        return 0
    n = cells.n
    if n == 0:
        return 0

    culled = (cells.flags & FLAG_CULLED) != 0
    if not culled.any():
        return 0

    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)        # (N, 6)
    from .grid import OPPOSITE_DIRECTION

    # Per-cell incoming magnitude, computed by reading neighbour outgoing
    # in OPPOSITE direction.
    flux_mass_padded = np.concatenate([
        np.abs(flux.mass).sum(axis=(2, 3)),                      # (N, 6)
        np.zeros((1, 6), dtype=np.float32),
    ])
    flux_energy_padded = np.concatenate([
        np.abs(flux.energy),
        np.zeros((1, 6), dtype=np.float32),
    ])

    incoming = np.zeros(n, dtype=np.float32)
    for d in range(6):
        opp = OPPOSITE_DIRECTION[d]
        incoming += flux_mass_padded[neighbors[:, d], opp]
        incoming += flux_energy_padded[neighbors[:, d], opp]

    waking = culled & (incoming > eps)
    if waking.any():
        cells.flags[waking] &= np.uint8((~FLAG_CULLED) & 0xFF)
    return int(waking.sum())


def clear_culled_flag(cells: CellArrays) -> None:
    """At the top of each cycle, clear all CULLED flags so the next cycle
    re-establishes quiescence from scratch. Wake-up due to incoming
    activity is handled implicitly by mid-cycle re-evaluation when M5'.7+
    refines the mid-cycle wake-up logic; for M5'.6 we accept per-cycle
    granularity."""
    cells.flags &= np.uint8((~FLAG_CULLED) & 0xFF)


def mask_culled_in_flux(cells: CellArrays, flux: FluxBuffer) -> None:
    """Zero out flux contributions for cells currently flagged CULLED.
    Called between region_kernel and apply_veto each sub-pass; means
    culled cells contribute nothing this sub-pass."""
    culled = (cells.flags & FLAG_CULLED) != 0
    if not culled.any():
        return
    flux.mass[culled, :, :, :] = 0.0
    flux.momentum[culled, :, :] = 0.0
    flux.energy[culled, :] = 0.0
    flux.stress[culled, :] = 0.0
