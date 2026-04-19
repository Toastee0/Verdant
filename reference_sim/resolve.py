"""
Stage 1 — Resolve.

The only stage that changes phase, mohs_level, or magnetization. Fires:
- phase transitions (with latent-heat shedding emitted as flow sources)
- ratcheting (mohs_level increments, compression work → energy)
- Curie demagnetization
- precipitation / dissolution (composition shifts)

For Tier 0 scenarios (single-element Si solids at stable states), almost
everything here is a no-op. Skeleton is in place so non-trivial scenarios
can be added incrementally.
"""

from __future__ import annotations

import numpy as np

from .cell import CellArrays, PHASE_SOLID
from .derive import DerivedFields
from .element_table import ElementTable
from .flags import RATCHETED
from .scenario import WorldConfig


def run_resolve_stage(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    world: WorldConfig,
) -> None:
    """Run Stage 1. For Tier 0 static scenarios: clears per-tick transient
    flags and returns. No phase changes, no ratchets, no precipitation."""

    # Clear per-tick transient flags (RATCHETED is set fresh each tick).
    # CULLED is cleared at the start of each sub-iteration in the propagate
    # loop, not here.
    # np.uint8 won't accept a negative int from ~; mask to 8 bits explicitly.
    cells.flags &= np.uint8((~RATCHETED) & 0xFF)

    # TODO Tier 0: ratchet check for cells where elastic_strain exceeded limit
    #              in last tick's Stage 2. For t0_static, elastic_strain is
    #              always 0, so no ratchets.
    # TODO Tier 1: phase resolve — check (P, U, composition) against phase diagram
    # TODO Tier 1: latent-heat shedding on phase transitions
    # TODO Tier 1: precipitation / dissolution
    # TODO Tier 2: Curie demagnetization when T crosses Curie for ferromagnetic cells
