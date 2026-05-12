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
from typing import TYPE_CHECKING

import numpy as np

from .grid import HexGrid

if TYPE_CHECKING:
    pass


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
# These remain useful as scenario-side hex-arithmetic anchors (e.g. "Si solid
# at equilibrium has phase_mass = 74088") but the runtime code reads per-cell
# composition-weighted EQ via `compute_eq_phase`, not these globals.
EQUILIBRIUM_CENTER = {
    PHASE_SOLID:  74088.0,
    PHASE_LIQUID:  1764.0,
    PHASE_GAS:       42.0,
    PHASE_PLASMA:    42.0,    # plasma shares gas's density per gen5
}


# Universal kg quantum. Every hex unit of phase_mass IS 1 kg of physical
# mass — kg are the native simulation unit. A "fully saturated cell" of
# water liquid at the canonical 0.1-m cell size carries phase_mass[LIQUID]
# = 1000 × 0.1³ = 1.0 kg ≈ 1 hex unit; ice → 0.917; air → ~0.0012; Si
# solid → 2.329. Energy follows: 1 kg of water heated 1 K = 4184 J.
#
# Choosing Q_KG = 1.0 instead of the earlier Si-solid-anchored
# ~3.14e-8 kg/unit puts every per-cell quantity on a human-readable
# SI scale. The hex-arithmetic universals 42 / 1764 / 74088 no longer
# correspond to phase_mass scales — they were a useful Tier 0 mnemonic
# but are dropped from runtime semantics under gen5 kg-native physics.
Q_KG: float = 1.0   # 1 kg of matter per hex unit of phase_mass


def compute_phase_fraction_from_mass(
    cells: "CellArrays",
    element_table,
    world,
) -> np.ndarray:
    """Per-cell volumetric phase_fraction derived from phase_mass + per-phase
    densities. Replaces the old "set at init, shift proportionally during
    transitions" pattern which gave incorrect fractions when (a) solid and
    liquid have different densities (ice/water volume mismatch) or (b) the
    cell is under-saturated.

    Physical model:
      - solid and liquid are incompressible: each takes fixed volume per kg.
        frac_p = phase_mass[p] × Q_KG / (ρ_p × V_cell)
      - gas and plasma are compressible: they fill the *remaining* cell
        volume (1 − solid_frac − liquid_frac), proportional to mass.
      - Vacuum is the implicit complement when no compressible phase has
        mass (`Σ phase_fraction < 1`).

    For a fully-melted-ice cell: solid_mass converts 1:1 in hex units, but
    ρ_liquid > ρ_solid means the liquid occupies less volume than the ice
    did → vacuum opens (~9 % for water at the 0.01-m cell scale).
    """
    n = cells.n
    out = np.zeros((n, N_PHASES), dtype=np.float32)
    if n == 0:
        return out

    volume = float(world.cell_size_m) ** 3

    # Lazy import to avoid circular reference
    from .compounds import get_compound_properties

    elements_by_id = {el.element_id: el for el in element_table}

    compound_id = cells.compound_id
    pm = cells.phase_mass

    # Per-cell per-phase density (kg/m³) — same logic as compute_eq_phase
    # but vectorised across all four phases.
    density = np.zeros((n, N_PHASES), dtype=np.float32)
    untagged = (compound_id == 0)

    for cid_val in np.unique(compound_id):
        if cid_val == 0:
            continue
        props = get_compound_properties(int(cid_val))
        if props is None:
            continue
        mask = (compound_id == cid_val)
        density[mask, PHASE_SOLID]   = props.density_solid
        density[mask, PHASE_LIQUID]  = props.density_liquid
        density[mask, PHASE_GAS]     = props.density_gas
        density[mask, PHASE_PLASMA]  = props.density_gas

    if untagged.any():
        for slot in range(COMPOSITION_SLOTS):
            eids = cells.composition[:, slot, 0]
            fracs = cells.composition[:, slot, 1].astype(np.float32) / 255.0
            for eid in np.unique(eids):
                if eid == 0:
                    continue
                element = elements_by_id.get(int(eid))
                if element is None:
                    continue
                mask = (eids == eid) & untagged
                if not mask.any():
                    continue
                density[mask, PHASE_SOLID]   += element.density_solid    * fracs[mask]
                density[mask, PHASE_LIQUID]  += element.density_liquid   * fracs[mask]
                density[mask, PHASE_GAS]     += element.density_gas_stp  * fracs[mask]
                density[mask, PHASE_PLASMA]  += element.density_gas_stp  * fracs[mask]

    # solid + liquid: fixed-density volumetric
    solid_frac  = pm[:, PHASE_SOLID]  * np.float32(Q_KG) / np.maximum(density[:, PHASE_SOLID],  1e-12) / np.float32(volume)
    liquid_frac = pm[:, PHASE_LIQUID] * np.float32(Q_KG) / np.maximum(density[:, PHASE_LIQUID], 1e-12) / np.float32(volume)

    # Clamp the incompressible fractions: f32 accumulation can drift them
    # marginally above 1.0 for cells at exactly EQ.
    solid_frac  = np.minimum(solid_frac,  1.0)
    liquid_frac = np.minimum(np.maximum(0.0, liquid_frac), np.maximum(0.0, 1.0 - solid_frac))

    # gas + plasma fill remaining volume proportional to mass
    remaining = np.maximum(0.0, 1.0 - solid_frac - liquid_frac)
    gas_mass    = pm[:, PHASE_GAS]
    plasma_mass = pm[:, PHASE_PLASMA]
    total_gp = gas_mass + plasma_mass
    has_compressible = total_gp > 1e-12

    gas_frac    = np.zeros(n, dtype=np.float32)
    plasma_frac = np.zeros(n, dtype=np.float32)
    gas_frac[has_compressible]    = remaining[has_compressible] * gas_mass[has_compressible]    / total_gp[has_compressible]
    plasma_frac[has_compressible] = remaining[has_compressible] * plasma_mass[has_compressible] / total_gp[has_compressible]

    out[:, PHASE_SOLID]   = solid_frac
    out[:, PHASE_LIQUID]  = liquid_frac
    out[:, PHASE_GAS]     = gas_frac
    out[:, PHASE_PLASMA]  = plasma_frac
    return out


