"""
Phase transitions + Mohs ratchet — gen5 §"Phase transitions" + §"Mohs ratcheting".

Two features in this module:

1. apply_phase_transitions: for each cell, look up target phase from the
   per-element phase diagram against current (T, P). When target ≠ current
   majority phase, shift mass from current phase channel to target channel
   in-place. Composition fractions stay (the same atoms; just a different
   phase). M5'.5 stub: full-cell instantaneous transition. Latent heat is
   M5'.5b — the cell SHOULD lose ε_phase × mass joules on melt/boil and
   gain it back on freeze/condense, but for M5'.5 we leave energy_raw
   untouched at transitions to avoid the c_p discontinuity oscillation
   that bites at small cell sizes (see M5'.5 commit message).

2. apply_ratchet: integrate sustained_overpressure on solid-dominant cells.
   When the integrator crosses RATCHET_TRIGGER, fire mohs_level++, set
   the RATCHETED flag, reset the integrator, and dump compression work
   into the energy field as heat (gen5: "ratcheting is exothermic —
   metamorphic rock is hot").

Both functions mutate cells in place. They run before the flux pipeline
(at sub_pass=0) so flux sees the post-transition state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PHASE_GAS,
    PHASE_LIQUID,
    PHASE_PLASMA,
    PHASE_SOLID,
)

if TYPE_CHECKING:
    from .derive import DerivedFields
    from .phase_diagram import PhaseDiagram1D
    from .scenario import WorldConfig


# Flag bits (mirror cell.py)
FLAG_FIXED_STATE = 1 << 3
FLAG_FRACTURED   = 1 << 5
FLAG_RATCHETED   = 1 << 6


# Ratchet constants — first-order tunable. Real calibration awaits M5'.7+.
# Pressure deviation above this contributes to the integrator (below it,
# integrator decays).
RATCHET_PRESSURE_THRESHOLD = 1000.0       # raw u16 deviation units
# Decay constant (per second) when below threshold
RATCHET_DECAY_RATE         = 0.5
# Integrator level that fires a ratchet
RATCHET_TRIGGER            = 10000.0
# Compression work per ratchet event, expressed as raw energy delta. M5'.5
# stub — proper calibration via material elastic properties lands at M5'.7.
RATCHET_COMPRESSION_WORK_RAW = 50.0
# Maximum mohs_level (Si is 7; diamond is 10)
MOHS_MAX = 10


def apply_phase_transitions(
    cells: CellArrays,
    derived: "DerivedFields",
    world: "WorldConfig",
    phase_diagrams: dict[int, "PhaseDiagram1D"],
) -> int:
    """Per-cell phase resolution against the element's phase diagram.

    Returns the number of cells that transitioned this call. M5'.5 stub:
    full-cell transition (entire phase_mass moves between channels);
    partial transitions for proper latent-heat handling are M5'.5b work.

    FIXED_STATE cells are exempt (their state is held).
    """
    n = cells.n
    if n == 0:
        return 0

    fixed = (cells.flags & FLAG_FIXED_STATE) != 0
    transitions_fired = 0

    majority_phase = derived.majority_phase   # uint8[N]
    majority_element = derived.majority_element  # uint8[N]
    T = derived.temperature
    P = derived.pressure

    for cid in range(n):
        if fixed[cid]:
            continue
        eid = int(majority_element[cid])
        if eid == 0:
            continue
        diagram = phase_diagrams.get(eid)
        if diagram is None:
            continue

        current_phase = int(majority_phase[cid])
        if current_phase == 255:
            continue

        target_phase, target_mohs = diagram.lookup(float(T[cid]), float(P[cid]))
        if target_phase == current_phase:
            continue

        # Shift entire mass from current phase channel to target channel.
        moved_mass = cells.phase_mass[cid, current_phase]
        moved_frac = cells.phase_fraction[cid, current_phase]
        if moved_mass <= 0 and moved_frac <= 0:
            continue

        cells.phase_mass[cid, target_phase]      += moved_mass
        cells.phase_mass[cid, current_phase]      = 0.0
        cells.phase_fraction[cid, target_phase]  += moved_frac
        cells.phase_fraction[cid, current_phase]  = 0.0

        # Mohs handling: solid uses initial_mohs; non-solid is 0
        if target_phase == PHASE_SOLID:
            cells.mohs_level[cid] = max(int(cells.mohs_level[cid]), int(target_mohs))
        else:
            cells.mohs_level[cid] = 0

        transitions_fired += 1

    return transitions_fired


def apply_ratchet(
    cells: CellArrays,
    derived: "DerivedFields",
    world: "WorldConfig",
) -> int:
    """Sustained-overpressure ratchet for solid-dominant cells.

    Each call:
      - Cells above threshold: integrator += (P_dev - threshold) × dt.
      - Cells below threshold: integrator decays exponentially toward 0.
      - When integrator crosses RATCHET_TRIGGER:
          mohs_level = min(mohs_level + 1, MOHS_MAX)
          RATCHETED flag set
          energy_raw += compression_work_raw (clamped to u16 max)
          integrator reset to 0

    Returns the number of cells that ratcheted this call.

    FIXED_STATE cells are exempt.
    """
    n = cells.n
    if n == 0:
        return 0

    is_solid = derived.majority_phase == PHASE_SOLID
    fixed = (cells.flags & FLAG_FIXED_STATE) != 0
    fractured = (cells.flags & FLAG_FRACTURED) != 0
    eligible = is_solid & ~fixed & ~fractured

    if not eligible.any():
        return 0

    P = derived.pressure
    dt = float(world.dt)

    excess = np.maximum(P - RATCHET_PRESSURE_THRESHOLD, 0.0)
    decay = (1.0 - RATCHET_DECAY_RATE * dt)
    if decay < 0:
        decay = 0.0

    # Vectorised update
    new_integrator = cells.sustained_overpressure.copy()
    new_integrator[eligible] = np.where(
        excess[eligible] > 0,
        new_integrator[eligible] + excess[eligible] * dt,
        new_integrator[eligible] * decay,
    )
    new_integrator = np.maximum(new_integrator, 0.0)

    # Fire ratchet
    fire_mask = eligible & (new_integrator > RATCHET_TRIGGER)
    if fire_mask.any():
        new_mohs = np.minimum(cells.mohs_level[fire_mask].astype(np.int32) + 1, MOHS_MAX)
        cells.mohs_level[fire_mask] = new_mohs.astype(np.uint8)
        cells.flags[fire_mask] |= FLAG_RATCHETED
        # Compression work added to energy (with u16 clamp)
        new_energy = cells.energy_raw[fire_mask].astype(np.float32) + RATCHET_COMPRESSION_WORK_RAW
        cells.energy_raw[fire_mask] = np.minimum(np.round(new_energy), 65535.0).astype(np.uint16)
        # Reset integrator on fired cells
        new_integrator[fire_mask] = 0.0

    cells.sustained_overpressure[:] = new_integrator.astype(np.float32)
    return int(fire_mask.sum())


def clear_ratcheted_flag(cells: CellArrays) -> None:
    """Clear the RATCHETED transient flag at the top of each cycle so it
    only marks cells that ratcheted in the most recent cycle."""
    cells.flags &= np.uint8((~FLAG_RATCHETED) & 0xFF)
