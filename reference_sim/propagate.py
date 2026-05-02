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

from .cell import CellArrays, COMPOSITION_SLOTS, PHASE_GAS, PHASE_LIQUID, PHASE_SOLID
from .derive import DerivedFields
from .element_table import Element, ElementTable
from .flags import CULLED, EXCLUDED, FIXED_STATE, FRACTURED, NO_FLOW
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

    # Tensile-failure detection: per-bond stress. If any bond's |Δε| × E
    # exceeds the cell's tensile_limit, the bond is broken — both endpoints
    # FRACTURED. Fractured cells release stored strain (snapped spring).
    _detect_bond_fracture(cells, element_table, neighbors, cohesion, strain, movable)

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
    """Auction-based mass flow down μ gradient. Returns sub-iterations used.

    For Tier 0 static: every cell at dead-band center, no bid generated,
    converges instantly with zero deltas.
    """
    # For a uniform scenario (every cell identical), ∇μ = 0 and no bids fire.
    # The full auction implementation is in PLAN.md M3 scope; this skeleton
    # pauses at the right hand-off point.
    # TODO: iterate up to phase cap with:
    #       - compute excess = decoded_P - dead_band_center per cell per element
    #       - find eligible downhill neighbors (μ lower)
    #       - distribute excess proportionally to Δμ
    #       - write per-direction per-element deltas to buffers.mass_deltas
    return 0


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

    For Tier 0 static scenario: uniform T means no conduction gradient, no
    mass movement means no convection, no RADIATES flags means no radiation.
    Zero iterations.
    """
    # Radiation pass — once per tick, not per sub-iteration
    _apply_radiation(cells, element_table, derived, buffers, world)

    # TODO: conduction Jacobi loop.
    # TODO: convection coupling — read buffers.mass_deltas produced by Stage 3,
    #       add per-mass-move thermal-energy carried term to buffers.energy_deltas.
    return 0


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