def compute_eq_phase(
    cells: "CellArrays",
    element_table,
    world,
    phase: int,
) -> np.ndarray:
    """Per-cell equilibrium centre for the given phase.

    Returns float32[N] where each entry is `density_phase × volume / Q_KG`,
    the hex-unit count a fully-saturated cell of this composition would hold
    in the given phase channel at equilibrium.

    For cells tagged with a compound id (set via `set_compound`), the
    per-phase density comes from the compound's calibrated `props` —
    matching real-molecule behaviour rather than the atomic blend
    (e.g., real H₂O liquid 1000 kg/m³ vs the 47%-H + 53%-O blend's
    662 kg/m³). Untagged cells fall back to composition-weighted
    per-element densities.
    """
    n = cells.n
    eq_arr = np.zeros(n, dtype=np.float32)
    if n == 0:
        return eq_arr

    volume = float(world.cell_size_m) ** 3

    # Lazy import to avoid circular imports (compounds.py imports from cell.py).
    from .compounds import get_compound_properties

    compound_id = cells.compound_id
    untagged = (compound_id == 0)

    # Compound-tagged cells: use compound density for the phase
    for cid_val in np.unique(compound_id):
        if cid_val == 0:
            continue
        props = get_compound_properties(int(cid_val))
        if props is None:
            continue
        if phase == PHASE_SOLID:
            d = props.density_solid
        elif phase == PHASE_LIQUID:
            d = props.density_liquid
        elif phase == PHASE_GAS or phase == PHASE_PLASMA:
            d = props.density_gas
        else:
            d = 0.0
        mask = (compound_id == cid_val)
        eq_arr[mask] = np.float32(d * volume / Q_KG)

    # Untagged cells: composition-weighted per-element densities
    if untagged.any():
        elements_by_id = {el.element_id: el for el in element_table}
        for slot in range(COMPOSITION_SLOTS):
            eids = cells.composition[:, slot, 0]
            fracs = cells.composition[:, slot, 1].astype(np.float32) / 255.0
            for eid in np.unique(eids):
                if eid == 0:
                    continue
                element = elements_by_id.get(int(eid))
                if element is None:
                    continue
                if phase == PHASE_SOLID:
                    d = element.density_solid
                elif phase == PHASE_LIQUID:
                    d = element.density_liquid
                elif phase == PHASE_GAS or phase == PHASE_PLASMA:
                    d = element.density_gas_stp
                else:
                    d = 0.0
                mask = (eids == eid) & untagged
                if not mask.any():
                    continue
                eq_arr[mask] += np.float32(d * volume / Q_KG) * fracs[mask]
    return eq_arr

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

    # Compound id (M6'.x calibration). When non-zero, indicates the cell was
    # initialised via `set_compound` and should use the compound's calibrated
    # per-phase density / specific heat / latent heats instead of an atomic
    # blend over composition slots. Compound-aware paths exist in derive
    # (compute_thermal_blends, compute_eq_phase) and transitions
    # (_latent_heat_per_kg). compound_id == 0 = atomic-blend behaviour.
    compound_id: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.uint8))

    # Sub-quantum energy residual (M5'.7c). At low cell mass × low conductivity
    # (water cells especially), per-sub-pass conduction ΔE can be far below
    # one u16 log-encoded quantum (~0.1 J at E ≈ 1 kJ). Encoding such a
    # tiny delta rounds to zero and heat refuses to flow. This f32 residual
    # accumulates the sub-quantum part of every integration; when it crosses
    # one quantum it flips the raw value. Working state only — not emitted.
    energy_residual: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))

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
            compound_id=np.zeros(n, dtype=np.uint8),
            energy_residual=np.zeros(n, dtype=np.float32),
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

def compute_identity(
    cells: CellArrays,
    element_table=None,
    world=None,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell (majority_phase, majority_element) computed from current
    state. Phase wins by fraction-of-equilibrium-saturation, not raw mass.
    Element wins by composition fraction within the majority-phase context.

    When `element_table` and `world` are provided, saturation uses per-cell
    equilibrium centres derived from each cell's composition × per-element
    densities (gen5 phase_mass↔kg semantics). When None (legacy/cheap
    callers), saturation uses the universal hex anchors 42/1764/74088 —
    correct for Si-anchored scenarios but inaccurate for compound or
    non-Si elements where the per-cell EQ differs from universal.

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

    if element_table is not None and world is not None:
        # Per-cell EQ for each phase
        eq_per_phase = np.zeros((n, N_PHASES), dtype=np.float32)
        for p in range(N_PHASES):
            eq_per_phase[:, p] = compute_eq_phase(cells, element_table, world, p)
        saturation = cells.phase_mass / np.maximum(eq_per_phase, 1e-12)
    else:
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
