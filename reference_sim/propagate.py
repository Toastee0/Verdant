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
from .element_table import ElementTable
from .flags import CULLED, EXCLUDED, FIXED_STATE, NO_FLOW
from .grid import NEIGHBOR_DELTAS, OPPOSITE_DIRECTION
from .scenario import WorldConfig


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
    """Propagate elastic strain through cohesion network. Returns number of
    sub-iterations used.

    For Tier 0 static scenarios (all cells at rest, strain=0): converges in
    0 iterations with no deltas.
    """
    # TODO: stress propagation. For now: no-op (strain stays at rest).
    return 0


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
