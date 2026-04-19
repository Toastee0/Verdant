"""
Cell storage — the stored state arrays for every cell on the grid.

SoA (struct-of-arrays) layout: each field is its own numpy array of length N.
This matches the GPU-friendly memory layout we'll port to. AoS (dict per cell)
form is only used when emitting JSON.

See `wiki/cell-struct.md` for the authoritative field list.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field

from .grid import HexGrid


# Phase identifiers, matching the 2-bit phase field in the packed struct.
# Also matches the loader constants in element_table.py.
PHASE_SOLID = 0
PHASE_LIQUID = 1
PHASE_GAS = 2
PHASE_PLASMA = 3

PHASE_NAMES = {
    PHASE_SOLID: "solid",
    PHASE_LIQUID: "liquid",
    PHASE_GAS: "gas",
    PHASE_PLASMA: "plasma",
}
PHASE_FROM_NAME = {v: k for k, v in PHASE_NAMES.items()}


# Composition slot count — see `wiki/cell-struct.md` (4 slots by default).
COMPOSITION_SLOTS = 4


@dataclass
class CellArrays:
    """All per-cell stored state for the simulation, one numpy array per field.

    Arrays are length N (= grid.cell_count). Dtypes match the packed C struct
    we'll eventually port to, so the Python reference can diff bitwise against
    the CUDA output.
    """

    # Grid binding (not "stored" in the struct sense, but used by everything)
    grid: HexGrid

    # Stored fields — these are the things persisted tick-to-tick.
    # Composition: int16[N, 4, 2]  --  (N cells, 4 slots, [element_id, fraction])
    # element_id is u8, fraction is u8, but stored as int16 to allow signed
    # arithmetic during flow passes without overflow. Re-clamped at reconcile.
    composition: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16))

    phase: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))
    mohs_level: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))
    flags: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))
    pressure_raw: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint16))
    energy: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint16))
    elastic_strain: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))
    magnetization: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))

    @classmethod
    def empty(cls, grid: HexGrid) -> "CellArrays":
        """Allocate zeroed arrays for every field. All cells start as void
        (composition slots all zero, phase solid, mohs 0, flags 0)."""
        n = grid.cell_count
        return cls(
            grid=grid,
            composition=np.zeros((n, COMPOSITION_SLOTS, 2), dtype=np.int16),
            phase=np.zeros(n, dtype=np.uint8),
            mohs_level=np.zeros(n, dtype=np.uint8),
            flags=np.zeros(n, dtype=np.uint8),
            pressure_raw=np.zeros(n, dtype=np.uint16),
            energy=np.zeros(n, dtype=np.uint16),
            elastic_strain=np.zeros(n, dtype=np.int8),
            magnetization=np.zeros(n, dtype=np.int8),
        )

    def __len__(self) -> int:
        return self.grid.cell_count

    @property
    def n(self) -> int:
        return self.grid.cell_count


def composition_sum(cells: CellArrays) -> np.ndarray:
    """Per-cell sum of composition fractions. Should equal 255 for every
    non-void cell (composition_sum_255 invariant)."""
    return cells.composition[:, :, 1].sum(axis=1).astype(np.int32)


def composition_as_list(cells: CellArrays, cell_id: int, id_to_symbol=None) -> list:
    """Emit a single cell's composition in the schema-v1 JSON form:
    [[symbol, fraction], ...], dropping (0, 0) trailing slots.
    """
    slots = cells.composition[cell_id]   # shape (4, 2)
    out = []
    for element_id, frac in slots:
        if element_id == 0 and frac == 0:
            continue
        if id_to_symbol is not None:
            key = id_to_symbol.get(int(element_id), str(int(element_id)))
        else:
            key = str(int(element_id))
        out.append([key, int(frac)])
    return out


def set_single_element(cells: CellArrays, cell_id: int, element_id: int, fraction: int = 255) -> None:
    """Fill a cell's composition with a single element at the given fraction,
    padding other slots with (0, 0). Convenience for Tier 0 scenarios."""
    cells.composition[cell_id, 0, 0] = element_id
    cells.composition[cell_id, 0, 1] = fraction
    cells.composition[cell_id, 1:, :] = 0
