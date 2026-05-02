"""
Stages 2/3/4 — Propagate.

Three Jacobi flow passes:
  Stage 2: elastic strain propagation through cohesion network
  Stage 3: mass (element) flow down μ gradient
  Stage 4: energy flow down T gradient (+ convection coupling, + radiation)

Each has a per-phase sub-iteration budget (gas ≤3, liquid ≤5, solid ≤7).
Delta buffers are per-direction, per-element for mass, per-direction for
energy / strain. See wiki/auction.md, wiki/mass-flow.md, wiki/energy-flow.md,
wiki/elastic-flow.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cell import CellArrays, COMPOSITION_SLOTS, PHASE_GAS, PHASE_LIQUID, PHASE_PLASMA, PHASE_SOLID
from .derive import DerivedFields
from .element_table import Element, ElementTable
from .flags import CULLED, EXCLUDED, FIXED_STATE, FRACTURED, INSULATED, NO_FLOW
from .grid import NEIGHBOR_DELTAS, OPPOSITE_DIRECTION
from .scenario import WorldConfig


# Strain encoding convention (mirrored in resolve.py as ELASTIC_STRAIN_SATURATED):
# elastic_strain is i8 in [-127, +127] mapping to normalized strain [-1, +1]
# where ±1 corresponds to ±elastic_limit/elastic_modulus (the yield strain).
# Saturation at +127 is the cross-tick "deferred plastic overflow" sentinel
# consumed by Stage 1's ratchet check next tick.
STRAIN_SATURATION = 127


@dataclass
class PropagateBuffers:
    """Per-sub-iteration scratch for the three flow passes."""

    # Mass: signed per-direction per-element deltas. Reconciled in Stage 5.
    # Shape: (N_cells, 6, COMPOSITION_SLOTS) int32. int32 prevents overflow
    # during accumulation even in cavitation-heavy sub-iterations.
    mass_deltas: np.ndarray

    # Energy: per-direction signed deltas (J)
    energy_deltas: np.ndarray   # (N, 6) int32

    # Elastic strain deltas. Applied at reconcile to cells.elastic_strain.
    strain_deltas: np.ndarray   # (N,) int32

    # Telemetry: per-stage sub-iterations actually used this tick
    iters_stage_2: int = 0
    iters_stage_3: int = 0
    iters_stage_4: int = 0

    @classmethod
    def allocate(cls, cells: CellArrays) -> "PropagateBuffers":
        n = cells.n
        return cls(
            mass_deltas=np.zeros((n, 6, COMPOSITION_SLOTS), dtype=np.int32),
            energy_deltas=np.zeros((n, 6), dtype=np.int32),
            strain_deltas=np.zeros(n, dtype=np.int32),
        )

    def clear(self) -> None:
        self.mass_deltas.fill(0)
        self.energy_deltas.fill(0)
        self.strain_deltas.fill(0)


# --------------------------------------------------------------------------
# Stage 2 — elastic strain
# --------------------------------------------------------------------------

def stage_2_elastic(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> int:
    """Propagate elastic strain through cohesion network via Jacobi sweep.
    Returns sub-iterations used (0 if no work).

    Per wiki/elastic-flow.md: each sub-iteration averages a cell's strain
    over its cohesive neighbors (steady-state Laplacian on the cohesion
    graph). Up to conv_cap_solid (default 7) sub-iterations — the "speed of
    sound" budget for the tick.

    Per-bond stress = E·|Δε_bond|. If any bond exceeds tensile_limit,
    FRACTURED is set on both sides and the broken cell zeroes its strain.
    Cells whose computed strain saturates at +127 (compression yield) leave
    the sentinel for Stage 1 next tick to ratchet on.

    FIXED_STATE cells hold their strain — they're load anchors, not movable
    elastic media. Cells with no cohesion (isolated solid, fluid, fractured)
    spring back toward zero with damping (a simple decay; finite-time
    relaxation is M5+ work).

    For Tier 0 t0_static (all-zero strain everywhere): early-out at 0
    iterations, zero deltas written.
    """
    solid = (cells.phase == PHASE_SOLID)
    fixed = (cells.flags & FIXED_STATE) != 0
    fractured = (cells.flags & FRACTURED) != 0
    movable = solid & ~fixed & ~fractured

    if not movable.any():
        return 0

    # Early-out: if every solid cell already has zero strain, iteration is
    # wasted work. t0_static hits this branch every tick.
    if not (cells.elastic_strain != 0).any():
        return 0

    cohesion = derived.cohesion                         # bool[N, 6]
    coh_count = cohesion.sum(axis=1).astype(np.int32)   # int[N]
    has_coh = coh_count > 0

    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)  # (N, 6)

    # Float work copy for averaging accuracy
    strain = cells.elastic_strain.astype(np.float32).copy()

    # Tensile-failure check on the LOADED state (before Jacobi smoothing).
    # Per wiki/elastic-flow.md, fracture is determined by per-bond stress
    # |Δε|·E exceeding the tensile limit; checking after smoothing would
    # always find the gradient flattened below threshold.
    _detect_bond_fracture(cells, element_table, neighbors, cohesion, strain, movable)

    # Decay factor for cells with no cohesive support (springback toward 0).
    # Half-life of one sub-iteration is a coarse approximation; refine later.
    decay = 0.5

    threshold = float(world.convergence_threshold) * STRAIN_SATURATION
    cap = world.conv_cap_solid

    iters = 0
    for it in range(cap):
        # Pad strain for safe -1 indexing (out-of-grid neighbors → 0)
        strain_padded = np.concatenate([strain, np.zeros(1, dtype=np.float32)])
        nbr_strain = strain_padded[neighbors]   # (N, 6)
        coh_strain = nbr_strain * cohesion.astype(np.float32)
        coh_sum = coh_strain.sum(axis=1)        # (N,)

        new_strain = strain.copy()
        # Cells with cohesion: Jacobi average over cohesive neighbors
        avg = np.zeros_like(strain)
        cohcount_safe = np.maximum(coh_count, 1)
        avg = coh_sum / cohcount_safe.astype(np.float32)
        upd_avg = movable & has_coh
        new_strain[upd_avg] = avg[upd_avg]
        # Cells without cohesion: decay toward 0 (springback)
        upd_decay = movable & ~has_coh
        new_strain[upd_decay] = strain[upd_decay] * decay

        delta = float(np.abs(new_strain - strain).max()) if strain.size else 0.0
        strain = new_strain
        iters = it + 1
        if delta < threshold:
            break

    # Plastic-overflow detection: cells whose post-Jacobi strain saturated at
    # +STRAIN_SATURATION in compression. Hold them at the sentinel for next
    # tick's Stage 1 ratchet check.
    saturated = movable & (strain >= STRAIN_SATURATION)
    if saturated.any():
        strain[saturated] = float(STRAIN_SATURATION)

    # Final clamp into i8 range
    strain = np.clip(strain, -STRAIN_SATURATION, STRAIN_SATURATION)

    # Write delta into the per-cell strain_deltas buffer for Stage 5 reconcile
    delta_int = np.round(strain - cells.elastic_strain.astype(np.float32)).astype(np.int32)
    buffers.strain_deltas[:] = delta_int
    return iters


def _detect_bond_fracture(
    cells: CellArrays,
    element_table: ElementTable,
    neighbors: np.ndarray,
    cohesion: np.ndarray,
    strain: np.ndarray,
    movable: np.ndarray,
) -> None:
    """Per-bond tensile-failure check. For each cohesive bond, compute the
    stress |E·Δε| across the bond. If it exceeds the cell's tensile_limit,
    mark both endpoints FRACTURED and release their strain.

    Per wiki/elastic-flow.md §"Tensile failure": bond break propagates the
    FRACTURED flag to both cells; cohesion is lost across that bond for all
    subsequent stages this tick and next (Stage 0b reads FRACTURED next
    tick to drop the bond).

    For Tier 0 t0_static (zero strain): no bond exceeds tensile, no-op.
    """
    n = cells.n
    # Pad strain for -1 indexing
    strain_padded = np.concatenate([strain, np.zeros(1, dtype=np.float32)])
    nbr_strain = strain_padded[neighbors]   # (N, 6)
    bond_dstrain = strain[:, None] - nbr_strain  # (N, 6)

    # Per-cell tensile_limit and elastic_modulus (composition-weighted)
    tensile_pa = _composition_weighted_scalar(cells, element_table, "tensile_limit")
    modulus_pa = _composition_weighted_scalar(cells, element_table, "elastic_modulus")
    elastic_lim_pa = _composition_weighted_scalar(cells, element_table, "elastic_limit")

    # |Δstrain_normalized| × elastic_limit_pa = bond stress in Pa
    # (normalized_strain * elastic_limit = ε * E by construction of our i8 encoding,
    #  where ε = strain_i8/127 × elastic_limit/elastic_modulus.
    #  σ_bond = E × |ε_a - ε_b| = E × elastic_limit/E × |s_a - s_b|/127
    #         = elastic_limit × |Δs|/127.
    #  So σ_bond_pa = elastic_lim_pa * |bond_dstrain| / STRAIN_SATURATION.)
    bond_stress_pa = np.abs(bond_dstrain) * (elastic_lim_pa[:, None] / STRAIN_SATURATION)

    bond_fail = cohesion & (bond_stress_pa > tensile_pa[:, None])
    if not bond_fail.any():
        return

    # Mark both endpoints. Boundary neighbors (-1) are skipped via cohesion
    # being False there.
    cell_frac = bond_fail.any(axis=1)
    if cell_frac.any():
        cells.flags[cell_frac] |= FRACTURED
        strain[cell_frac] = 0.0

    # Symmetric: also mark the neighbor across each failed bond
    for direction in range(6):
        fail_in_dir = bond_fail[:, direction]
        if not fail_in_dir.any():
            continue
        neigh_ids = neighbors[fail_in_dir, direction]
        valid = neigh_ids != -1
        nbrs = neigh_ids[valid]
        cells.flags[nbrs] |= FRACTURED
        strain[nbrs] = 0.0


def _composition_weighted_scalar(
    cells: CellArrays,
    element_table: ElementTable,
    attr: str,
) -> np.ndarray:
    """Per-cell composition-weighted average of an Element scalar attribute.
    Mirrors resolve._composition_weighted; duplicated here to avoid an
    import cycle between propagate.py and resolve.py.
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
            out[mask] += float(getattr(element, attr)) * frac[mask]
    return out


