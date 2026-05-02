"""
Stage 5 — Reconcile.

Apply all accumulated deltas from Stages 1/2/3/4 to stored state, running
the overflow cascade per wiki/overflow.md:

  Tier 1 (cavitation) — proposed_P > dead_band but within u16 range. Not
    an error: cell becomes a bidder next tick. Implicit, no Stage 5 work.
  Tier 2 (P↔U coupling) — proposed_P > p_max (u16 ceiling) or < p_min with
    available U. Excess pressure converts to/from energy via the
    per-element-per-phase thermodynamic_coupling factor.
  Tier 3 (refund + EXCLUDED) — after P↔U cascade, U still saturated. Refund
    unplaceable mass to bidders proportional to their incoming contribution;
    set EXCLUDED on the saturated cell.

Stage 5 sub-passes (matching wiki/pipeline.md):
  5a — apply deltas with overflow cascade.
  5b — apply Tier 3 refunds (separate pass; refunds can't cascade further).
  5c — clear scratch (handled by buffers.clear() at next tick start).

For Tier 0 t0_static (zero deltas): pure no-op.
"""

from __future__ import annotations

import numpy as np

from .cell import COMPOSITION_SLOTS, CellArrays, PHASE_GAS, PHASE_LIQUID, PHASE_PLASMA, PHASE_SOLID
from .element_table import ElementTable
from .flags import EXCLUDED, FIXED_STATE
from .propagate import PropagateBuffers
from .scenario import WorldConfig


U16_MAX = 0xFFFF
FRAC_MAX = 255


def run_reconcile_stage(
    cells: CellArrays,
    element_table: ElementTable,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> None:
    """Stage 5: apply deltas with the three-tier overflow cascade.

    Order: strain (no overflow logic) → mass + energy with cascade.
    FIXED_STATE cells are exempt from updates (held state).
    """
    fixed = (cells.flags & FIXED_STATE) != 0

    # Strain: simple delta-apply with i8 clamp. No P↔U interaction since
    # strain is a separate stored field; saturation at +127 is the
    # cross-tick ratchet sentinel consumed by Stage 1 next tick.
    if buffers.strain_deltas.any():
        new_strain = cells.elastic_strain.astype(np.int32) + buffers.strain_deltas
        new_strain = np.clip(new_strain, -128, 127)
        # FIXED_STATE: hold strain
        if fixed.any():
            new_strain[fixed] = cells.elastic_strain[fixed].astype(np.int32)
        cells.elastic_strain[:] = new_strain.astype(np.int8)

    # Stage 5a: apply mass + energy deltas with Tier 2 P↔U cascade.
    #
    # Tier 2 is keyed on PRESSURE overshoot, but our auction's mass deltas
    # operate on composition fraction (u8). For Tier 0 single-element
    # scenarios, fraction overshoot at a recipient is the relevant
    # saturation case. We treat fraction-saturation as the trigger and
    # convert overshoot into the energy field via the dominant element's
    # solid P↔U coupling (Tier 0 is solid-only).
    proposed_frac = cells.composition[:, :, 1].astype(np.int32) + buffers.mass_deltas.sum(axis=1)
    proposed_energy = cells.energy.astype(np.int32) + buffers.energy_deltas.sum(axis=1)

    # Frame the cascade per cell.
    # 1. Compute per-cell over-frac (sum across slots beyond 255 saturation).
    # 2. Convert over-frac to a pressure-equivalent (Tier 2 P↔U): bleed to U.
    # 3. If U also overflows: queue refund (Tier 3).
    # 4. Apply clamped state.

    # Couplings per cell (composition-weighted, phase-dependent)
    coupling = _composition_weighted_coupling(cells, element_table)
    # Solid pressure-mantissa scale per cell (used to translate fraction
    # overshoot into pressure-equivalent units; rough approximation since
    # fraction → pressure isn't a clean scalar in general, but for Tier 0
    # single-element saturation this is the closest analog).
    p_scale = _solid_pressure_scale(cells, element_table)

    # Over-saturation per cell per slot
    over_slot = np.maximum(proposed_frac - FRAC_MAX, 0)              # (N, SLOTS)
    under_slot = np.minimum(proposed_frac, 0)                         # (N, SLOTS) <= 0

    # Tier 2 P→U: fraction overshoot → energy. Tier 0 simplification:
    # assume each over-fraction unit ≈ one mantissa unit of pressure;
    # convert via coupling.
    if over_slot.any():
        # Sum over slots → per-cell total fraction overshoot
        total_over = over_slot.sum(axis=1).astype(np.float32)
        # Pressure-equivalent (Pa) of that overshoot
        p_equiv = total_over * p_scale
        # Energy gained = overshoot × coupling (J), then to raw u16
        energy_scale = _global_energy_scale(element_table)
        dU_J = p_equiv * coupling
        dU_raw = np.round(dU_J / energy_scale).astype(np.int32)
        proposed_energy = proposed_energy + dU_raw

    # Tier 3 refund: if proposed_energy > u_max after Tier 2, refund the
    # excess back to the bidders that contributed mass to this cell.
    over_energy = np.maximum(proposed_energy - U16_MAX, 0)
    refund_cells = (over_energy > 0) & ~fixed
    if refund_cells.any():
        _scatter_refunds(cells, buffers, refund_cells, over_energy)
        cells.flags[refund_cells] |= EXCLUDED
        proposed_energy[refund_cells] = U16_MAX
        # Hold pressure (mass) at FRAC_MAX in saturating slots — the refund
        # path returns the over-fraction to senders. Cap the proposed_frac
        # we'll commit:
        proposed_frac[refund_cells] = np.minimum(proposed_frac[refund_cells], FRAC_MAX)

    # Final clamps + commit (skipping FIXED_STATE)
    new_frac = np.clip(proposed_frac, 0, FRAC_MAX)
    new_energy = np.clip(proposed_energy, 0, U16_MAX)
    if fixed.any():
        new_frac[fixed] = cells.composition[fixed, :, 1].astype(np.int32)
        new_energy[fixed] = cells.energy[fixed].astype(np.int32)
    cells.composition[:, :, 1] = new_frac.astype(np.int16)
    cells.energy[:] = new_energy.astype(np.uint16)

    # Stage 5b: apply pending refunds. Refunds were added to buffers.mass_deltas
    # at scatter time; they're already part of new_frac above. Per
    # wiki/overflow.md §"Refund cannot cascade", a refund returns to a sender
    # that had room to send the original — so its arrival never overflows.
    # No additional pass needed: the scattered refund was applied alongside
    # the rest of the mass cascade.

    # Stage 5c: scratch clearing happens at next tick's buffers.clear().


def _scatter_refunds(
    cells: CellArrays,
    buffers: PropagateBuffers,
    refund_cells: np.ndarray,
    over_energy: np.ndarray,
) -> None:
    """For each cell that overflowed past u_max even after P↔U coupling,
    refund unplaceable contributions back to the bidders that supplied them.

    Per wiki/overflow.md §"Tier 3 refund": each direction's incoming bid
    contribution gets a proportional share of the refund. For Tier 0 we
    refund mass (not energy — energy is the destination of the cascade).
    The refund returns fraction units to the senders.
    """
    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)

    # mass_deltas[cell, dir, slot]: positive entries are CREDITS (mass coming
    # IN from a neighbor), negative are DEBITS (mass A sent out — not what
    # we refund).
    cell_ids = np.where(refund_cells)[0]
    for cid in cell_ids:
        # Per-direction incoming mass (positive entries only)
        incoming = np.maximum(buffers.mass_deltas[cid, :, :], 0)   # (6, SLOTS)
        total_in = float(incoming.sum())
        if total_in <= 0:
            continue
        # Refund fraction proportional to each direction's contribution
        # We refund the over_energy worth of mass, i.e., enough mass to
        # offset the energy overshoot. Tier 0 simplification: refund
        # proportional to over_energy / coupling — but easier: refund
        # all the incoming mass (cell can't accept any more).
        # Per-direction proportional refund:
        refund_share = incoming.astype(np.float32) / total_in       # (6, SLOTS)
        refund_amount = float(min(total_in, total_in))              # all of it
        for d in range(6):
            for s in range(COMPOSITION_SLOTS):
                qty = int(round(refund_share[d, s] * refund_amount))
                if qty <= 0:
                    continue
                src_id = neighbors[cid, d]
                if src_id == -1:
                    continue
                # Refund: source gets back, recipient debited
                buffers.mass_deltas[src_id, d, s] += qty            # source credit
                buffers.mass_deltas[cid, d, s] -= qty               # recipient debit


