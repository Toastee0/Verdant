"""
Radiation — gen5 §"Borders" + §"Energy flow" (radiation channel).

Once-per-cycle Stefan-Boltzmann emission from cells flagged RADIATES.
Energy leaves the cell as a self-channel energy flux entry; integrate()
applies it as a negative delta to energy_raw.

  P_net = ε(composition, phase) × σ × (T⁴ - T_space⁴) × face_area × dt

For Tier 0 with single-element scenarios the emissivity is read from the
element table per the cell's dominant phase. Solar absorption (incoming
flux on RADIATES cells with `world.solar_flux > 0`) is M5'.6 stretch and
not implemented yet — gen5 §"Radiation" describes the symmetric absorb
path; deferred to a future scenario that actually exercises it.

Radiation runs ONCE per cycle (not per sub-pass) because radiative cooling
is a slow boundary-loss mechanism, not a fast-transport mechanism. It
happens at sub_pass=0 alongside phase transitions and ratchet — all
state-change events fire together at the top of the cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .cell import (
    CellArrays,
    PHASE_GAS,
    PHASE_LIQUID,
    PHASE_PLASMA,
    PHASE_SOLID,
)

if TYPE_CHECKING:
    from .derive import DerivedFields
    from .scenario import WorldConfig


SIGMA = 5.670374419e-8   # Stefan-Boltzmann constant W/(m²·K⁴)
FLAG_RADIATES = 1 << 1


def apply_radiation(
    cells: CellArrays,
    derived: "DerivedFields",
    element_table,
    world: "WorldConfig",
) -> int:
    """Emit blackbody radiation from RADIATES-flagged cells. Mutates
    cells.energy_raw directly (radiation is a state-change event, not
    flux). Returns the number of cells that radiated this call.

    Energy delta in joules per cell per call:
        ΔU = -ε σ (T⁴ - T_space⁴) × face_area × dt

    Sign convention: negative for cooling (T > T_space, default case).
    Converted to raw via element_table[Si].energy_scale (Tier 0 single-
    element); M6'+ multi-element scenarios will need per-cell scale
    weighting.
    """
    n = cells.n
    if n == 0:
        return 0

    radiates = (cells.flags & FLAG_RADIATES) != 0
    if not radiates.any():
        return 0

    face_area = float(world.cell_size_m) ** 2
    t_space_4 = float(world.t_space) ** 4
    dt = float(world.dt)

    # Tier 0: single energy_scale across all cells. M6'+ will need
    # per-cell scaling for mixed-composition emissions.
    first_element = next(iter(element_table))
    energy_scale = float(first_element.energy_scale)

    delta_E_J = np.zeros(n, dtype=np.float32)

    # Per-element contribution: emissivity depends on dominant element
    # and dominant phase. Iterate elements; for each, find cells where
    # this element dominates AND cell is RADIATES.
    dominant_element = derived.majority_element
    dominant_phase = derived.majority_phase
    T = derived.temperature

    for element in element_table:
        emask = (dominant_element == element.element_id) & radiates
        if not emask.any():
            continue
        # Per-phase emissivity
        for phase_id, ε in (
            (PHASE_SOLID,  element.emissivity_solid),
            (PHASE_LIQUID, element.emissivity_liquid),
            (PHASE_GAS,    0.0),  # gas/plasma emissivity not in element table; 0 stub
            (PHASE_PLASMA, 0.0),
        ):
            pmask = emask & (dominant_phase == phase_id)
            if not pmask.any():
                continue
            T_arr = T[pmask]
            P_per_face_J_per_s = ε * SIGMA * (T_arr ** 4 - t_space_4) * face_area
            ΔU_J = -(P_per_face_J_per_s * dt)
            delta_E_J[pmask] += ΔU_J.astype(np.float32)

    # Apply with u16 floor (cell can't go below 0 energy_raw)
    new_E = cells.energy_raw.astype(np.float32) + (delta_E_J / energy_scale)
    new_E = np.maximum(new_E, 0.0)
    cells.energy_raw[:] = np.round(new_E).astype(np.uint16)

    return int(radiates.sum())
