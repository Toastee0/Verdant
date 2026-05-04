"""
Phase transitions + Mohs ratchet — gen5 §"Phase transitions" + §"Mohs ratcheting".

Two features in this module:

1. apply_phase_transitions: for each cell, look up target phase from the
   per-element phase diagram against current (T, P). When the cell holds
   mass in a non-target phase, convert just enough of it that the cell's
   T lands exactly on the phase boundary. This is the energy-balanced
   formulation (M5'.5c): in physical reality latent heat absorption
   prevents the c_p-discontinuity oscillation our earlier rate-limited
   stub allowed. The cell is self-stabilising — sits at the boundary
   while transitioning, only crosses once all of one phase's mass has
   converted (or once excess thermal energy is exhausted).

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
    Q_KG,
)

if TYPE_CHECKING:
    from .derive import DerivedFields
    from .phase_diagram import PhaseDiagram1D
    from .scenario import WorldConfig


# Flag bits (mirror cell.py)
FLAG_FIXED_STATE = 1 << 3
FLAG_FRACTURED   = 1 << 5
FLAG_RATCHETED   = 1 << 6


# Per-cycle safety cap on the energy-balance Δm. With properly-calibrated
# materials (real cp ratios of ~2× across phase boundaries), the analytical
# Δm derived from constant-cp_blend assumption lands close enough to the
# boundary that a full transition is fine. Our Tier 1 (H,114)+(O,141) water
# compound has cp_liquid/cp_solid ≈ 4.7× — far above real water's ~2×.
# Under that mis-calibration the constant-cp assumption breaks down at full
# conversion (cp jumps so much that T overshoots T_boundary by hundreds
# of kelvin), so we cap Δm to a small fraction of avail per cycle. Once
# M6'.x compound calibration lands, this cap can rise back to 1.0.
MAX_TRANSITION_FRACTION_PER_CYCLE: float = 0.0625    # 1/16, matches old rate-limit cadence


# Ratchet constants — first-order tunable. Real calibration awaits M5'.7+.
RATCHET_PRESSURE_THRESHOLD = 1000.0       # raw u16 deviation units
# Decay constant (per second) when below threshold
RATCHET_DECAY_RATE         = 0.5
# Integrator level that fires a ratchet
RATCHET_TRIGGER            = 10000.0
# Compression work per ratchet event, in joules. Calibration via material
# elastic properties lands at M5'.7.
RATCHET_COMPRESSION_WORK_J = 50.0
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
    """Per-cell energy-balanced partial phase transitions (M5'.5c).

    For each non-target phase that holds mass, compute Δm such that the
    cell's resulting T lands on the boundary between current_phase and
    target_phase. The mechanics:

        T_after = (E - L_J/kg × Δm_kg) / (m_total_kg × cp_blend)
        set T_after = T_boundary
        ⇒  Δm_kg = (T_now - T_boundary) × m_total_kg × cp_blend / L_J/kg

    where L_J/kg < 0 for endothermic transitions (melting, vaporising,
    sublimation, ionisation) and > 0 for exothermic ones (freezing,
    condensing, deposition, recombination). The sign of (T_now - T_boundary)
    matches the sign of L_J/kg, so Δm_kg comes out positive in the common
    case. We clamp to [0, avail_mass] — exhausting one phase's mass takes
    the cell across the boundary cleanly.

    Latent heat is debited (or credited) at the cell's energy_raw via the
    log-encoding helpers in encoding.py.

    Mohs follows the solid component: while phase_mass[solid] > 0, mohs
    stays at the diagram-derived initial_mohs (or whatever it was before
    — solid component identity is preserved). When solid mass reaches 0,
    mohs resets to 0. When freezing creates new solid, mohs is set to the
    diagram's initial_mohs.

    FIXED_STATE cells are exempt. `element_table` is required to look up
    L_fusion / L_vaporization and density_solid; if None, no transitions
    fire (caller didn't ask for energy-balanced physics).
    """
    n = cells.n
    if n == 0 or element_table is None:
        return 0

    from .encoding import decode_energy_J_scalar, encode_energy_J_scalar

    fixed = (cells.flags & FLAG_FIXED_STATE) != 0
    transitions_fired = 0

    majority_element = derived.majority_element
    T_arr = derived.temperature
    P_arr = derived.pressure
    cp_arr = derived.cp
    density_arr = derived.density

    volume = float(world.cell_size_m) ** 3

    elements_by_id = {el.element_id: el for el in element_table}

    for cid in range(n):
        if fixed[cid]:
            continue
        eid = int(majority_element[cid])
        if eid == 0:
            continue
        diagram = phase_diagrams.get(eid)
        if diagram is None:
            continue
        element = elements_by_id.get(eid)
        if element is None:
            continue

        T_now = float(T_arr[cid])
        target_phase, target_mohs = diagram.lookup(T_now, float(P_arr[cid]))

        cp_blend = float(cp_arr[cid])
        m_total_kg = float(density_arr[cid]) * volume
        if m_total_kg <= 0 or cp_blend <= 0:
            continue
        # kg_per_unit is universal: 1 hex unit of phase_mass = Q_KG kg
        # regardless of which phase channel holds it. This is the gen5
        # phase_mass↔kg semantics — transitions transfer hex units 1:1
        # and kg conserves trivially.
        kg_per_unit = Q_KG

        any_transitioned = False
        for current_phase in (PHASE_SOLID, PHASE_LIQUID, PHASE_GAS, PHASE_PLASMA):
            if current_phase == target_phase:
                continue
            avail_mass = float(cells.phase_mass[cid, current_phase])
            avail_frac = float(cells.phase_fraction[cid, current_phase])
            if avail_mass <= 0 and avail_frac <= 0:
                continue

            L_J_per_kg = _latent_heat_per_kg(current_phase, target_phase, element)
            if L_J_per_kg == 0.0:
                continue

            T_boundary = diagram.transition_threshold_T(current_phase, target_phase)
            if T_boundary is None:
                continue

            # Energy-balance Δm. Starting from
            #   E_after = E + L_J_per_kg × Δm_kg     (L < 0 endothermic)
            #   T_after = E_after / (m × cp)
            # set T_after = T_boundary and solve:
            #   Δm_kg = (T_boundary - T_now) × m × cp / L_J_per_kg
            # For melting (T_now > T_boundary, L < 0): num>0, den<0… wait, no.
            # T_now > T_boundary ⇒ (T_boundary - T_now) < 0; L < 0; ratio > 0. ✓
            # For freezing (T_now < T_boundary, L > 0): num>0, den>0, ratio>0. ✓
            delta_mass_kg = (T_boundary - T_now) * m_total_kg * cp_blend / L_J_per_kg
            if delta_mass_kg <= 0:
                # Cell already on the "stable" side of the boundary for this
                # transition direction (e.g. solid mass present, target
                # liquid, but cell T already at or below T_melt). No
                # transition fires this sub-pass.
                continue

            delta_mass_units = delta_mass_kg / kg_per_unit
            cap = MAX_TRANSITION_FRACTION_PER_CYCLE * avail_mass
            delta_mass_units = min(delta_mass_units, avail_mass, cap)
            if delta_mass_units <= 0:
                continue

            # Phase fraction tracks the same proportion as phase mass
            # (caller maintains this invariant at scenario init).
            frac_ratio = (delta_mass_units / avail_mass) if avail_mass > 0 else 0.0
            delta_frac = avail_frac * frac_ratio

            cells.phase_mass[cid, current_phase]     -= np.float32(delta_mass_units)
            cells.phase_mass[cid, target_phase]      += np.float32(delta_mass_units)
            cells.phase_fraction[cid, current_phase] -= np.float32(delta_frac)
            cells.phase_fraction[cid, target_phase]  += np.float32(delta_frac)

            # Apply latent heat through log-encoding decode/re-encode
            delta_mass_kg_actual = delta_mass_units * kg_per_unit
            delta_E_J = L_J_per_kg * delta_mass_kg_actual
            current_E_J = decode_energy_J_scalar(int(cells.energy_raw[cid]))
            new_E_J = max(0.0, current_E_J + delta_E_J)
            cells.energy_raw[cid] = np.uint16(encode_energy_J_scalar(new_E_J))

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
        # Compression work added to energy through log-encoded round-trip.
        from .encoding import decode_energy_J, encode_energy_J
        fired_E_J = decode_energy_J(cells.energy_raw[fire_mask]) + RATCHET_COMPRESSION_WORK_J
        cells.energy_raw[fire_mask] = encode_energy_J(fired_E_J)
        # Reset integrator on fired cells
        new_integrator[fire_mask] = 0.0

    cells.sustained_overpressure[:] = new_integrator.astype(np.float32)
    return int(fire_mask.sum())


def clear_ratcheted_flag(cells: CellArrays) -> None:
    """Clear the RATCHETED transient flag at the top of each cycle so it
    only marks cells that ratcheted in the most recent cycle."""
    cells.flags &= np.uint8((~FLAG_RATCHETED) & 0xFF)
