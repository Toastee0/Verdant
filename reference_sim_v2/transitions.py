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


# Phase-transition rate (per simulated second). With dt=1/128 and 7 sub-
# passes per cycle, this gives full transition over ~16 cycles. Hardware-
# friendly: a small per-cycle scalar update, no fixed-point iteration.
# M5'.5b stub — per-element calibration via element_table latent_heat
# columns is M6'+ work.
TRANSITION_RATE_PER_SEC = 8.0


# Ratchet constants — first-order tunable. Real calibration awaits M5'.7+.
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


def _latent_heat_per_kg(
    current_phase: int,
    target_phase: int,
    element,
) -> float:
    """Joules absorbed (negative) or released (positive) per kg converted
    from current_phase to target_phase. Per gen5 §"Phase transitions":
    melting/boiling absorb (energy is consumed by breaking bonds);
    freezing/condensing release.

    Tier 0/1 handles the common four directions (solid↔liquid,
    liquid↔gas). Sublimation, deposition, ionisation latent heats are
    deferred to M6'+ when scenarios actually exercise them.
    """
    L_f = float(element.L_fusion)
    L_v = float(element.L_vaporization)
    if   current_phase == PHASE_SOLID  and target_phase == PHASE_LIQUID: return -L_f
    elif current_phase == PHASE_LIQUID and target_phase == PHASE_SOLID:  return +L_f
    elif current_phase == PHASE_LIQUID and target_phase == PHASE_GAS:    return -L_v
    elif current_phase == PHASE_GAS    and target_phase == PHASE_LIQUID: return +L_v
    elif current_phase == PHASE_SOLID  and target_phase == PHASE_GAS:    return -(L_f + L_v)
    elif current_phase == PHASE_GAS    and target_phase == PHASE_SOLID:  return +(L_f + L_v)
    return 0.0


def apply_phase_transitions(
    cells: CellArrays,
    derived: "DerivedFields",
    world: "WorldConfig",
    phase_diagrams: dict[int, "PhaseDiagram1D"],
    element_table=None,
) -> int:
    """Per-cell rate-limited partial phase transitions with energy-balanced
    latent-heat absorption.

    Each cycle, a fraction TRANSITION_RATE_PER_SEC × dt of the source
    phase's mass shifts to the target phase. Latent heat ΔE_J = L_phase
    × Δm_kg is absorbed (or released) into the cell's energy_raw, with
    u16 floor at 0. Partial transitions avoid the c_p-discontinuity
    oscillation that the full-cell M5'.5 stub triggered at small cell
    sizes (g5_melt would have re-frozen immediately as latent absorption
    drops T below the melt threshold).

    Mohs follows the solid component: while phase_mass[solid] > 0, mohs
    stays at the initial_mohs from the phase diagram (or whatever it was
    before — solid component identity is preserved). When solid mass
    reaches 0, mohs resets to 0. When freezing creates new solid, mohs
    is set to the diagram's initial_mohs.

    FIXED_STATE cells are exempt.

    `element_table` (optional) is used to look up L_fusion / L_vaporization
    per element. When None, latent heat is skipped — the M5'.5 fall-back
    behaviour for backward compatibility with scenarios that don't pass
    a table.
    """
    n = cells.n
    if n == 0:
        return 0

    fixed = (cells.flags & FLAG_FIXED_STATE) != 0
    transitions_fired = 0

    majority_phase = derived.majority_phase
    majority_element = derived.majority_element
    T = derived.temperature
    P = derived.pressure

    rate_per_cycle = TRANSITION_RATE_PER_SEC * float(world.dt)
    if rate_per_cycle > 1.0:
        rate_per_cycle = 1.0   # never overflow a single cycle

    volume = float(world.cell_size_m) ** 3
    eq_solid = float(EQUILIBRIUM_CENTER[PHASE_SOLID])

    elements_by_id: dict[int, object] = {}
    if element_table is not None:
        for el in element_table:
            elements_by_id[el.element_id] = el

    for cid in range(n):
        if fixed[cid]:
            continue
        eid = int(majority_element[cid])
        if eid == 0:
            continue
        diagram = phase_diagrams.get(eid)
        if diagram is None:
            continue

        target_phase, target_mohs = diagram.lookup(float(T[cid]), float(P[cid]))

        element = elements_by_id.get(eid)
        any_transitioned = False

        # Iterate over each non-target phase channel that has mass; drain a
        # rate-limited fraction toward target. This handles mixed cells
        # (e.g., 50/50 solid+liquid in a cell that's already liquid-
        # majority by saturation can still bleed its remaining solid mass
        # to liquid each cycle).
        for current_phase in (PHASE_SOLID, PHASE_LIQUID, PHASE_GAS, PHASE_PLASMA):
            if current_phase == target_phase:
                continue
            avail_mass = float(cells.phase_mass[cid, current_phase])
            avail_frac = float(cells.phase_fraction[cid, current_phase])
            if avail_mass <= 0 and avail_frac <= 0:
                continue

            delta_mass = avail_mass * rate_per_cycle
            delta_frac = avail_frac * rate_per_cycle
            cells.phase_mass[cid, current_phase]     -= np.float32(delta_mass)
            cells.phase_mass[cid, target_phase]      += np.float32(delta_mass)
            cells.phase_fraction[cid, current_phase] -= np.float32(delta_frac)
            cells.phase_fraction[cid, target_phase]  += np.float32(delta_frac)

            if element is not None:
                L_J_per_kg = _latent_heat_per_kg(current_phase, target_phase, element)
                if L_J_per_kg != 0.0:
                    kg_per_unit = element.density_solid * volume / max(eq_solid, 1e-12)
                    delta_mass_kg = delta_mass * kg_per_unit
                    delta_E_J = L_J_per_kg * delta_mass_kg
                    delta_E_raw = float(delta_E_J / max(element.energy_scale, 1e-12))
                    new_E = float(cells.energy_raw[cid]) + delta_E_raw
                    cells.energy_raw[cid] = np.uint16(max(0.0, min(new_E, 65535.0)))

            any_transitioned = True

        if not any_transitioned:
            continue

        # Mohs follows the solid component after this cycle's transitions
        post_solid = float(cells.phase_mass[cid, PHASE_SOLID])
        if post_solid <= 0:
            cells.mohs_level[cid] = 0
        elif target_phase == PHASE_SOLID:
            cells.mohs_level[cid] = max(int(cells.mohs_level[cid]), int(target_mohs))
        # else (melting): keep mohs since solid component remains

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
