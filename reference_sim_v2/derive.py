"""
Derive — gen5 per-cycle scratch computation.

Computes f32 working state from canonical packed state at the top of each
cycle (and refreshed each sub-pass when needed). Per gen5:

  - identity (majority phase + element) computed every cycle from the
    phase-fraction × equilibrium-center saturation. Per D5 / user
    confirmation: identity-flip *is* the displacement/nucleation event.
  - cohesion is a transient per-cell-per-direction f32 damping coefficient,
    blind (cell reads its own composition + neighbor's canonical composition,
    does NOT consult the neighbor's cohesion). Asymmetric behavior emerges
    from the blind sum across edges.
  - temperature derived from energy + composition + phase distribution.
  - pressure decoded from canonical u16 → f32 deviation-from-equilibrium-center.

Cohesion formula (gen5 §"Cohesion"):

    cohesion(self, dir) = f(shared_majority_match(self.comp, neighbor.comp))
                        × g(self.purity)

For M5'.1 we use:
    f(match) = 1.0 if same majority element, else 0.0
    g(purity) = max_fraction / 255

This produces cohesion=1.0 inside same-material regions, drops to 0 across
material boundaries (surface tension), and 0 at grid edges (no neighbor).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .cell import (
    COMPOSITION_SLOTS,
    CellArrays,
    EQUILIBRIUM_CENTER,
    N_PETAL_DIRS,
    N_PHASES,
    PHASE_GAS,
    PHASE_LIQUID,
    PHASE_PLASMA,
    PHASE_SOLID,
    compute_identity,
)

if TYPE_CHECKING:
    from .scenario import WorldConfig


@dataclass
class DerivedFields:
    """Per-cycle scratch buffers — recomputed at the top of each cycle (and
    each sub-pass when fluxes are flowing). Allocated once at scenario
    bring-up and reused; all fields are float32 except identity arrays."""

    # Identity (computed-not-stored, per gen5 §"Cell identity is computed,
    # not stored")
    majority_phase: np.ndarray   # uint8[N]; 255 = void
    majority_element: np.ndarray # uint8[N]; 0 = void

    # Cohesion: per-cell-per-direction blind damping coefficient ∈ [0, 1]
    cohesion: np.ndarray         # float32[N, 6]

    # Temperature: per-cell f32 K
    temperature: np.ndarray      # float32[N]

    # Decoded pressure (deviation from phase-density equilibrium center)
    pressure: np.ndarray         # float32[N]; signed, in arbitrary units for M5'.1 (true log-scale lands M5'.5)

    # Gravity vector field — populated at M5'.2; zero for M5'.1
    gravity_vec: np.ndarray      # float32[N, 2]

    @classmethod
    def allocate(cls, n: int) -> "DerivedFields":
        return cls(
            majority_phase=np.full(n, 255, dtype=np.uint8),
            majority_element=np.zeros(n, dtype=np.uint8),
            cohesion=np.zeros((n, N_PETAL_DIRS), dtype=np.float32),
            temperature=np.zeros(n, dtype=np.float32),
            pressure=np.zeros(n, dtype=np.float32),
            gravity_vec=np.zeros((n, 2), dtype=np.float32),
        )


# --------------------------------------------------------------------------
# Pressure decode (M5'.1 stub — true signed log-scale lands at M5'.5)
# --------------------------------------------------------------------------

def decode_pressure_to_f32(cells: CellArrays) -> np.ndarray:
    """Decode u16 pressure_raw → f32 deviation from equilibrium center.

    M5'.1 STUB: returns raw cast to f32 (no scaling). Real log-scale signed
    encoding lands at M5'.5 when pressure dynamics need to operate in
    physical units. The convention is unchanged:
      - raw == 0 ⇒ deviation == 0 (cell at equilibrium)
      - positive raw ⇒ positive deviation (mass wants to flow out)

    Until M5'.5, scenarios should set pressure_raw=0 to mean equilibrium.
    """
    return cells.pressure_raw.astype(np.float32)


# --------------------------------------------------------------------------
# Temperature
# --------------------------------------------------------------------------

def compute_temperature(
    cells: CellArrays,
    element_table,
    world: "WorldConfig",
) -> np.ndarray:
    """Per-cell temperature in K from energy_raw + composition + phase
    distribution.

    T = energy_J / (mass_kg × c_p_weighted)

    where mass_kg comes from a composition-and-phase-fraction-weighted
    density, and c_p_weighted is the analogous specific-heat blend. This
    extends the Tier 0 formula to gen5's fractional-phase model: a
    half-liquid-half-solid cell takes the average of liquid and solid c_p
    weighted by the phase fractions.

    For void cells (no composition) returns 0.
    """
    n = cells.n
    volume = float(world.cell_size_m ** 3)

    # Build per-cell density (kg/m³) and c_p (J/(kg·K))
    density = np.zeros(n, dtype=np.float32)
    cp = np.zeros(n, dtype=np.float32)
    for slot in range(COMPOSITION_SLOTS):
        eid = cells.composition[:, slot, 0]
        frac = cells.composition[:, slot, 1].astype(np.float32) / 255.0
        if not (frac > 0).any():
            continue
        for element in element_table:
            mask = (eid == element.element_id) & (frac > 0)
            if not mask.any():
                continue
            # Phase-fraction-weighted density and c_p for THIS element
            d_per_phase = np.array(
                [element.density_solid, element.density_liquid,
                 element.density_gas_stp, element.density_gas_stp],
                dtype=np.float32,
            )
            cp_per_phase = np.array(
                [element.specific_heat_solid, element.specific_heat_liquid,
                 element.specific_heat_gas, element.specific_heat_gas],
                dtype=np.float32,
            )
            d_blend = (cells.phase_fraction[mask] * d_per_phase).sum(axis=1)
            cp_blend = (cells.phase_fraction[mask] * cp_per_phase).sum(axis=1)
            density[mask] += d_blend * frac[mask]
            cp[mask]      += cp_blend * frac[mask]

    mass_kg = density * volume
    # Energy in joules. Per D6, working state is f32 throughout the cycle;
    # u16 → f32 round-trip happens here (decode) and at integration
    # boundary (encode). For M5'.1 the energy_scale is just element_table[0].energy_scale.
    first = next(iter(element_table))
    energy_j = cells.energy_raw.astype(np.float32) * float(first.energy_scale)

    denom = np.maximum(mass_kg * cp, 1e-12)
    T = energy_j / denom
    # Void cells have mass_kg = 0 → denom is the floor; clamp T to 0 for
    # those (they have no thermal content meaningfully).
    void = (mass_kg == 0)
    T[void] = 0.0
    return T.astype(np.float32)


# --------------------------------------------------------------------------
# Cohesion — blind, per-cell-per-direction f32 damping
# --------------------------------------------------------------------------

def compute_cohesion(
    cells: CellArrays,
    derived: DerivedFields,
) -> np.ndarray:
    """Per-cell-per-direction cohesion ∈ [0, 1].

    Gen5 §"Cohesion (per-cell, per-direction damping)":
        cohesion(self, dir) = f(shared_majority_match(self.comp, neighbor.comp))
                            × g(self.purity)

    M5'.1 implementation:
        f(match) = 1.0 if same majority_element, else 0.0
        g(purity) = max_fraction / 255

    Cohesion is BLIND: cell reads its own and the neighbor's canonical
    composition; does not consult the neighbor's cohesion value. There is
    no reciprocity. Asymmetric behavior between A→B and B→A emerges from
    the blind sum during flux computation in M5'.3.

    Returns: float32[N, 6]. cohesion[i, d] = 0 when neighbor d doesn't
    exist (grid edge).
    """
    n = cells.n
    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)   # (N, 6)

    cohesion = np.zeros((n, N_PETAL_DIRS), dtype=np.float32)
    if n == 0:
        return cohesion

    # Per-cell purity = max fraction across slots / 255
    fracs = cells.composition[:, :, 1].astype(np.float32)
    purity = fracs.max(axis=1) / 255.0

    # Majority element from derived (must be computed first)
    majority = derived.majority_element

    for d in range(N_PETAL_DIRS):
        nids = neighbors[:, d]
        valid = (nids != -1)
        if not valid.any():
            continue
        nbr_majority = np.where(valid, majority[np.where(valid, nids, 0)], 0)
        # Boolean: same majority element and both non-void (majority != 0)
        match = valid & (majority != 0) & (majority == nbr_majority)
        cohesion[match, d] = 1.0 * purity[match]

    return cohesion


# --------------------------------------------------------------------------
# Orchestrator — runs at top of cycle (and at top of each sub-pass when
# physics is flowing in M5'.3+)
# --------------------------------------------------------------------------

def run_derive(
    cells: CellArrays,
    element_table,
    world: "WorldConfig",
    derived: DerivedFields,
) -> None:
    """Refresh all derived fields from current canonical state. Mutates
    `derived` in place.

    Order matters: identity must come before cohesion (cohesion reads
    majority_element); pressure and temperature are independent of the
    others.
    """
    # Identity
    majority_phase, majority_element = compute_identity(cells)
    derived.majority_phase[:]   = majority_phase
    derived.majority_element[:] = majority_element

    # Cohesion (depends on identity)
    derived.cohesion[:] = compute_cohesion(cells, derived)

    # Temperature
    derived.temperature[:] = compute_temperature(cells, element_table, world)

    # Pressure decode
    derived.pressure[:] = decode_pressure_to_f32(cells)

    # Gravity vector field — populated at M5'.2 (Jacobi diffusion). For
    # M5'.1 we leave it at the zero from allocate().
