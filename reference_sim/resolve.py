"""
Stage 1 — Resolve.

The only stage that changes phase, mohs_level, or magnetization. Fires:
- Mohs ratcheting (consumes deferred plastic overflow from last tick's Stage 2)
- phase transitions (with latent-heat shedding emitted as flow sources)
- Curie demagnetization
- precipitation / dissolution (composition shifts)

Runtime order per wiki/phase-transitions.md §"Scheduling within Stage 1":
    1. Ratchet check        (deferred from last tick's Stage 2)
    2. Phase resolve        (may queue latent-heat shedding)
    3. Curie demagnetization (after any phase-induced T shift)
    4. Precipitation / dissolution

Per the wiki's "Deltas emitted, not state written" rule, Stage 1 queues
energy and mass deltas into the same PropagateBuffers used by Stages 2/3/4,
so the Stage 5 reconcile cascade sees Stage 1 outputs uniformly.

Direct state writes are limited to mohs_level, phase, magnetization, flags,
and elastic_strain reset — phase/integer state, not flow quantities.

Cross-tick deferred-overflow signal: elastic_strain == ELASTIC_STRAIN_SATURATED
(+127 in i8) marks "compression saturation persisted to end of last tick."
Stage 2 leaves the sentinel; Stage 1 next tick consumes it. This avoids any
new field on CellArrays for Tier 0; revisit when scenarios need fractional
plastic accumulation tracking (session log §4 "cycles_above_threshold").

For Tier 0 single-element Si scenarios at low T with no strain, every
sub-phase is a no-op. Code paths are wired so Tier 1+ scenarios plug in
incrementally.
"""

from __future__ import annotations

import numpy as np

from .cell import (
    COMPOSITION_SLOTS,
    CellArrays,
    PHASE_GAS,
    PHASE_LIQUID,
    PHASE_PLASMA,
    PHASE_SOLID,
)
from .derive import DerivedFields
from .element_table import Element, ElementTable
from .flags import FIXED_STATE, FRACTURED, RATCHETED
from .propagate import PropagateBuffers
from .scenario import WorldConfig


# Sentinel: elastic_strain at +i8 max means "compression saturated; next
# Stage 1 should ratchet." Mirrors the wiki/elastic-flow.md "saturation"
# concept and avoids adding a separate pending-overflow field.
ELASTIC_STRAIN_SATURATED = 127

# Direction-0 in PropagateBuffers.energy_deltas is re-purposed as a "self"
# channel for non-neighbor-bound deltas (radiation, ratchet heat, latent
# heat self-conversion). See propagate.py _apply_radiation for precedent.
SELF_CHANNEL = 0


def run_resolve_stage(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    world: WorldConfig,
    buffers: PropagateBuffers,
) -> None:
    """Run Stage 1 sub-phases in canonical order (ratchet → phase → Curie →
    precipitation). Mutates cells (phase/mohs/magnetization/flags/strain
    direct writes) and buffers (queued energy/mass deltas)."""

    # Clear last tick's RATCHETED transient so this tick's flag reflects
    # only ratchets fired now.
    cells.flags &= np.uint8((~RATCHETED) & 0xFF)

    _resolve_ratchet(cells, element_table, derived, buffers, world)
    transitions = _resolve_phase(cells, element_table, derived, world)
    if transitions is not None and transitions.any():
        _resolve_latent_heat(cells, element_table, derived, buffers, world, transitions)
    _resolve_curie(cells, element_table, derived)
    _resolve_precipitation(cells, element_table, derived, buffers, world)


# --------------------------------------------------------------------------
# Sub-phase 1 — Ratchet check (deferred from last tick's Stage 2)
# --------------------------------------------------------------------------