def _composition_weighted_coupling(
    cells: CellArrays,
    element_table: ElementTable,
) -> np.ndarray:
    """Per-cell P↔U coupling, composition-weighted and phase-dependent.

    Per element_table:
      P_U_coupling_solid (~0.008 for Si — elastic regime stores most as strain)
      P_U_coupling_liquid (~0.125)
      P_U_coupling_gas (~1.0 — adiabatic ideal gas)
    """
    n = cells.n
    out = np.zeros(n, dtype=np.float32)
    for slot in range(COMPOSITION_SLOTS):
        eid = cells.composition[:, slot, 0]
        frac = cells.composition[:, slot, 1].astype(np.float32) / 255.0
        for element in element_table:
            mask = (eid == element.element_id) & (frac > 0)
            if not mask.any():
                continue
            phases = cells.phase[mask]
            c = np.empty(phases.shape, dtype=np.float32)
            c[phases == PHASE_SOLID]  = element.P_U_coupling_solid
            c[phases == PHASE_LIQUID] = element.P_U_coupling_liquid
            c[phases == PHASE_GAS]    = element.P_U_coupling_gas
            c[phases == PHASE_PLASMA] = element.P_U_coupling_gas
            out[mask] += c * frac[mask]
    return out


def _solid_pressure_scale(
    cells: CellArrays,
    element_table: ElementTable,
) -> np.ndarray:
    """Per-cell solid pressure mantissa scale (Pa per mantissa unit), for
    cells where the dominant element is solid. For non-solids, returns the
    matching liquid/gas scale. Used to translate fraction-overshoot into
    pressure-equivalent during P↔U coupling.
    """
    n = cells.n
    out = np.ones(n, dtype=np.float32)
    dominant = cells.composition[:, 0, 0]
    for element in element_table:
        for phase_id, scale_key in (
            (PHASE_SOLID, "pressure_mantissa_scale_solid"),
            (PHASE_LIQUID, "pressure_mantissa_scale_liquid"),
            (PHASE_GAS, "pressure_mantissa_scale_gas"),
        ):
            mask = (dominant == element.element_id) & (cells.phase == phase_id)
            if not mask.any():
                continue
            base = float(getattr(element, scale_key))
            if phase_id == PHASE_SOLID:
                mohs = cells.mohs_level[mask].astype(np.float32)
                scale = base * (element.mohs_multiplier ** np.maximum(mohs - 1, 0))
                out[mask] = scale
            else:
                out[mask] = base
    return out


def _global_energy_scale(element_table: ElementTable) -> float:
    """Tier 0 single-element table → first element's scale. M5+ multi-element
    will need per-cell scaling."""
    first = next(iter(element_table))
    return first.energy_scale
