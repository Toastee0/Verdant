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
    EQUILIBRIUM_CENTER,
    N_PHASES,
    N_PETAL_DIRS,
    PHASE_GAS,
    PHASE_LIQUID,
    PHASE_PLASMA,
    PHASE_SOLID,
    Q_KG,
)
from .flux import DST_PHASE_SENTINEL, FLAG_NO_FLOW, FluxBuffer
from .transitions import _latent_heat_per_kg

if TYPE_CHECKING:
    from .derive import DerivedFields
    from .phase_diagram import PhaseDiagram1D
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
    phase_diagrams: dict[int, "PhaseDiagram1D"] | None = None,
    element_table=None,
) -> None:
    """Compute per-cell outgoing mass flux into `flux.mass` for every cell
    in the grid. Vectorised stencil compute; equivalent to per-cell
    region kernels run in parallel.

    `active_phases` selects which phase channels contribute flux this
    sub-pass. Phases not in the set get zero flux, freezing their
    phase_mass for this sub-pass (gen5 §"Concurrent phase sub-passes" —
    a phase that has hit its budget stops updating). Default None = all
    phases active (M5'.3 behaviour, unchanged for non-scheduler tests).

    `phase_diagrams` (optional) enables the gen5 sorting-ruleset extension
    for cross-phase mass transmutation (verdant_sim_design.md §"Cells are
    indivisible; boundary interactions apply a sorting ruleset" + §"Cross-
    phase dynamics → Evaporation"). When provided, for each (A, d, slot)
    the kernel looks up the phase that A's slot species would adopt at
    neighbour B's (T, P), writes that into flux.dst_phase_per_slot, and
    when src_phase ≠ dst_phase debits the source-cell's energy by
    L_phase_change × Δm_kg (source pays per Q1 verdict). When phase_diagrams
    is None the dst_phase array stays at the sentinel value and integration
    falls back to same-phase routing — Tier 0 behaviour unchanged.

    `element_table` is required when `phase_diagrams` is provided so the
    kernel can look up L_fusion / L_vaporization and density_solid for
    the kg_per_unit conversion.

    Mutates `flux.mass`, `flux.dst_phase_per_slot`, and `flux.energy_self`
    in place; expected to be called between flux.clear() and apply_veto().
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

    # ---- Cross-phase sorting ruleset (gen5 evaporation/sublimation path) ----
    if phase_diagrams:
        _apply_sorting_ruleset(
            cells, derived, world, flux, neighbors, valid, phase_diagrams,
            element_table,
        )


def _apply_sorting_ruleset(
    cells: CellArrays,
    derived: "DerivedFields",
    world: "WorldConfig",
    flux: FluxBuffer,
    neighbors: np.ndarray,
    valid: np.ndarray,
    phase_diagrams: dict[int, "PhaseDiagram1D"],
    element_table,
) -> None:
    """Fill flux.dst_phase_per_slot from neighbour-side phase-diagram lookup,
    and accumulate source-side latent-heat debits into flux.energy_self.

    For each (A, d, slot) where A holds species s and neighbour B exists:
        dst_phase = phase_diagrams[s].lookup(T_B, P_B).phase
        flux.dst_phase_per_slot[A, d, slot] = dst_phase

    For each (A, d, slot, src_phase) entry where flux.mass > 0 and
    src_phase ≠ dst_phase:
        Δm_units = flux.mass[A, d, slot, src_phase]
        Δm_kg    = Δm_units × kg_per_unit(species, density_solid, eq_solid)
        L_J/kg   = _latent_heat_per_kg(src_phase, dst_phase, element)
        flux.energy_self[A] += L_J/kg × Δm_kg / energy_scale
            (sign: melting/boiling → L_J/kg < 0 → debit; freezing/condensing
             → L_J/kg > 0 → credit. Source pays per Q1 verdict.)
    """
    n = cells.n
    if n == 0 or not element_table:
        return

    elements_by_id = {el.element_id: el for el in element_table}
    volume = float(world.cell_size_m) ** 3

    # Precompute per-element per-cell phase decision (shape: dict[eid] -> uint8[N]).
    # Unique element ids that appear in any composition slot.
    eids_flat = cells.composition[:, :, 0].ravel()
    unique_eids = np.unique(eids_flat[eids_flat > 0])

    phase_by_eid: dict[int, np.ndarray] = {}
    for eid in unique_eids:
        eid_int = int(eid)
        diagram = phase_diagrams.get(eid_int)
        if diagram is None:
            continue
        Ts = np.array([row[0] for row in diagram.rows], dtype=np.float32)
        phases = np.array([row[1] for row in diagram.rows], dtype=np.uint8)
        # Last row index where Ts <= T (clip to [0, len-1])
        T_query = derived.temperature
        idx = np.searchsorted(Ts, T_query, side="right") - 1
        idx = np.clip(idx, 0, len(phases) - 1)
        phase_by_eid[eid_int] = phases[idx]   # uint8[N]

    # Build flux.dst_phase_per_slot via per-slot, per-direction iteration.
    # For each slot s: which species does A hold there? Look up that species
    # at neighbour B's temperature → that's the dst_phase.
    slot_eids_A = cells.composition[:, :, 0]   # int16[N, 16]

    for slot in range(COMPOSITION_SLOTS):
        eids_A = slot_eids_A[:, slot]   # int16[N], 0 = empty slot
        # Per-direction lookups (small loop; fully vectorised within each)
        for d in range(N_PETAL_DIRS):
            nbr_idx = neighbors[:, d]
            edge_valid = valid[:, d]
            # Per-element scatter: only cells whose slot s holds element e
            # get dst_phase[A,d,s] = phase_by_eid[e][B].
            for eid_int, phase_at_cell in phase_by_eid.items():
                mask = edge_valid & (eids_A == eid_int)
                if not mask.any():
                    continue
                Bs = nbr_idx[mask]
                flux.dst_phase_per_slot[mask, d, slot] = phase_at_cell[Bs]

    # Latent heat debit on cross-phase events. Loop over the small
    # 4×4×16 (src_phase, dst_phase, slot) space; per-element scaling is a
    # cell-level mask, kept compact. Under gen5 log encoding flux.energy_self
    # is in pure joules; the integration step decodes/encodes through the
    # log-scale at the canonical-state boundary.
    for slot in range(COMPOSITION_SLOTS):
        eids_A = slot_eids_A[:, slot]                     # int16[N]
        for eid_int in phase_by_eid.keys():
            element = elements_by_id.get(eid_int)
            if element is None:
                continue
            # Universal kg-per-hex-unit — gen5 phase_mass↔kg semantics:
            # 1 hex unit of phase_mass = Q_KG kg regardless of phase channel.
            kg_per_unit = Q_KG

            slot_mask_A = (eids_A == eid_int)              # bool[N]
            if not slot_mask_A.any():
                continue

            # For each (d, src_phase, dst_phase) combination check transmute:
            for d in range(N_PETAL_DIRS):
                # Per-cell dst_phase for this (slot, d), restricted to A cells holding eid:
                dst_per_A = flux.dst_phase_per_slot[:, d, slot]   # uint8[N]
                edge_active = slot_mask_A & (dst_per_A != DST_PHASE_SENTINEL)
                if not edge_active.any():
                    continue
                for src_p in range(N_PHASES):
                    # Asymmetry per Q3 verdict: only fire latent-heat debit
                    # for transitions to a higher-energy phase (dst > src).
                    # Reverse transitions (condensation, freezing, deposition,
                    # recombination) defer to in-place apply_phase_transitions
                    # at the destination — no source-side latent debit here.
                    mass_entry = flux.mass[:, d, slot, src_p]         # f32[N]
                    fire = edge_active & (dst_per_A > np.uint8(src_p)) & (mass_entry > 0.0)
                    if not fire.any():
                        continue
                    dst_phase_fire = dst_per_A[fire].astype(np.int32)
                    fire_indices = np.where(fire)[0]
                    for dst_p_val in np.unique(dst_phase_fire):
                        inner = (dst_phase_fire == dst_p_val)
                        if not inner.any():
                            continue
                        cells_idx = fire_indices[inner]
                        L = _latent_heat_per_kg(src_p, int(dst_p_val), element)
                        if L == 0.0:
                            continue
                        delta_m_kg = mass_entry[cells_idx] * kg_per_unit
                        delta_E_J = L * delta_m_kg
                        flux.energy_self[cells_idx] += np.float32(delta_E_J)


def run_energy_kernels(
    cells: CellArrays,
    derived: "DerivedFields",
    world: "WorldConfig",
    flux: FluxBuffer,
    element_scale: float = 1.0,    # vestigial: gen5 log-encoding ignores per-element scale
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
    flux.energy += cond_flux_J.astype(np.float32)

    # ---- convection ------------------------------------------------------
    # Energy carried by mass that's leaving cell A in direction d:
    #   ΔU_J = mass_flux × kg_per_unit(phase, A) × c_p_for(slot, phase) × T_A
    # We approximate kg_per_unit using the per-cell composition × phase-
    # fraction blended density (derived.density), and c_p with derived.cp.
    # This is sound for Tier 0 single-element cells; multi-element mixing
    # uses the cell-level blend, which is correct only at uniform
    # composition (good for Tier 0/1).
    # Total per-direction mass flux (sum over slots and phases); this
    # represents physical mass leaving in phase-mass units. Under gen5
    # phase_mass↔kg semantics (M6'.x), 1 hex unit = Q_KG kg universally,
    # regardless of phase channel. So the kg-per-unit conversion is
    # simply Q_KG — no element- or phase-dependent factor needed.
    mass_outgoing_per_direction = np.maximum(flux.mass, 0.0).sum(axis=(2, 3))  # (N, 6)
    convect_J = (
        mass_outgoing_per_direction
        * np.float32(Q_KG)
        * derived.cp[:, None]
        * T[:, None]
    )
    flux.energy += convect_J.astype(np.float32)
