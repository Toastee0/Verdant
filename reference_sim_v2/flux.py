"""
Flux records — gen5 §"Flux records (scratch, per-cycle)" + §"Flux summation".

Per-edge transport contributions, computed by region kernels each sub-pass,
summed by blind addition, then integrated to update canonical state.

Cell-centric SoA layout (per D3 in gen5_roadmap.md): each cell owns its 6
outgoing records. Cell B's "incoming from A in direction d" is read as
`outgoing[A, OPPOSITE_DIRECTION[d]]` — the authorship convention. No
separate incoming buffer; integration walks neighbours and sums.

Channels:
  - mass:     (N, 6, COMPOSITION_SLOTS, N_PHASES) f32 — element/phase
              transport. Per-element so multi-composition cells flow
              independently per slot; per-phase so mixed-phase cells
              transport each phase fraction independently.
  - momentum: (N, 6, 2) f32 — 2D vector momentum carried with mass.
  - energy:   (N, 6)   f32 — kinetic + thermal + pressure work.
  - stress:   (N, 6)   f32 — directional stress (solid only).

Conservation invariant: for every (A, d) where neighbour exists and bond
is not vetoed, mass leaving A in direction d should appear as mass
arriving at neighbour B from direction OPPOSITE[d]. The integration step
is symmetric by construction; the verifier's flux-summation-symmetric-per-edge
invariant catches authorship-convention bugs.

Veto stage: before integration, flux entries where the edge is out-of-grid
(neighbour=-1) or NO_FLOW (per channel) are zeroed. This prevents mass
loss across grid boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .cell import COMPOSITION_SLOTS, N_PHASES, N_PETAL_DIRS, CellArrays
from .grid import OPPOSITE_DIRECTION

if TYPE_CHECKING:
    from .scenario import WorldConfig


# Flag bits for veto (mirror cell.py's flags layout)
FLAG_NO_FLOW = 1 << 0
FLAG_RADIATES = 1 << 1
FLAG_INSULATED = 1 << 2
FLAG_FIXED_STATE = 1 << 3
FLAG_CULLED = 1 << 4


@dataclass
class FluxBuffer:
    """Per-cell-per-direction outgoing flux, allocated once per scenario,
    cleared at the start of each sub-pass."""
    mass:     np.ndarray   # float32[N, 6, COMPOSITION_SLOTS, N_PHASES]
    momentum: np.ndarray   # float32[N, 6, 2]
    energy:   np.ndarray   # float32[N, 6]
    stress:   np.ndarray   # float32[N, 6]

    @classmethod
    def allocate(cls, n: int) -> "FluxBuffer":
        return cls(
            mass=np.zeros((n, N_PETAL_DIRS, COMPOSITION_SLOTS, N_PHASES), dtype=np.float32),
            momentum=np.zeros((n, N_PETAL_DIRS, 2), dtype=np.float32),
            energy=np.zeros((n, N_PETAL_DIRS), dtype=np.float32),
            stress=np.zeros((n, N_PETAL_DIRS), dtype=np.float32),
        )

    def clear(self) -> None:
        self.mass.fill(0)
        self.momentum.fill(0)
        self.energy.fill(0)
        self.stress.fill(0)


# --------------------------------------------------------------------------
# Veto stage — zero out fluxes that would cross hard-constraint edges
# --------------------------------------------------------------------------

def apply_veto(
    cells: CellArrays,
    flux: FluxBuffer,
) -> None:
    """Zero out flux entries where:
      - the neighbour is -1 (out-of-grid)
      - either endpoint has NO_FLOW (mass/momentum/stress)
      - either endpoint has INSULATED (energy only)
      - either endpoint has FIXED_STATE (mass + energy + stress; FIXED_STATE
        cells are state-pinned per gen5 §"Borders")

    Operates in place on `flux`. Returns nothing.
    """
    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)        # (N, 6)
    valid = neighbors >= 0                                       # (N, 6)

    # Per-direction veto masks
    flags_padded = np.concatenate([cells.flags, np.array([0xFF], dtype=np.uint8)])
    nbr_flags = flags_padded[neighbors]                          # (N, 6)
    self_flags = cells.flags[:, None]                            # (N, 1)

    no_flow = ((self_flags | nbr_flags) & FLAG_NO_FLOW) != 0     # (N, 6)
    insulated = ((self_flags | nbr_flags) & FLAG_INSULATED) != 0
    fixed_state = ((self_flags | nbr_flags) & FLAG_FIXED_STATE) != 0

    # Out-of-grid veto applies to all channels
    not_valid = ~valid
    veto_mass = not_valid | no_flow | fixed_state                # (N, 6)
    veto_energy = not_valid | insulated | fixed_state
    veto_stress = not_valid | no_flow | fixed_state
    veto_momentum = not_valid | no_flow | fixed_state

    flux.mass[veto_mass]         = 0.0
    flux.momentum[veto_momentum] = 0.0
    flux.energy[veto_energy]     = 0.0
    flux.stress[veto_stress]     = 0.0


# --------------------------------------------------------------------------
# Integration — apply outgoing - incoming to canonical state
# --------------------------------------------------------------------------

def integrate(
    cells: CellArrays,
    flux: FluxBuffer,
    world: "WorldConfig",
) -> None:
    """Apply per-cell mass/momentum/energy/stress deltas from the flux
    buffer to canonical state.

    For each cell B and each direction d:
        outgoing[B, d, ...]  is what B sent in direction d
        incoming[B, d, ...]  is what B received from neighbor[B, d] in
                              their direction OPPOSITE[d]
                            = flux[neighbor[B, d], OPPOSITE[d], ...]

    Net change = +incoming - outgoing summed across directions.

    For M5'.3, integration acts on:
      - phase_mass: mass flux integrates per phase, summed across slots
      - composition: M5'.3 stub — composition fractions stay constant
        (single-element scenarios). Multi-element mixing lands at M5'.5.
      - energy_raw: energy flux integrates as f32 → re-encoded to u16
      - petal_stress: stress flux accumulates onto BOTH endpoints' petals
        (M5'.6 detail; M5'.3 keeps petals untouched)
      - petal_velocity: momentum flux divided by mass updates velocity
        (M5'.6 detail)

    FIXED_STATE cells are exempt from updates (their canonical state is
    held; flux contributions from them still flow to neighbours, but they
    don't accept incoming).
    """
    n = cells.n
    if n == 0:
        return

    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)         # (N, 6)
    fixed = (cells.flags & FLAG_FIXED_STATE) != 0

    # ----- Mass -----
    # outgoing per (cell, phase) = sum over (direction, slot)
    outgoing_phase_mass = flux.mass.sum(axis=(1, 2))             # (N, 4)

    # incoming per (cell, phase) = sum over (direction, slot) of
    #   flux[neighbor[cell, d], OPPOSITE[d], slot, phase]
    flux_mass_padded = np.concatenate([
        flux.mass,
        np.zeros((1, N_PETAL_DIRS, COMPOSITION_SLOTS, N_PHASES), dtype=np.float32),
    ])
    incoming_phase_mass = np.zeros((n, N_PHASES), dtype=np.float32)
    for d in range(N_PETAL_DIRS):
        opp = OPPOSITE_DIRECTION[d]
        contributions = flux_mass_padded[neighbors[:, d], opp, :, :].sum(axis=1)  # (N, 4)
        incoming_phase_mass += contributions

    delta_phase_mass = incoming_phase_mass - outgoing_phase_mass
    if fixed.any():
        delta_phase_mass[fixed, :] = 0.0
    cells.phase_mass[:, :] += delta_phase_mass

    # ----- Energy -----
    outgoing_energy = flux.energy.sum(axis=1)                    # (N,)
    flux_energy_padded = np.concatenate([flux.energy, np.zeros((1, N_PETAL_DIRS), dtype=np.float32)])
    incoming_energy = np.zeros(n, dtype=np.float32)
    for d in range(N_PETAL_DIRS):
        opp = OPPOSITE_DIRECTION[d]
        incoming_energy += flux_energy_padded[neighbors[:, d], opp]

    delta_energy = incoming_energy - outgoing_energy
    if fixed.any():
        delta_energy[fixed] = 0.0
    # Apply with u16 clamp (re-encode at canonical-state boundary)
    new_energy = cells.energy_raw.astype(np.float32) + delta_energy
    new_energy = np.clip(new_energy, 0.0, 65535.0)
    cells.energy_raw[:] = np.round(new_energy).astype(np.uint16)

    # ----- Momentum, stress -----
    # M5'.3 stubs — these channels integrate to petal data at M5'.6.
    # For now we don't update petal_velocity/petal_stress; the verifier
    # invariant for petal symmetry won't fire because flux is zero on
    # those channels in the M5'.3 scenarios.


# --------------------------------------------------------------------------
# Symmetry diagnostic (used by verifier indirectly + for in-sim asserts)
# --------------------------------------------------------------------------

def flux_summation_residual(
    cells: CellArrays,
    flux: FluxBuffer,
) -> float:
    """For every edge that wasn't vetoed, sum of mass flux from both sides
    should reflect coherent transport (not be wildly asymmetric in a way
    that signals an authorship-convention bug). The residual we report is
    the global sum of `(flux[A, d] - (-flux[B, OPP[d]]))` — for a sane
    integration where outgoing[A]=incoming[B], the residual encodes any
    spurious source/sink injected by region kernels.

    For M5'.3's region kernel, each region writes its OWN cell's outgoing
    only — neighbours' outgoing is independently authored. Conservation
    ⇔ Σ_A outgoing[A, d] over all (A, d, slot, phase) where the bond is
    not vetoed equals Σ_B incoming[B] for the matched (B, OPP[d]).

    The strict "every edge must net to zero" check requires equal-and-
    opposite contributions, which is FALSE in gen5 (different phenomena
    contribute different fluxes per direction). The right invariant is
    GLOBAL conservation: total mass_per_phase is unchanged after veto
    + integrate. That check lives in verify_v2's mass-per-element-per-phase
    invariant.

    This helper exists for in-sim debugging — return value is the L1 norm
    of (flux + flipped-neighbor-flux) across the grid.
    """
    grid = cells.grid
    neighbors = np.array(grid.neighbors, dtype=np.int32)
    valid = neighbors >= 0

    flux_mass_padded = np.concatenate([
        flux.mass,
        np.zeros((1, N_PETAL_DIRS, COMPOSITION_SLOTS, N_PHASES), dtype=np.float32),
    ])

    # For every (A, d, ...), compare flux[A, d] to flux[neighbor[A, d], OPP[d]].
    # An "edge" is the pair (A, d). Each unique edge appears twice in this
    # listing (once as (A, d_to_B), once as (B, OPP[d])). Sum the two for
    # the directional flow on that edge: outgoing[A] + outgoing[B in opposite] =
    # net edge transport (positive = A→B net, negative = B→A net).
    residual_sum = 0.0
    for d in range(N_PETAL_DIRS):
        opp = OPPOSITE_DIRECTION[d]
        nbr_flux = flux_mass_padded[neighbors[:, d], opp, :, :]   # (N, 16, 4)
        # For valid edges only
        v = valid[:, d]
        if not v.any():
            continue
        residual_sum += float(np.abs(flux.mass[v, d] + nbr_flux[v]).sum())
    return residual_sum
