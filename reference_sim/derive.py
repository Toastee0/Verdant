"""
Stage 0 — Derive phase.

Compute all derived per-cell fields from stored state. No state change.
Produces scratch buffers consumed by Stage 1 onward.

Sub-stages (see wiki/pipeline.md):
    0a  Φ  gravitational potential  (Poisson via Jacobi)
    0b  cohesion topology
    0c  T  temperature
    0d  B  magnetic field  (scenario-gated)
    0e  μ  chemical potential per (cell, element)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .cell import CellArrays, COMPOSITION_SLOTS, PHASE_SOLID, PHASE_LIQUID, PHASE_GAS, PHASE_PLASMA
from .element_table import ElementTable
from .flags import FRACTURED, EXCLUDED
from .grid import HexGrid, NEIGHBOR_DELTAS, OPPOSITE_DIRECTION
from .scenario import WorldConfig


@dataclass
class DerivedFields:
    """Per-tick scratch: fields recomputed in Stage 0, consumed by 1/2/3/4."""
    phi: np.ndarray                    # float32[N] gravitational potential
    temperature: np.ndarray            # float32[N] K
    b_field: np.ndarray                # float32[N, 2] magnetic flux density (or zeros)
    mu: np.ndarray                     # float32[N, COMPOSITION_SLOTS] chemical potential per element slot
    cohesion: np.ndarray               # bool[N, 6] per-bond cohesion (symmetric)

    @classmethod
    def allocate(cls, grid: HexGrid) -> "DerivedFields":
        n = grid.cell_count
        return cls(
            phi=np.zeros(n, dtype=np.float32),
            temperature=np.zeros(n, dtype=np.float32),
            b_field=np.zeros((n, 2), dtype=np.float32),
            mu=np.zeros((n, COMPOSITION_SLOTS), dtype=np.float32),
            cohesion=np.zeros((n, 6), dtype=bool),
        )


# --------------------------------------------------------------------------
# Stage 0a  --  Gravitational potential Φ
# --------------------------------------------------------------------------

def stage_0a_gravity(
    cells: CellArrays,
    world: WorldConfig,
    element_table: ElementTable,
    derived: DerivedFields,
) -> None:
    """Solve ∇²Φ = 4πG·ρ via Jacobi iteration on the hex grid.

    For world.g_sim == 0, Φ stays zero (trivial skip). For non-zero G, iterate
    until max delta < convergence_threshold or max_iters reached.
    """
    if world.g_sim == 0.0:
        derived.phi.fill(0.0)
        return

    # Compute per-cell mass density (kg/m³) from composition weights.
    # For Tier 0 this is dominated by density_solid of the single element.
    density = _compute_density(cells, element_table)
    # Source term: 4πG·ρ·h²·(3/2)   [hex Laplacian constant]
    h = world.cell_size_m
    hex_lap_const = 1.5
    source = 4.0 * np.pi * world.g_sim * density * (h * h) * hex_lap_const

    grid = cells.grid
    # Jacobi: Φ_new[c] = (Σ Φ_old[neighbors] - source[c]) / 6
    max_iters = 500
    tol = world.convergence_threshold
    phi_old = derived.phi
    phi_new = np.zeros_like(phi_old)
    neighbors = np.array(grid.neighbors, dtype=np.int32)  # shape (N, 6)
    for _ in range(max_iters):
        # Boundary neighbors (-1) → treat as Φ = 0 (Dirichlet BC at bottle edge).
        phi_padded = np.concatenate([phi_old, [0.0]])  # index -1 hits the 0-padding
        neighbor_phi_sum = phi_padded[neighbors].sum(axis=1)
        phi_new[:] = (neighbor_phi_sum - source) / 6.0
        delta = np.abs(phi_new - phi_old).max()
        phi_old, phi_new = phi_new, phi_old
        if delta < tol:
            break
    derived.phi = phi_old


def _compute_density(cells: CellArrays, element_table: ElementTable) -> np.ndarray:
    """Per-cell mass density [kg/m³], composition-weighted by element density
    at the cell's current phase."""
    n = cells.n
    density = np.zeros(n, dtype=np.float32)
    for slot in range(COMPOSITION_SLOTS):
        eid = cells.composition[:, slot, 0]
        frac = cells.composition[:, slot, 1].astype(np.float32) / 255.0
        for element in element_table:
            mask = (eid == element.element_id) & (frac > 0)
            if not mask.any():
                continue
            phase_density = _phase_density(element, cells.phase[mask])
            density[mask] += phase_density * frac[mask]
    return density


