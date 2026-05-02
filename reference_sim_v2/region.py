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
    active_phases: set[int] | None = None,
) -> None:
    """Compute per-cell outgoing mass flux into `flux.mass` for every cell
    in the grid. Vectorised stencil compute; equivalent to per-cell
    region kernels run in parallel.

    `active_phases` selects which phase channels contribute flux this
    sub-pass. Phases not in the set get zero flux, freezing their
    phase_mass for this sub-pass (gen5 §"Concurrent phase sub-passes" —
    a phase that has hit its budget stops updating). Default None = all
    phases active (M5'.3 behaviour, unchanged for non-scheduler tests).

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

    # Phase-active mask: gas/liquid/solid/plasma phases that are still
    # within their sub-pass budget contribute; frozen phases get zero
    # flux. Per gen5 §"Concurrent phase sub-passes".
    if active_phases is not None:
        mask = np.zeros(N_PHASES, dtype=np.float32)
        for p in active_phases:
            mask[p] = 1.0
        phi = phi * mask[None, None, :]

    # Distribute across composition slots. Each slot's contribution scales
    # with that element's fraction in the source cell.
    slot_frac = cells.composition[:, :, 1].astype(np.float32) / 255.0   # (N, 16)

    # flux.mass[i, d, slot, phase] = phi[i, d, phase] × slot_frac[i, slot]
    np.copyto(flux.mass, phi[:, :, None, :] * slot_frac[:, None, :, None])


def run_energy_kernels(
    cells: CellArrays,
    derived: "DerivedFields",
    world: "WorldConfig",
    flux: FluxBuffer,
    element_scale: float,
) -> None:
    """Compute per-cell outgoing energy flux from conduction + convection,
    accumulating into flux.energy. Called AFTER run_region_kernels so the
    convective coupling can read flux.mass.

    Conduction (Fick on T gradient, INSULATED-gated):
        flux.energy[A, d] += κ_bond × max(0, T_A - T_B) × area × dt / energy_scale
    Only the warm side contributes positive outgoing flux; cool side
    writes zero. Symmetric net transport falls out of the integration
    step (A loses, B gains) just like mass flow.

    Convection (energy rides moving mass):
        For each (A, d, slot, phase) where flux.mass > 0:
          ΔU_J = mass_flux × kg_per_phase_unit × c_p × T_A
        with kg_per_phase_unit derived from per-phase equilibrium centres
        and the source cell's density.

    Tier 0 resolution caveat: at default cell_size + Si energy_scale=1.0,
    per-tick conduction signal can floor below 1 raw unit (~0.5 J).
    Mathematically correct; visible at Tier 1+ scales.
    """
    n = cells.n
    if n == 0:
        return

    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)        # (N, 6)
    valid = neighbors >= 0

    # ---- conduction ------------------------------------------------------
    T = derived.temperature
    kappa = derived.kappa
    T_padded = np.concatenate([T, np.zeros(1, dtype=np.float32)])
    nbr_T = T_padded[neighbors]                                  # (N, 6)
    dT = T[:, None] - nbr_T                                      # (N, 6)

    kappa_padded = np.concatenate([kappa, np.zeros(1, dtype=np.float32)])
    nbr_kappa = kappa_padded[neighbors]
    kappa_bond = np.minimum(kappa[:, None], nbr_kappa)           # (N, 6)

    # INSULATED gate at the kernel level (apply_veto will gate again)
    flags_padded = np.concatenate([cells.flags, np.array([0xFF], dtype=np.uint8)])
    nbr_flags = flags_padded[neighbors]
    insulated = ((cells.flags[:, None] | nbr_flags) & (1 << 2)) != 0
    bond_open = valid & ~insulated

    area = float(world.cell_size_m) ** 2
    dt = float(world.dt)

    cond_flux_J = np.where(bond_open, kappa_bond * np.maximum(dT, 0.0) * area * dt, 0.0)
    flux.energy += (cond_flux_J / float(element_scale)).astype(np.float32)

    # ---- convection ------------------------------------------------------
    # Energy carried by mass that's leaving cell A in direction d:
    #   ΔU_J = mass_flux × kg_per_unit(phase, A) × c_p_for(slot, phase) × T_A
    # We approximate kg_per_unit using the per-cell composition × phase-
    # fraction blended density (derived.density), and c_p with derived.cp.
    # This is sound for Tier 0 single-element cells; multi-element mixing
    # uses the cell-level blend, which is correct only at uniform
    # composition (good for Tier 0/1).
    volume = float(world.cell_size_m) ** 3
    # Total per-direction mass flux (sum over slots and phases); this
    # represents physical mass leaving in phase-mass units. Convert via
    # the cell's density blend × volume × (1/EQ_CENTER) — we approximate
    # by taking the SOLID equilibrium center because Tier 0/1 source
    # cells are typically solid-or-liquid; gen5's per-element density
    # scaling lands at M6'+ when this matters.
    from .cell import EQUILIBRIUM_CENTER, PHASE_SOLID
    eq_center = float(EQUILIBRIUM_CENTER[PHASE_SOLID])
    kg_per_unit = (derived.density * volume / max(eq_center, 1e-12)).astype(np.float32)
    # Per-direction mass leaving the cell: sum over (slot, phase) of POSITIVE
    # contributions only (negative entries indicate this cell received,
    # not sent — those are recipients of others' bids).
    mass_outgoing_per_direction = np.maximum(flux.mass, 0.0).sum(axis=(2, 3))  # (N, 6)
    convect_J = (
        mass_outgoing_per_direction
        * kg_per_unit[:, None]
        * derived.cp[:, None]
        * T[:, None]
    )
    flux.energy += (convect_J / float(element_scale)).astype(np.float32)
