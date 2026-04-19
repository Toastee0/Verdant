"""
Stage 5 — Reconcile.

Apply all accumulated deltas from Stages 1/2/3/4 to stored state, running the
overflow cascade (Tier 1 cavitation → Tier 2 P↔U coupling → Tier 3 refund +
EXCLUDED). See wiki/overflow.md.

For Tier 0 static scenarios with zero deltas: a pure no-op.
"""

from __future__ import annotations

import numpy as np

from .cell import CellArrays, COMPOSITION_SLOTS
from .element_table import ElementTable
from .propagate import PropagateBuffers
from .scenario import WorldConfig


def run_reconcile_stage(
    cells: CellArrays,
    element_table: ElementTable,
    buffers: PropagateBuffers,
    world: WorldConfig,
) -> None:
    """Apply deltas → new state, with overflow cascade.

    Tier 0 minimal implementation:
      - Sum per-direction deltas into per-cell totals
      - Apply to composition/energy
      - Clamp at bounds (u16, u8 fractions)
      - TODO Tier 1: P↔U coupling, refund routing, EXCLUDED flag
    """
    # Mass: sum across 6 directions per element slot, then apply
    # buffers.mass_deltas shape: (N, 6, SLOTS)
    mass_per_cell = buffers.mass_deltas.sum(axis=1)  # (N, SLOTS)
    if mass_per_cell.any():
        new_fractions = cells.composition[:, :, 1].astype(np.int32) + mass_per_cell
        # Clamp fractions into valid u8 range [0, 255]
        new_fractions = np.clip(new_fractions, 0, 255)
        cells.composition[:, :, 1] = new_fractions.astype(np.int16)

    # Energy: sum direction deltas, apply with u16 clamp
    energy_per_cell = buffers.energy_deltas.sum(axis=1)  # (N,)
    if energy_per_cell.any():
        new_energy = cells.energy.astype(np.int32) + energy_per_cell
        new_energy = np.clip(new_energy, 0, 0xFFFF)
        cells.energy[:] = new_energy.astype(np.uint16)

    # Strain: apply delta with i8 clamp
    if buffers.strain_deltas.any():
        new_strain = cells.elastic_strain.astype(np.int32) + buffers.strain_deltas
        new_strain = np.clip(new_strain, -128, 127)
        cells.elastic_strain[:] = new_strain.astype(np.int8)

    # TODO: Tier 2 P<->U coupling when approaching u16 ceilings
    # TODO: Tier 3 refund + EXCLUDED on double saturation