def _phase_density(element, phases: np.ndarray) -> np.ndarray:
    """Look up density per cell based on phase array."""
    out = np.empty(phases.shape, dtype=np.float32)
    out[phases == PHASE_SOLID]  = element.density_solid
    out[phases == PHASE_LIQUID] = element.density_liquid
    out[phases == PHASE_GAS]    = element.density_gas_stp
    out[phases == PHASE_PLASMA] = element.density_gas_stp  # placeholder until plasma lands
    return out


# --------------------------------------------------------------------------
# Stage 0b  --  Cohesion topology
# --------------------------------------------------------------------------

def stage_0b_cohesion(cells: CellArrays, derived: DerivedFields) -> None:
    """Two cells are cohesively bonded iff:
       - both are solid
       - same dominant element
       - neither is FRACTURED, EXCLUDED, or FIXED_STATE
    """
    grid = cells.grid
    n = grid.cell_count
    cohesion = derived.cohesion
    cohesion.fill(False)

    # Pre-compute per-cell eligibility: solid AND not fractured/excluded
    solid = (cells.phase == PHASE_SOLID)
    broken = ((cells.flags & (FRACTURED | EXCLUDED)) != 0)
    eligible = solid & ~broken
    # Dominant element is the first non-zero composition slot (by convention,
    # scenarios store the dominant element in slot 0)
    dominant_element = cells.composition[:, 0, 0]  # int16[N]

    neighbors = np.array(grid.neighbors, dtype=np.int32)  # (N, 6)
    for direction in range(6):
        nids = neighbors[:, direction]
        valid = (nids != -1)
        if not valid.any():
            continue
        # cell i bonds in `direction` to cell nids[i] iff both eligible and
        # same dominant element
        nid_safe = np.where(valid, nids, 0)  # safe for indexing
        bond = (
            valid
            & eligible
            & eligible[nid_safe]
            & (dominant_element == dominant_element[nid_safe])
        )
        cohesion[:, direction] = bond


# --------------------------------------------------------------------------
# Stage 0c  --  Temperature T
# --------------------------------------------------------------------------

def stage_0c_temperature(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    world: WorldConfig,
) -> None:
    """Per-cell temperature from energy + composition + phase.

    Simple model for Tier 0:  T = energy / (mass × c_p(composition, phase))
    where `mass` is taken as density × cell_volume (scenario's cell size).
    """
    n = cells.n
    volume = world.cell_size_m ** 3
    density = _compute_density(cells, element_table)
    mass = density * volume   # kg per cell

    # Composition-weighted specific heat
    cp = np.zeros(n, dtype=np.float32)
    for slot in range(COMPOSITION_SLOTS):
        eid = cells.composition[:, slot, 0]
        frac = cells.composition[:, slot, 1].astype(np.float32) / 255.0
        for element in element_table:
            mask = (eid == element.element_id) & (frac > 0)
            if not mask.any():
                continue
            cp_per_phase = _phase_specific_heat(element, cells.phase[mask])
            cp[mask] += cp_per_phase * frac[mask]

    energy_j = cells.energy.astype(np.float32) * _infer_energy_scale(cells, element_table)
    # Guard against zero mass or zero cp (empty / void cells)
    denom = np.maximum(mass * cp, 1e-12)
    # Reference T offset: we treat stored energy as thermal content above 0 K.
    # For Tier 0 this is good enough; later we'll fold in phase-reference offsets.
    derived.temperature = energy_j / denom


def _phase_specific_heat(element, phases: np.ndarray) -> np.ndarray:
    out = np.empty(phases.shape, dtype=np.float32)
    out[phases == PHASE_SOLID]  = element.specific_heat_solid
    out[phases == PHASE_LIQUID] = element.specific_heat_liquid
    out[phases == PHASE_GAS]    = element.specific_heat_gas
    out[phases == PHASE_PLASMA] = element.specific_heat_gas
    return out


def _infer_energy_scale(cells: CellArrays, element_table: ElementTable) -> float:
    """Global scenario energy_scale is taken from the dominant element of
    cell 0. Works for Tier 0 (single element); Tier 1+ will need per-cell
    scaling. TODO."""
    eid = int(cells.composition[0, 0, 0])
    element = element_table.by_id.get(eid)
    if element is None:
        return 1.0
    return element.energy_scale


# --------------------------------------------------------------------------
# Stage 0d  --  Magnetic field B
# --------------------------------------------------------------------------