def _resolve_ratchet(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> None:
    """For each solid cell whose elastic_strain saturated last tick, increment
    mohs_level (capped at element.mohs_max), set RATCHETED, queue compression
    work as a self-channel energy delta, reset strain.

    Per phase-transitions.md §"Ratchet check":
        compression_work = ½ × elastic_limit² / elastic_modulus × V
    (linear-elastic strain energy density at saturation, integrated over cell
    volume). This is the energy that was stored in the elastic spring and is
    now released as heat when the spring "ratchets" to a new dead-band centre.

    For Tier 0 t0_static (zero strain everywhere): no cells qualify; no-op.
    FIXED_STATE cells are exempt — walls don't ratchet.
    """
    solid = (cells.phase == PHASE_SOLID)
    saturated = (cells.elastic_strain == ELASTIC_STRAIN_SATURATED)
    fixed = (cells.flags & FIXED_STATE) != 0
    candidates = solid & saturated & ~fixed
    if not candidates.any():
        return

    volume = world.cell_size_m ** 3
    energy_scale = _global_energy_scale(element_table)

    # Process per dominant element (Tier 0: just Si)
    dominant = cells.composition[:, 0, 0]
    for element in element_table:
        mask = candidates & (dominant == element.element_id)
        if not mask.any():
            continue

        # Cap mohs_level at element.mohs_max. Cells already at the cap
        # fracture instead of ratcheting (per phase-transitions.md §83).
        at_cap = mask & (cells.mohs_level >= element.mohs_max)
        ratchet_mask = mask & ~at_cap

        # Cap-hit → FRACTURED (mass becomes downward bidder; Stage 3 handles).
        if at_cap.any():
            cells.flags[at_cap] |= FRACTURED
            cells.elastic_strain[at_cap] = 0  # spring snaps; strain released

        if ratchet_mask.any():
            # Strain energy at saturation: ½ × σ_y² / E × V
            sigma_y = element.elastic_limit
            E_mod = element.elastic_modulus
            compression_work_j = 0.5 * sigma_y * sigma_y / E_mod * volume

            # Convert J → u16 raw energy units via global energy_scale
            compression_work_raw = int(round(compression_work_j / energy_scale))
            if compression_work_raw < 1:
                compression_work_raw = 1  # ensure observable in u16

            cells.mohs_level[ratchet_mask] = cells.mohs_level[ratchet_mask] + 1
            cells.flags[ratchet_mask] |= RATCHETED
            cells.elastic_strain[ratchet_mask] = 0
            idx = np.where(ratchet_mask)[0]
            buffers.energy_deltas[idx, SELF_CHANNEL] += np.int32(compression_work_raw)


# --------------------------------------------------------------------------
# Sub-phase 2 — Phase resolve
# --------------------------------------------------------------------------

def _resolve_phase(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    world: WorldConfig,
) -> np.ndarray | None:
    """Compute new phase per cell from temperature against composition-weighted
    melt/boil thresholds. Writes new_phase to cells.phase directly (phase is
    integer state, safe to write inline per the wiki).

    Returns a bool mask of cells that transitioned, or None if no transitions.
    The latent-heat sub-phase consumes the mask to queue mass/energy deltas.

    Tier 0 simplification: T-thresholding only. P-T phase diagram (which would
    e.g. raise the melt point of ice under pressure) is M5+ work. For Si at
    Tier 0 energies (T ≪ melt_K), every cell stays solid; no transitions.

    FIXED_STATE cells (walls, sources, drains) hold their phase.
    """
    fixed = (cells.flags & FIXED_STATE) != 0
    if cells.phase[~fixed].size == 0:
        return None

    melt_K = _composition_weighted(cells, element_table, "melt_K")
    boil_K = _composition_weighted(cells, element_table, "boil_K")
    # critical_T is where the gas/plasma distinction begins; Tier 0 ignores
    # plasma (no scenarios reach it). Wired but never fires in Tier 0.
    T = derived.temperature

    new_phase = np.full(cells.n, PHASE_SOLID, dtype=np.uint8)
    new_phase[T >= melt_K] = PHASE_LIQUID
    new_phase[T >= boil_K] = PHASE_GAS
    # FIXED_STATE cells keep their stored phase
    new_phase[fixed] = cells.phase[fixed]

    transitions = (new_phase != cells.phase)
    if not transitions.any():
        return None

    # Direct write — phase is integer state, not a flow quantity.
    cells.phase[transitions] = new_phase[transitions]
    # Going non-solid resets mohs_level (only solids ratchet) and elastic_strain
    going_nonsolid = transitions & (new_phase != PHASE_SOLID)
    if going_nonsolid.any():
        cells.mohs_level[going_nonsolid] = 0
        cells.elastic_strain[going_nonsolid] = 0
    return transitions


# --------------------------------------------------------------------------
# Sub-phase 3 — Curie demagnetization
# --------------------------------------------------------------------------

def _resolve_curie(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
) -> None:
    """Zero magnetization on cells whose dominant element is ferromagnetic and
    whose temperature exceeds its Curie point. Per phase-transitions.md §86.

    Re-magnetization on cool-down (hysteresis) is not implemented yet — Tier
    2+ work, requires B-field from Stage 0d which is itself stubbed.

    For Tier 0 (Si, not ferromagnetic): no-op.
    """
    if cells.magnetization.size == 0 or not (cells.magnetization != 0).any():
        return

    dominant = cells.composition[:, 0, 0]
    T = derived.temperature
    for element in element_table:
        if not element.is_ferromagnetic:
            continue
        mask = (dominant == element.element_id) & (T > element.curie_K)
        if mask.any():
            cells.magnetization[mask] = 0


# --------------------------------------------------------------------------
# Sub-phase 4 — Latent-heat shedding (called from phase resolve transitions)
# --------------------------------------------------------------------------

def _resolve_latent_heat(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
    transitions: np.ndarray,
) -> None:
    """For cells that transitioned phase, queue the latent heat as a
    self-channel energy delta. Per phase-transitions.md §32:

        energy_to_absorb = L_phase × mass × fraction_converted

    Tier 0 simplification: in-place conversion only — entire cell flips phase,
    no mass shedding to a fluid neighbor. The neighbor-search and partial-
    shedding path is Tier 1 (M5) when H₂O scenarios actually exercise it.

    Sign convention: L_fusion is *absorbed* on melting (energy goes into
    breaking bonds, not raising T) — so the energy delta is *negative* for
    solid→liquid. Symmetric for boiling (negative on liquid→gas). On freezing
    or condensation, positive. We compute the signed energy from the
    transition direction.
    """
    if not transitions.any():
        return

    volume = world.cell_size_m ** 3
    energy_scale = _global_energy_scale(element_table)
    dominant = cells.composition[:, 0, 0]

    # We need the OLD phase to know which transition direction we took.
    # _resolve_phase has already overwritten cells.phase, so we infer the
    # old phase from the new phase + the fact that we transitioned. We keep
    # this simple by handling the four common transitions explicitly.
    # (Tier 0 doesn't actually exercise this path; full directional handling
    # comes in M5 with H₂O scenarios.)
    new_phase = cells.phase

    for element in element_table:
        elem_mask = transitions & (dominant == element.element_id)
        if not elem_mask.any():
            continue

        density = _phase_density_for(element, new_phase[elem_mask])
        mass_kg = density * volume

        # Direction inference is approximate: we assume each transition went
        # one phase step from the most likely prior phase. Solid↔Liquid uses
        # L_fusion; Liquid↔Gas uses L_vaporization. For Tier 0 this code
        # path isn't reached.
        latent = np.zeros(elem_mask.sum(), dtype=np.float32)
        np_arr = new_phase[elem_mask]
        # Going to liquid → previous was solid (melt) or gas (condense)
        # Without old-phase tracking we attribute melts as the dominant case.
        liquid_in = (np_arr == PHASE_LIQUID)
        gas_in = (np_arr == PHASE_GAS)
        solid_in = (np_arr == PHASE_SOLID)
        latent[liquid_in] = -element.L_fusion * mass_kg[liquid_in]   # absorb
        latent[gas_in]    = -element.L_vaporization * mass_kg[gas_in]
        latent[solid_in]  = +element.L_fusion * mass_kg[solid_in]    # release

        latent_raw = np.round(latent / energy_scale).astype(np.int32)
        idx = np.where(elem_mask)[0]
        buffers.energy_deltas[idx, SELF_CHANNEL] += latent_raw


# --------------------------------------------------------------------------
# Sub-phase 5 — Precipitation / dissolution
# --------------------------------------------------------------------------

def _resolve_precipitation(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> None:
    """Solubility-driven composition shifts. Per wiki/precipitation.md.

    For each multi-element cell, check non-host fractions against host-phase
    solubility limits. Excess → precipitate (queue mass delta to adjacent
    same-material solid OR crystallize in-place). Deficit + adjacent source
    → dissolve in (queue intake mass delta).

    Tier 0 has only single-element compositions, so the loop never finds an
    eligible (host_element, dissolved_element) pair. M5+ adds H₂O with
    dissolved Si scenarios; this is where solubility tables plug in.
    """
    # Tier 0: count cells with more than one populated composition slot.
    # If none, nothing to do.
    populated = (cells.composition[:, :, 1] > 0).sum(axis=1)
    if not (populated > 1).any():
        return

    # TODO M5+: load solubility table from data/solubility.tsv (not yet
    # written). Iterate per (host_element, dissolved_element) pair, compute
    # excess/deficit, queue deltas via buffers.mass_deltas using a
    # representative direction (or the SELF_CHANNEL convention adapted for
    # mass). The mechanism is well-defined in wiki/precipitation.md but
    # exercised first by t1_precipitate.


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _composition_weighted(
    cells: CellArrays,
    element_table: ElementTable,
    attr: str,
) -> np.ndarray:
    """Per-cell weighted average of an Element scalar across the 4 composition
    slots. fraction is u8 (0..255), normalized by 255 → unitless weights.

    For void cells (all-zero composition) returns 0; callers should mask
    those out before using the result.
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
            value = float(getattr(element, attr))
            out[mask] += value * frac[mask]
    return out


def _phase_density_for(element: Element, phases: np.ndarray) -> np.ndarray:
    """Density per cell in [kg/m³] for a single element across given phases."""
    out = np.empty(phases.shape, dtype=np.float32)
    out[phases == PHASE_SOLID]  = element.density_solid
    out[phases == PHASE_LIQUID] = element.density_liquid
    out[phases == PHASE_GAS]    = element.density_gas_stp
    out[phases == PHASE_PLASMA] = element.density_gas_stp
    return out


def _global_energy_scale(element_table: ElementTable) -> float:
    """Scenario energy_scale (Joules per u16 unit). Tier 0 single-element
    table → first element's scale; Tier 1+ TODO when scenarios mix elements
    with different scales."""
    first = next(iter(element_table))
    return first.energy_scale
