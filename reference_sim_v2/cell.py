"""
Cell storage v2 — gen5 SoA layout.

This is the canonical per-cell stored state for the gen5 reference simulator.
Layout follows verdant_sim_design.md §"State representation":

  - 16-slot composition vector (vs Tier 0's 4)
  - 4-channel phase distribution (solid+liquid+gas+plasma fractions; vacuum implicit)
  - per-phase mass content tracked independently
  - log-scale u16 pressure encoding (deviation from phase-density equilibrium center)
  - u16 energy encoding (decoded to f32 at cycle entry, re-encoded at cycle exit)
  - u8 mohs_level (single dominant-solid component for now; per gen5 shelved Q)
  - f32 sustained_overpressure integrator (replaces u8 cycles_above_threshold)
  - 6 petals per cell with persistent directional state (stress, velocity, topology)
  - u8 flags (subset of Tier 0 flags; CULLED/RATCHETED/EXCLUDED retire under gen5
    in favor of the noise-floor culling mechanism — they remain only as scenario-
    level diagnostic markers)

Working state during a cycle uses f32 throughout via decode helpers; canonical
storage stays packed for cross-validation against the eventual CUDA port.

See gen5_implementation_spec.md and gen5_roadmap.md §3.1 for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .grid import HexGrid


# Composition slots — gen5 commitment (verdant_sim_design.md §"Per-cell state")
COMPOSITION_SLOTS = 16

# Phase channels — gen5 commits to four named phases. Vacuum is implicit
# (1.0 - sum(phase_fractions)). Index assignments are CANONICAL — emit/verify
# both depend on this ordering.
PHASE_SOLID = 0
PHASE_LIQUID = 1
PHASE_GAS = 2
PHASE_PLASMA = 3
N_PHASES = 4

PHASE_NAMES = {
    PHASE_SOLID:  "solid",
    PHASE_LIQUID: "liquid",
    PHASE_GAS:    "gas",
    PHASE_PLASMA: "plasma",
}
PHASE_FROM_NAME = {v: k for k, v in PHASE_NAMES.items()}

# Phase-density equilibrium centers (gen5 §"Phases and density equilibrium centers").
# Hex-arithmetic universals: 42 = 6×7, 1764 = 42², 74088 = 42³.
# Per-element scaling factors multiply these at scenario init (gen5 §"Per-element
# density scaling"); for M5'.0 we use the bare values as stubs.
EQUILIBRIUM_CENTER = {
    PHASE_SOLID:  74088.0,
    PHASE_LIQUID:  1764.0,
    PHASE_GAS:       42.0,
    PHASE_PLASMA:    42.0,    # plasma shares gas's density per gen5
}

# Petal directions are the same six as the grid neighbour ordering. Each cell
# has one petal per direction holding persistent per-edge state.
N_PETAL_DIRS = 6

# Petal topology bit-packing (per cell, per direction)
PETAL_TOPO_IS_BORDER     = 1 << 0
PETAL_TOPO_IS_GRID_EDGE  = 1 << 1
PETAL_TOPO_IS_INERT      = 1 << 2
# Bits 3-7 reserve 5 bits for a border_type_index (0..31) — sufficient for the
# border-properties table size we expect.
PETAL_TOPO_BORDER_TYPE_SHIFT = 3
PETAL_TOPO_BORDER_TYPE_MASK  = 0b11111000


@dataclass
class CellArrays:
    """Gen5 SoA cell storage. Each field is a numpy array of length N (or
    shaped accordingly). Field dtypes are chosen so the in-memory layout
    matches what the eventual CUDA port stores in VRAM, enabling bit-for-bit
    cross-validation through the schema-v2 JSON contract.
    """

    grid: HexGrid

    # ---- canonical stored state ----------------------------------------

    # Composition: int16[N, 16, 2] — (element_id, fraction). Sum of fractions
    # across the 16 slots must equal 255 for any non-void cell. int16 storage
    # allows signed deltas during multi-element migration without overflow;
    # re-clamped to [0, 255] at integration boundaries.
    composition: np.ndarray = field(default_factory=lambda: np.zeros((0, COMPOSITION_SLOTS, 2), dtype=np.int16))

    # Phase distribution: float32[N, 4]. Each fraction in [0, 1]; sum across
    # the four channels must be ≤ 1.0; vacuum_fraction = 1 - sum.
    phase_fraction: np.ndarray = field(default_factory=lambda: np.zeros((0, N_PHASES), dtype=np.float32))

    # Per-phase mass content in gen5's hex-arithmetic mass units. The quantity
    # each phase fraction seeks to hold near its equilibrium center.
    phase_mass: np.ndarray = field(default_factory=lambda: np.zeros((0, N_PHASES), dtype=np.float32))

    # Pressure as deviation from phase-density equilibrium center, log-scale
    # u16 encoded. Decoded to f32 at cycle entry, re-encoded at cycle exit.
    pressure_raw: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint16))

    # Energy: u16 encoded internal energy. Temperature is derived from
    # (energy_raw, composition, phase_fraction).
    energy_raw: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint16))

    # Mohs level (1..10 for solid-dominant cells; 0 for non-solid).
    mohs_level: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))

    # Sustained-overpressure integrator (gen5 §"Mohs ratcheting"). f32 so it
    # can hold both magnitude and a fractional decay state; replaces Tier 0's
    # u8 cycles_above_threshold counter.
    sustained_overpressure: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))

    # ---- petal data (persistent per-cell-per-direction) ----------------

    petal_stress: np.ndarray   = field(default_factory=lambda: np.zeros((0, N_PETAL_DIRS), dtype=np.float32))
    petal_velocity: np.ndarray = field(default_factory=lambda: np.zeros((0, N_PETAL_DIRS, 2), dtype=np.float32))
    # Topology bits cached on first contact; never re-validated at runtime.
    petal_topology: np.ndarray = field(default_factory=lambda: np.zeros((0, N_PETAL_DIRS), dtype=np.uint8))

    # ---- per-cell flags -----------------------------------------------

    # u8 flags. The persistent four (NO_FLOW / RADIATES / INSULATED /
    # FIXED_STATE) survive from Tier 0; CULLED/RATCHETED/FRACTURED/EXCLUDED
    # exist as diagnostic markers but Tail-at-Scale culling is the new
    # primary mechanism (see gen5 §"Tail at Scale: straggler culling").
    flags: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))

    @classmethod
    def empty(cls, grid: HexGrid) -> "CellArrays":
        """Allocate zeroed arrays for every field. All cells start as void."""
        n = grid.cell_count
        return cls(
            grid=grid,
            composition=np.zeros((n, COMPOSITION_SLOTS, 2), dtype=np.int16),
            phase_fraction=np.zeros((n, N_PHASES), dtype=np.float32),
            phase_mass=np.zeros((n, N_PHASES), dtype=np.float32),
            pressure_raw=np.zeros(n, dtype=np.uint16),
            energy_raw=np.zeros(n, dtype=np.uint16),
            mohs_level=np.zeros(n, dtype=np.uint8),
            sustained_overpressure=np.zeros(n, dtype=np.float32),
            petal_stress=np.zeros((n, N_PETAL_DIRS), dtype=np.float32),
            petal_velocity=np.zeros((n, N_PETAL_DIRS, 2), dtype=np.float32),
            petal_topology=np.zeros((n, N_PETAL_DIRS), dtype=np.uint8),
            flags=np.zeros(n, dtype=np.uint8),
        )

    def __len__(self) -> int:
        return self.grid.cell_count

    @property
    def n(self) -> int:
        return self.grid.cell_count


# --------------------------------------------------------------------------
# Composition helpers
# --------------------------------------------------------------------------

def composition_sum(cells: CellArrays) -> np.ndarray:
    """Per-cell sum of composition fractions across the 16 slots. Should
    equal 255 for every non-void cell."""
    return cells.composition[:, :, 1].sum(axis=1).astype(np.int32)


def set_single_element(
    cells: CellArrays,
    cell_id: int,
    element_id: int,
    fraction: int = 255,
) -> None:
    """Fill a cell's composition with a single element at the given fraction,
    zeroing the other 15 slots. Convenience for single-element scenarios."""
    cells.composition[cell_id, 0, 0] = element_id
    cells.composition[cell_id, 0, 1] = fraction
    cells.composition[cell_id, 1:, :] = 0


def composition_pairs(cells: CellArrays, cell_id: int, id_to_symbol=None) -> list:
    """Emit a single cell's composition as the schema-v2 JSON form:
    [[symbol, fraction], ...] with (0, 0) placeholder slots dropped."""
    out = []
    for slot in range(COMPOSITION_SLOTS):
        eid = int(cells.composition[cell_id, slot, 0])
        frac = int(cells.composition[cell_id, slot, 1])
        if eid == 0 and frac == 0:
            continue
        key = id_to_symbol.get(eid, str(eid)) if id_to_symbol is not None else str(eid)
        out.append([key, frac])
    return out


# --------------------------------------------------------------------------
# Identity (computed, not stored — gen5 §"Cell identity is computed, not
# stored"). Per D5 we use majority-by-fraction-of-equilibrium so under-dense
# phases register as displacement candidates rather than stealing identity.
# --------------------------------------------------------------------------

def compute_identity(cells: CellArrays) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell (majority_phase, majority_element) computed from current
    state. Phase wins by fraction-of-equilibrium-saturation, not raw mass.
    Element wins by composition fraction within the majority-phase context.

    Returns:
        majority_phase: uint8[N], values in {0..3, 255 for void}
        majority_element: uint8[N], element_id of the majority composition
                          slot, 0 for void
    """
    n = cells.n
    majority_phase = np.full(n, 255, dtype=np.uint8)  # 255 = void sentinel
    majority_element = np.zeros(n, dtype=np.uint8)

    if n == 0:
        return majority_phase, majority_element

    # Saturation per phase = phase_mass / equilibrium_center (per phase)
    centers = np.array(
        [EQUILIBRIUM_CENTER[p] for p in range(N_PHASES)],
        dtype=np.float32,
    )
    saturation = cells.phase_mass / np.maximum(centers, 1e-12)   # (N, 4)

    nonzero = (saturation.sum(axis=1) > 0)
    majority_phase[nonzero] = saturation[nonzero].argmax(axis=1).astype(np.uint8)

    # Majority element = composition slot with the largest fraction
    fracs = cells.composition[:, :, 1]
    has_comp = (fracs.sum(axis=1) > 0)
    if has_comp.any():
        slot_idx = fracs.argmax(axis=1)
        majority_element[has_comp] = cells.composition[has_comp, slot_idx[has_comp], 0].astype(np.uint8)

    return majority_phase, majority_element
