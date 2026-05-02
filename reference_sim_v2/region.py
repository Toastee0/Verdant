"""
Region kernel — the heart of gen5 §"Region kernels".

A region is a 7-cell hex flower (center + 6 neighbours). Each cell is the
center of its own region and a peripheral member of six others. Per gen5,
regions are *overlapping* — each cell participates in up to 7 different
regions' computations per cycle.

In the Python reference we compute one region per cell sequentially (no
GPU parallelism); the algorithm is pure stencil so the result matches a
real parallel execution bit-for-bit.

Per-phase transport rules (gen5 §"Phase-dependent transport rules"):
  - Plasma: gas-like averaging + amplified thermal coupling.
  - Gas: same-phase averaging, opportunistic.
  - Liquid: same-phase averaging with reduced rate; gravity-biased.
  - Solid: non-opportunistic; transmits stress; moves discretely on yield.

For M5'.3 we implement a UNIFIED simple-Fick model parameterised by phase:
    flux[d, slot, phase] = K_phase × max(0, ΔP) × cohesion[d] × dt
                        × phase_fraction × element_fraction
where ΔP = P_self - P_neighbour. K_phase is the per-phase mass conductance
constant. Solid gets a tiny K (still non-zero so sustained gravity loading
eventually displaces solid mass); gas/liquid get larger Ks.

This is intentionally simple. M5'.5 specialises: plasma gets thermal
amplification; liquid gets gravity-bias; solid gets yield-event
displacement instead of continuous Fick. The flux SoA shape stays the
same; only the kernel that fills it gets richer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .cell import (
    COMPOSITION_SLOTS,
    CellArrays,
    N_PHASES,
    N_PETAL_DIRS,
    PHASE_GAS,
    PHASE_LIQUID,
    PHASE_PLASMA,
    PHASE_SOLID,
)
from .flux import FLAG_NO_FLOW, FluxBuffer

if TYPE_CHECKING:
    from .derive import DerivedFields
    from .scenario import WorldConfig


# Per-phase mass conductance — first-order tunable. Solid is non-zero so
# sustained gravity loading eventually moves rock; gas equilibrates fast.
# These constants will be replaced by element-table-driven values at M5'.5.
PHASE_CONDUCTANCE = np.array([
    1e-6,   # solid  — non-opportunistic; only flows under sustained loading
    1e-3,   # liquid — modest opportunistic flow
    1e-2,   # gas    — fastest opportunistic flow
    1e-2,   # plasma — gas-like for mass; thermal amplification at M5'.5
], dtype=np.float32)


def run_region_kernels(
    cells: CellArrays,
    derived: "DerivedFields",
    world: "WorldConfig",
    flux: FluxBuffer,
) -> None:
    """Compute per-cell outgoing mass flux into `flux.mass` for every cell
    in the grid. M5'.3 produces only the mass channel; momentum / energy /
    stress channels stay zero (M5'.4–M5'.6 fill them in).

    The algorithm is fully vectorised — equivalent to running one
    region_kernel per cell in parallel, but expressed as numpy ops so the
    Python ref stays fast enough for Tier 0 grids.

    Mutates `flux.mass` in place; expected to be called between flux.clear()
    and apply_veto().
    """
    n = cells.n
    if n == 0:
        return

    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)        # (N, 6)
    valid = neighbors >= 0                                       # (N, 6)

    # Per-direction pressure deltas (positive = self higher → outgoing flow)
    P_padded = np.concatenate([derived.pressure, np.zeros(1, dtype=np.float32)])
    nbr_P = P_padded[neighbors]                                  # (N, 6)
    dP = derived.pressure[:, None] - nbr_P                       # (N, 6)
    downhill = (dP > 0) & valid                                  # (N, 6)
    effective_dP = np.where(downhill, dP, 0.0).astype(np.float32)

    # Cohesion damping — already computed in derive, blind per-cell-per-direction
    cohesion = derived.cohesion                                  # (N, 6)

    # NO_FLOW gate at the kernel level (veto stage will gate again, but
    # zeroing here saves Inf/NaN risks in case of phase-zero cells)
    flags_padded = np.concatenate([cells.flags, np.array([FLAG_NO_FLOW], dtype=np.uint8)])
    nbr_no_flow = (flags_padded[neighbors] & FLAG_NO_FLOW) != 0
    self_no_flow = (cells.flags & FLAG_NO_FLOW) != 0
    bond_open = ~self_no_flow[:, None] & ~nbr_no_flow            # (N, 6)
    effective_dP = np.where(bond_open, effective_dP, 0.0)

    # Per-phase transport amplitude — phi[N, 6, PHASE]
    # phi = K_phase × dP × cohesion × dt × phase_fraction
    phi = (
        effective_dP[:, :, None]
        * cohesion[:, :, None]
        * float(world.dt)
        * cells.phase_fraction[:, None, :]
        * PHASE_CONDUCTANCE[None, None, :]
    )                                                            # (N, 6, 4)

    # Distribute across composition slots. Each slot's contribution scales
    # with that element's fraction in the source cell. Single-element
    # cells (Tier 0) put 100% in slot 0; mixed-composition cells split.
    slot_frac = cells.composition[:, :, 1].astype(np.float32) / 255.0   # (N, 16)

    # flux.mass[i, d, slot, phase] = phi[i, d, phase] × slot_frac[i, slot]
    np.copyto(flux.mass, phi[:, :, None, :] * slot_frac[:, None, :, None])