def stage_0d_magnetism(
    cells: CellArrays,
    world: WorldConfig,
    element_table: ElementTable,
    derived: DerivedFields,
) -> None:
    """Compute B field from magnetization distribution. Skipped entirely when
    world.magnetism_enabled is False."""
    if not world.magnetism_enabled:
        derived.b_field.fill(0.0)
        return
    # TODO: Poisson-like Jacobi solve on magnetization, or direct sum for small
    # grids. Deferred until Tier 2+ needs it.
    derived.b_field.fill(0.0)


# --------------------------------------------------------------------------
# Stage 0e  --  Chemical potential μ
# --------------------------------------------------------------------------

def stage_0e_chemical_potential(
    cells: CellArrays,
    element_table: ElementTable,
    derived: DerivedFields,
    world: WorldConfig,
) -> None:
    """Composition potential per (cell, element_slot). Written into
    derived.mu for Stage 3 to consume.

    μ = decoded_P + ρ_element × Φ + concentration_term + cohesion_barrier + magnetic_term

    Tier 0 keeps this minimal: pressure + gravity only. Solubility, cohesion
    barrier, and magnetism are included in the framework but return 0 for
    Tier 0 scenarios.
    """
    n = cells.n
    derived.mu.fill(0.0)

    # Decode pressure to Pa (phase-dependent)
    decoded_p = _decode_pressure_all(cells, element_table)

    for slot in range(COMPOSITION_SLOTS):
        eid = cells.composition[:, slot, 0]
        frac = cells.composition[:, slot, 1]
        active = (frac > 0)
        if not active.any():
            continue

        # Term 1: pressure contribution — same for every element in the cell
        derived.mu[active, slot] += decoded_p[active]

        # Term 2: gravity contribution (ρ_element × Φ). For Tier 0, Φ = 0 so
        # this term is zero, but the code path is here.
        for element in element_table:
            mask = active & (eid == element.element_id)
            if not mask.any():
                continue
            phase_density = _phase_density(element, cells.phase[mask])
            derived.mu[mask, slot] += phase_density * derived.phi[mask]

        # Term 3 (concentration / solubility) — Tier 0 single element; no contribution.
        # Term 4 (cohesion barrier) — applied at bond-evaluation time in Stage 3,
        # not baked into μ itself (depends on direction).
        # Term 5 (magnetic) — derived.b_field is zero when magnetism disabled.


def _decode_pressure_all(cells: CellArrays, element_table: ElementTable) -> np.ndarray:
    """Vectorised decode of pressure_raw for every cell in SI Pa."""
    n = cells.n
    out = np.zeros(n, dtype=np.float32)
    for element in element_table:
        eid = element.element_id
        mask = (cells.composition[:, 0, 0] == eid)
        if not mask.any():
            continue
        mantissa = cells.pressure_raw[mask].astype(np.float32)
        phase = cells.phase[mask]
        # Gas
        g = (phase == PHASE_GAS)
        if g.any():
            sub_idx = np.where(mask)[0][g]
            out[sub_idx] = mantissa[g] * element.pressure_mantissa_scale_gas
        # Liquid
        l = (phase == PHASE_LIQUID)
        if l.any():
            sub_idx = np.where(mask)[0][l]
            out[sub_idx] = mantissa[l] * element.pressure_mantissa_scale_liquid
        # Solid (mohs-dependent)
        s = (phase == PHASE_SOLID)
        if s.any():
            sub_idx = np.where(mask)[0][s]
            mohs = cells.mohs_level[sub_idx].astype(np.float32)
            scale = element.pressure_mantissa_scale_solid * (element.mohs_multiplier ** (mohs - 1))
            out[sub_idx] = mantissa[s] * scale
        # Plasma
        p = (phase == PHASE_PLASMA)
        if p.any():
            sub_idx = np.where(mask)[0][p]
            out[sub_idx] = mantissa[p] * element.pressure_mantissa_scale_gas * 64
    return out


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------

def run_derive_stage(
    cells: CellArrays,
    element_table: ElementTable,
    world: WorldConfig,
    derived: DerivedFields,
) -> None:
    """Run all Stage 0 sub-stages in order."""
    stage_0a_gravity(cells, world, element_table, derived)
    stage_0b_cohesion(cells, derived)
    stage_0c_temperature(cells, element_table, derived, world)
    stage_0d_magnetism(cells, world, element_table, derived)
    stage_0e_chemical_potential(cells, element_table, derived, world)