# --------------------------------------------------------------------------
# Stage 3 — mass flow
# --------------------------------------------------------------------------

def stage_3_mass(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> int:
    """Staged Jacobi auction for mass flow. Returns sub-iterations used.

    Per wiki/auction.md and wiki/mass-flow.md:
      Stage 3a (per cell, parallel):
        - if outside dead-band, compute excess
        - find downhill-μ neighbors (μ_self > μ_nbr) past cohesion + flow gates
        - distribute excess proportionally to Δμ (Fick's law)
        - if zero eligible neighbors → CULLED for next sub-iteration
        - write per-direction per-element deltas
      Stage 3b (per cell, parallel):
        - sum incoming + self residual
        - no clearing (cavitation is a feature; overshoot is allowed)

    Bidder-ignorant capacity: each bidder writes its bid without coordinating
    with other bidders into the same recipient. Multiple bidders into one
    recipient cause cavitation; Stage 5 overflow cascade catches numeric
    extremes.

    Tier 0 simplifications:
      - Dead-band center = 0 in mantissa space (cells at pressure_raw=0 are
        at rest, the explicit convention in t0_static).
      - Excess "amount" is computed in fraction units as
            bid_total = damping × frac_self × (excess_p_normalized)
        where excess_p_normalized = clip(decoded_p / typical_p, 0, 1) and
        damping = 1/cap so convergence completes within the per-phase budget.
      - Cohesion barrier: bonded intact solids cannot bid for their dominant
        element across cohesive bonds (Stage 0b's cohesion graph). Fractured
        solids and fluids ignore the barrier.

    For Tier 0 t0_static (all pressure_raw=0): zero bidders → 0 iterations,
    no deltas, mass conserved exactly.
    """
    n = cells.n
    if n == 0:
        return 0

    fixed = (cells.flags & FIXED_STATE) != 0
    excluded = (cells.flags & EXCLUDED) != 0
    no_flow = (cells.flags & NO_FLOW) != 0
    solid = (cells.phase == PHASE_SOLID)
    fractured = (cells.flags & FRACTURED) != 0

    # Eligibility: not state-pinned, not in flow lockout. Solids must be
    # fractured to bid for their dominant element (intact solids have ∞
    # cohesion_barrier — handled per-bond below for non-dominant elements).
    eligible = ~fixed & ~excluded & ~no_flow

    if not eligible.any():
        return 0

    grid = cells.grid
    neighbors_arr = np.array(grid.neighbors, dtype=np.int32)  # (N, 6)
    cohesion = derived.cohesion                                # bool[N, 6]

    # Cap: pick the most permissive (longest) budget over present phases.
    has_gas = (cells.phase == PHASE_GAS).any()
    has_liquid = (cells.phase == PHASE_LIQUID).any()
    if has_gas:
        cap = world.conv_cap_gas
    elif has_liquid:
        cap = world.conv_cap_liquid
    else:
        cap = world.conv_cap_solid

    threshold = float(world.convergence_threshold)
    damping = max(1.0 / max(cap, 1), 0.05)

    # Per-cell typical pressure for excess normalization (avoids div-by-zero).
    # Use the solid mantissa scale × mohs_factor as the natural pressure unit.
    typical_p = _typical_pressure(cells, element_table)

    # Working composition snapshot — sub-iterations apply against this, never
    # against cells.composition. The accumulated buffer is the single source
    # of truth committed by Stage 5.
    working_frac = cells.composition[:, :, 1].astype(np.int32).copy()  # (N, SLOTS)
    # Pre-existing Stage 1 mass deltas (latent-heat shedding etc.) are
    # already queued in buffers.mass_deltas; fold them into the working
    # state so the auction sees the post-Stage-1 composition.
    if buffers.mass_deltas.any():
        working_frac += buffers.mass_deltas.sum(axis=1)
        working_frac = np.clip(working_frac, 0, 255)

    # Decoded pressure is independent of composition (it's a function of
    # pressure_raw + phase + mohs), so we can read it once. μ depends on
    # composition only through ρ_element × Φ; for Tier 0 (g_sim = 0) Φ is
    # zero and μ ≈ decoded_p, so refreshing μ between sub-iters changes
    # nothing. Tier 1+ scenarios with non-zero Φ will need a per-sub-iter μ
    # refresh on a cells-snapshot — TODO when t1_* lands.
    from .derive import _decode_pressure_all
    decoded_p = _decode_pressure_all(cells, element_table)
    excess_p = np.maximum(decoded_p, 0.0)
    bidding_base = eligible & (excess_p > 1.0)  # 1 Pa floor to ignore noise

    if not bidding_base.any():
        return 0

    iters = 0
    for it in range(cap):
        # Per-direction per-slot Δμ from snapshot derived.mu
        mu = derived.mu                                          # (N, SLOTS)
        mu_padded = np.concatenate([mu, np.zeros((1, mu.shape[1]), dtype=mu.dtype)])
        nbr_mu = mu_padded[neighbors_arr]                        # (N, 6, SLOTS)
        delta_mu = mu[:, None, :] - nbr_mu                       # (N, 6, SLOTS)

        # Bond gates: neighbor exists AND not NO_FLOW
        nbr_valid = neighbors_arr != -1
        no_flow_padded = np.concatenate([no_flow, np.array([True])])
        nbr_no_flow = no_flow_padded[neighbors_arr]
        bond_open = nbr_valid & ~nbr_no_flow                     # (N, 6)

        # Per-slot per-direction eligibility: bond open AND downhill μ.
        downhill = delta_mu > 0                                  # (N, 6, SLOTS)
        eligible_bond = downhill & bond_open[:, :, None]          # (N, 6, SLOTS)

        # Cohesion barrier: intact solids cannot transfer their dominant
        # element across cohesive bonds. (For Tier 0 with all-cohesive Si
        # discs, this gates every direction → no flow. t0_compression
        # therefore needs FRACTURED or fluid composition to demonstrate.)
        intact_solid = solid & ~fractured
        if intact_solid.any():
            block_slot0 = intact_solid[:, None] & cohesion        # (N, 6)
            eligible_bond[:, :, 0] = eligible_bond[:, :, 0] & ~block_slot0

        weighted_dmu = delta_mu * eligible_bond                   # (N, 6, SLOTS)
        total_dmu = weighted_dmu.sum(axis=1)                      # (N, SLOTS)

        # Bid totals per slot:
        #   bid_total_slot = damping × working_frac_slot × clip(excess_p / typical_p, 0..1)
        frac_self = working_frac.astype(np.float32)                # (N, SLOTS)
        excess_norm = np.clip(excess_p / np.maximum(typical_p, 1.0), 0.0, 1.0)
        bid_total_slot = damping * frac_self * excess_norm[:, None]
        any_eligible_slot = (total_dmu > 0)                        # (N, SLOTS)
        active_bid = bidding_base[:, None] & any_eligible_slot
        bid_total_slot = bid_total_slot * active_bid

        # Proportional split across eligible directions
        denom = np.where(total_dmu > 0, total_dmu, 1.0)
        share = weighted_dmu * (bid_total_slot / denom)[:, None, :]
        share_int = np.floor(share).astype(np.int32)               # (N, 6, SLOTS)

        # Bidder-ignorant capacity check (per wiki/auction.md §"deliberate
        # race"): each bid is capped at the recipient's CURRENT remaining
        # slot capacity. Multiple bidders into one recipient can still
        # overshoot (cavitation, physically correct), but no single bid
        # exceeds the recipient's headroom — which prevents the naive
        # 255-clamp at Stage 5 from silently dropping mass.
        working_padded = np.concatenate([working_frac, np.full((1, working_frac.shape[1]), 255, dtype=working_frac.dtype)])
        nbr_frac = working_padded[neighbors_arr]                    # (N, 6, SLOTS)
        nbr_capacity = np.maximum(255 - nbr_frac, 0).astype(np.int32)
        share_int = np.minimum(share_int, nbr_capacity)

        # Source capacity cap: don't bid more than the slot currently holds.
        cell_total = share_int.sum(axis=1)                         # (N, SLOTS)
        over_cap = cell_total > working_frac
        if over_cap.any():
            scale = np.where(over_cap, working_frac.astype(np.float32) / np.maximum(cell_total, 1).astype(np.float32), 1.0)
            share_int = (share_int * scale[:, None, :]).astype(np.int32)

        if not share_int.any():
            # No-path culling: bidders with no eligible slot at all
            no_path = bidding_base & ~any_eligible_slot.any(axis=1)
            if no_path.any():
                cells.flags[no_path] |= CULLED
            break

        # Build per-direction iter_deltas (debit source, credit target)
        iter_deltas = np.zeros_like(buffers.mass_deltas)           # (N, 6, SLOTS) int32
        iter_deltas -= share_int

        for d in range(6):
            send = share_int[:, d, :]                               # (N, SLOTS)
            send_mask = (send.sum(axis=1) > 0)
            if not send_mask.any():
                continue
            dst_ids = neighbors_arr[send_mask, d]
            valid = dst_ids != -1
            if not valid.any():
                continue
            dst_ids_v = dst_ids[valid]
            send_vals = send[send_mask][valid]
            opp = OPPOSITE_DIRECTION[d]
            np.add.at(iter_deltas, (dst_ids_v, opp), send_vals)

        # Apply to working_frac (sub-iter visibility for next iter's bid sizing)
        working_frac = working_frac + iter_deltas.sum(axis=1)
        working_frac = np.clip(working_frac, 0, 255)

        # Accumulate to buffer for Stage 5 commit
        buffers.mass_deltas += iter_deltas

        max_delta = float(np.abs(iter_deltas).max())
        max_frac = float(np.abs(working_frac).max()) or 1.0
        iters = it + 1
        if max_delta / max_frac < threshold:
            break

    return iters


def _typical_pressure(
    cells: CellArrays,
    element_table: ElementTable,
) -> np.ndarray:
    """Per-cell 'natural' pressure scale used to normalize bid sizes.

    For Tier 0 we use the cell's mantissa-scale × mohs_factor (i.e. the
    pressure that mantissa=1 corresponds to). This is the smallest non-zero
    decodable pressure for that cell — using it as the normalization floor
    means a cell at decoded_p ≥ this value bids at full strength.
    """
    n = cells.n
    out = np.ones(n, dtype=np.float32)
    dominant = cells.composition[:, 0, 0]
    for element in element_table:
        for phase_id in (PHASE_SOLID, PHASE_LIQUID, PHASE_GAS):
            mask = (dominant == element.element_id) & (cells.phase == phase_id)
            if not mask.any():
                continue
            if phase_id == PHASE_GAS:
                scale = element.pressure_mantissa_scale_gas * 4096
            elif phase_id == PHASE_LIQUID:
                scale = element.pressure_mantissa_scale_liquid * 4096
            else:
                # Solid: include mohs factor
                mohs = cells.mohs_level[mask].astype(np.float32)
                base = element.pressure_mantissa_scale_solid * 4096
                scale_arr = base * (element.mohs_multiplier ** np.maximum(mohs - 1, 0))
                out[mask] = scale_arr
                continue
            out[mask] = float(scale)
    return out


# --------------------------------------------------------------------------
# Stage 4 — energy flow
# --------------------------------------------------------------------------

def stage_4_energy(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> int:
    """Thermal transport: conduction + convection + radiation.

    Per wiki/energy-flow.md three mechanisms in one pass:
      - Conduction: per-bond ΔU = κ_bond × ΔT × area × dt over T gradient.
      - Convection: from Stage 3's mass deltas — energy rides moved mass.
      - Radiation: blackbody P_net to T_space (once per tick).

    For Tier 0 t0_static: uniform T → no conduction gradient; no mass moves →
    no convection; no RADIATES → no radiation. Zero iterations, zero deltas.
    """
    _apply_radiation(cells, element_table, derived, buffers, world)
    _apply_convection(cells, element_table, derived, buffers, world)
    iters = _stage_4_conduction(cells, element_table, derived, buffers, world)
    return iters


def _stage_4_conduction(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> int:
    """Jacobi conduction sweep. Per-bond ΔU = κ_bond × (T_self - T_nbr) × area × dt
    where κ_bond = min(κ_self, κ_nbr) — series-resistance approximation per
    wiki/energy-flow.md §"Conduction".

    Working-energy snapshot pattern (mirrors Stage 3): cells.energy untouched
    mid-loop; buffer accumulates; Stage 5 commits.

    Convergence on max|ΔU|/max|U| with phase-dependent cap. INSULATED on
    either endpoint zeros the bond.
    """
    n = cells.n
    if n == 0:
        return 0

    insulated = (cells.flags & INSULATED) != 0
    fixed = (cells.flags & FIXED_STATE) != 0

    # Per-cell thermal conductivity (composition-weighted, phase-dependent)
    kappa = _composition_weighted_kappa(cells, element_table)
    # Early-out: zero conductivity everywhere (gas-only scenarios with κ=0)
    # OR uniform temperature (no gradient possible)
    if not (kappa > 0).any():
        return 0
    T_initial = derived.temperature
    if (T_initial.max() - T_initial.min()) < 1e-9:
        return 0

    grid = cells.grid
    neighbors_arr = np.array(grid.neighbors, dtype=np.int32)
    face_area = world.cell_size_m ** 2
    dt = world.dt
    energy_scale = _global_energy_scale(element_table)

    # Pick cap based on most permissive present phase
    has_gas = (cells.phase == PHASE_GAS).any()
    has_liquid = (cells.phase == PHASE_LIQUID).any()
    if has_gas:
        cap = world.conv_cap_gas
    elif has_liquid:
        cap = world.conv_cap_liquid
    else:
        cap = world.conv_cap_solid

    threshold = float(world.convergence_threshold)

    working_energy = cells.energy.astype(np.int32).copy()

    # Pre-compute per-cell c_p × mass for T recomputation between iters
    volume = world.cell_size_m ** 3
    density = _composition_weighted_scalar(cells, element_table, "density_solid")
    # Tier 0 (all solid) approximation; revisit when gas/liquid scenarios exist
    cp = _phase_specific_heat_per_cell(cells, element_table)
    mass = density * volume
    cp_mass = np.maximum(mass * cp, 1e-12)

    iters = 0
    for it in range(cap):
        # Refresh T from working_energy (sub-iter visibility)
        if it == 0:
            T = T_initial.copy()
        else:
            T = (working_energy.astype(np.float32) * energy_scale) / cp_mass

        # Per-direction temperature gradient
        T_padded = np.concatenate([T, np.zeros(1, dtype=np.float32)])
        nbr_T = T_padded[neighbors_arr]                 # (N, 6)
        delta_T = T[:, None] - nbr_T                    # (N, 6) — positive = self hotter

        # Bond gates: neighbor exists, neither endpoint INSULATED
        nbr_valid = neighbors_arr != -1
        insul_padded = np.concatenate([insulated, np.array([True])])
        nbr_insul = insul_padded[neighbors_arr]
        bond_open = nbr_valid & ~nbr_insul & ~insulated[:, None]   # (N, 6)

        # Per-bond conductivity (min)
        kappa_padded = np.concatenate([kappa, np.zeros(1, dtype=np.float32)])
        nbr_kappa = kappa_padded[neighbors_arr]
        kappa_bond = np.minimum(kappa[:, None], nbr_kappa) * bond_open  # (N, 6)

        # ΔU in joules per direction (positive = self → neighbor)
        dU_J = kappa_bond * delta_T * face_area * dt
        dU_int = np.round(dU_J / energy_scale).astype(np.int32)

        # FIXED_STATE cells don't lose or gain energy; zero their rows
        if fixed.any():
            dU_int[fixed, :] = 0

        # Build symmetric per-direction iter_deltas
        iter_deltas = np.zeros_like(buffers.energy_deltas)
        iter_deltas -= dU_int                            # source debit

        # Scatter credits to recipients (drop credits to FIXED_STATE)
        for d in range(6):
            send = dU_int[:, d]
            send_mask = send != 0
            if not send_mask.any():
                continue
            dst_ids = neighbors_arr[send_mask, d]
            valid = dst_ids != -1
            if not valid.any():
                continue
            dst_ids_v = dst_ids[valid]
            send_vals = send[send_mask][valid]
            # Drop credits to FIXED_STATE recipients (held energy)
            non_fixed = ~fixed[dst_ids_v]
            if non_fixed.any():
                opp = OPPOSITE_DIRECTION[d]
                np.add.at(iter_deltas, (dst_ids_v[non_fixed], opp), send_vals[non_fixed])

        # Apply to working_energy
        working_energy = working_energy + iter_deltas.sum(axis=1)
        working_energy = np.clip(working_energy, 0, 0xFFFF)
        # FIXED_STATE: hold their original energy
        if fixed.any():
            working_energy[fixed] = cells.energy[fixed].astype(np.int32)

        # Accumulate to buffer for Stage 5
        buffers.energy_deltas += iter_deltas

        # Convergence
        max_dU = float(np.abs(iter_deltas).max())
        max_U = float(np.abs(working_energy).max()) or 1.0
        iters = it + 1
        if max_dU / max_U < threshold:
            break

    return iters


def _apply_convection(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> None:
    """Energy carried by Stage 3's mass moves: ΔU = mass × c_p × T_source.

    Per wiki/energy-flow.md §"Convection (coupled to Stage 3)": Stage 3's
    delta buffer is the source of truth for mass movements; Stage 4 reads
    those entries and queues per-direction energy deltas in the same
    direction layout.

    Tier 0 stub: no Tier 0 scenario exercises mass-driven heat transport
    (t0_radiate is uniform-composition with conduction only). The mechanism
    is wired so it activates as soon as Stage 3 produces non-zero mass
    deltas; correctness validated when t1_* scenarios land.
    """
    if not buffers.mass_deltas.any():
        return

    grid = cells.grid
    n = cells.n
    energy_scale = _global_energy_scale(element_table)
    volume = world.cell_size_m ** 3
    T = derived.temperature                             # source-cell T snapshot
    composition = cells.composition

    # For each (cell, direction, slot) where mass left this cell
    # (mass_deltas[cell, direction, slot] < 0 ⇒ A → B sent share to neighbor),
    # energy_carried = |share_units| × density × volume / 255 × c_p × T_source.
    # Convert to u16 raw and add to energy_deltas (same direction).
    mass_deltas = buffers.mass_deltas

    for slot in range(COMPOSITION_SLOTS):
        # Find outgoing per direction
        out = -np.minimum(mass_deltas[:, :, slot], 0)   # (N, 6) positive = sent
        if not out.any():
            continue
        # Need element-specific c_p × density for this slot
        eid = composition[:, slot, 0]
        for element in element_table:
            elem_mask = (eid == element.element_id)
            if not elem_mask.any():
                continue
            phase_arr = cells.phase[elem_mask]
            cp_phase = np.empty(phase_arr.shape, dtype=np.float32)
            cp_phase[phase_arr == PHASE_SOLID]  = element.specific_heat_solid
            cp_phase[phase_arr == PHASE_LIQUID] = element.specific_heat_liquid
            cp_phase[phase_arr == PHASE_GAS]    = element.specific_heat_gas
            cp_phase[phase_arr == PHASE_PLASMA] = element.specific_heat_gas
            d_phase = np.empty(phase_arr.shape, dtype=np.float32)
            d_phase[phase_arr == PHASE_SOLID]  = element.density_solid
            d_phase[phase_arr == PHASE_LIQUID] = element.density_liquid
            d_phase[phase_arr == PHASE_GAS]    = element.density_gas_stp
            d_phase[phase_arr == PHASE_PLASMA] = element.density_gas_stp
            mass_per_unit = d_phase * volume / 255.0    # kg per fraction-unit
            cell_mask_idx = np.where(elem_mask)[0]
            for d in range(6):
                send_units = out[cell_mask_idx, d]
                if not send_units.any():
                    continue
                # Energy carried per cell (J)
                E_J = send_units.astype(np.float32) * mass_per_unit * cp_phase * T[cell_mask_idx]
                E_raw = np.round(E_J / energy_scale).astype(np.int32)
                # Source loses; symmetric scatter handled by Stage 3's mass scatter
                # (energy follows mass across the same bond).
                buffers.energy_deltas[cell_mask_idx, d] -= E_raw
                # Credit recipient (opposite direction)
                neighbors_arr = np.array(grid.neighbors, dtype=np.int32)
                dst_ids = neighbors_arr[cell_mask_idx, d]
                valid = dst_ids != -1
                if not valid.any():
                    continue
                opp = OPPOSITE_DIRECTION[d]
                np.add.at(buffers.energy_deltas, (dst_ids[valid], opp), E_raw[valid])


def _composition_weighted_kappa(
    cells: CellArrays,
    element_table: ElementTable,
) -> np.ndarray:
    """Per-cell thermal conductivity in W/(m·K), composition-weighted and
    phase-dependent (per element table)."""
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
            kappa_phase = np.empty(phases.shape, dtype=np.float32)
            kappa_phase[phases == PHASE_SOLID]  = element.thermal_conductivity_solid
            kappa_phase[phases == PHASE_LIQUID] = element.thermal_conductivity_liquid
            kappa_phase[phases == PHASE_GAS]    = element.thermal_conductivity_gas
            kappa_phase[phases == PHASE_PLASMA] = element.thermal_conductivity_gas
            out[mask] += kappa_phase * frac[mask]
    return out


def _phase_specific_heat_per_cell(
    cells: CellArrays,
    element_table: ElementTable,
) -> np.ndarray:
    """Per-cell c_p in J/(kg·K), composition-weighted and phase-dependent."""
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
            cp_phase = np.empty(phases.shape, dtype=np.float32)
            cp_phase[phases == PHASE_SOLID]  = element.specific_heat_solid
            cp_phase[phases == PHASE_LIQUID] = element.specific_heat_liquid
            cp_phase[phases == PHASE_GAS]    = element.specific_heat_gas
            cp_phase[phases == PHASE_PLASMA] = element.specific_heat_gas
            out[mask] += cp_phase * frac[mask]
    return out


def _apply_radiation(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> None:
    """Blackbody radiation from RADIATES-flagged cells to T_space.

    Once-per-tick operation (not per sub-iteration). Writes into
    buffers.energy_deltas[:, 0] as a 'self' contribution channel (direction 0
    is re-purposed here because radiation isn't neighbor-bound; we could
    add a separate self-delta buffer but this keeps memory tight).

    For Tier 0 static (no RADIATES cells): no-op.
    """
    from .flags import RADIATES  # local import to avoid circular

    mask = (cells.flags & RADIATES) != 0
    if not mask.any():
        return

    # P_net = ε σ (T⁴ - T_space⁴) × face_area × dt
    sigma = 5.670374419e-8     # Stefan-Boltzmann
    face_area = world.cell_size_m ** 2
    t_space_4 = world.t_space ** 4

    for element in element_table:
        emissivity_s = element.emissivity_solid
        emissivity_l = element.emissivity_liquid
        for phase_id, emissivity in [(PHASE_SOLID, emissivity_s), (PHASE_LIQUID, emissivity_l)]:
            sel = mask & (cells.phase == phase_id) & (cells.composition[:, 0, 0] == element.element_id)
            if not sel.any():
                continue
            t = derived.temperature[sel]
            dP = emissivity * sigma * (t**4 - t_space_4) * face_area
            dU = -(dP * world.dt)   # negative = cell loses energy
            # Apply to 'self' slot. Using direction 0 as self-delta channel.
            # NOTE: this is a hack for Tier 0; proper design is a separate
            # self_delta array in buffers. Upgrade when Stage 4 grows.
            dU_int = np.round(dU / _global_energy_scale(element_table)).astype(np.int32)
            # Accumulate into direction-0 slot (will be summed with direction
            # contributions at reconcile). Safer to add a dedicated self
            # channel — documented TODO.
            sub_idx = np.where(sel)[0]
            buffers.energy_deltas[sub_idx, 0] += dU_int


def _global_energy_scale(element_table: ElementTable) -> float:
    """Global scenario energy_scale — Tier 0 uses single element."""
    first = next(iter(element_table))
    return first.energy_scale


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------

def run_propagate_stages(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> None:
    """Run Stages 2, 3, 4 in sequence. Returns nothing; all outputs are in
    buffers. Orchestration pattern here is 'serial within tick' — see
    wiki/pipeline.md for discussion of interleaved alternative."""
    buffers.iters_stage_2 = stage_2_elastic(cells, element_table, derived, buffers, world)
    buffers.iters_stage_3 = stage_3_mass(cells, element_table, derived, buffers, world)
    buffers.iters_stage_4 = stage_4_energy(cells, element_table, derived, buffers, world)
