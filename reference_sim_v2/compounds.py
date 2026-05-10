"""
Compound macros — gen5 §"Periodic table data strategy".

Compounds are an AUTHORING CONVENIENCE that expand to element-level
composition vectors at scenario init AND a CALIBRATION HANDLE that lets
the runtime treat a "water cell" as having water's molecular thermal
properties rather than a 47-percent-H + 53-percent-O atomic blend
(which gives wildly wrong c_p, ρ, L per the M6'.x calibration caveat).

Material IDs ≥ 200 are reserved for compound aliases. Each entry has:
  - `recipe`: list of (element_symbol, fraction) pairs summing to 255 —
    the composition vector poured into the cell at init
  - `props`: a `CompoundProperties` record with calibrated per-phase
    density / specific heat / thermal conductivity / latent heats / phase
    boundaries. When a cell carries a non-zero `compound_id`, derive +
    transitions consult these values directly instead of blending the
    element-table per-element rows.

Tier 1 inventory:
    200 — water (H₂O), with NIST calibration

The H/O ratio (114:141) is gen5's prescribed composition value. It does
NOT match the H₂O atom ratio (2:1 → 170:85) or the mass ratio (~11:89).
gen5 uses these as composition-fraction units calibrated for the
framework's phase-density-equilibrium model. The `props` record
sidesteps the resulting per-element-blend mismatch with real H₂O.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cell import COMPOSITION_SLOTS, CellArrays


@dataclass(frozen=True)
class CompoundProperties:
    """Per-phase calibrated properties for a compound. Values reflect
    the compound at its real-world equilibrium (real H₂O parameters for
    H2O, etc.), so the simulation's c_p_l/c_p_s ratio matches reality
    instead of falling out of an arbitrary atomic blend."""
    density_solid:  float       # kg/m³
    density_liquid: float
    density_gas:    float
    specific_heat_solid:  float  # J/(kg·K)
    specific_heat_liquid: float
    specific_heat_gas:    float
    thermal_conductivity_solid:  float   # W/(m·K)
    thermal_conductivity_liquid: float
    thermal_conductivity_gas:    float
    L_fusion:        float       # J/kg
    L_vaporization:  float


# Compound recipe + calibrated properties.
COMPOUNDS: dict[int, dict] = {
    200: {
        "recipe": [("H", 114), ("O", 141)],
        # NIST-sourced H₂O at 1 atm. Solid = ice at 0 °C; liquid = water at
        # ~25 °C; gas = vapor at 100 °C (close to STP). cp_l/cp_s = 4186/2090
        # ≈ 2.0× — a normal phase-discontinuity ratio that the M5'.5c energy-
        # balance Δm formula handles cleanly without the 1/16 cap.
        "props": CompoundProperties(
            density_solid=917.0,
            density_liquid=1000.0,
            density_gas=0.804,
            specific_heat_solid=2090.0,
            specific_heat_liquid=4186.0,
            specific_heat_gas=2010.0,
            thermal_conductivity_solid=2.18,
            thermal_conductivity_liquid=0.598,
            thermal_conductivity_gas=0.018,
            L_fusion=334000.0,
            L_vaporization=2257000.0,
        ),
    },
}


def get_compound_properties(compound_id: int) -> CompoundProperties | None:
    """Return calibrated properties for a compound, or None if the id is
    not a known compound (compound_id == 0 means "no compound")."""
    entry = COMPOUNDS.get(compound_id)
    if entry is None:
        return None
    return entry["props"]


def get_compound_recipe(compound_id: int) -> list[tuple[str, int]]:
    """Return the (element_symbol, fraction) recipe for a compound."""
    entry = COMPOUNDS.get(compound_id)
    if entry is None:
        raise ValueError(f"unknown compound id {compound_id}")
    return list(entry["recipe"])


# --------------------------------------------------------------------------
# Scenario helpers — compute compound-calibrated phase_mass / energy values
# so scenario init code stays in sync with the runtime's compound-aware
# derive + transitions. Use these instead of doing atomic-blend math.
# --------------------------------------------------------------------------

def compound_eq_phase_mass(compound_id: int, phase: int, world) -> float:
    """Per-cell equilibrium phase_mass (hex units) for a compound at the
    given phase: density_compound_phase × volume / Q_KG. A fully-saturated
    compound cell holds this many hex units in `phase_mass[phase]`."""
    from .cell import PHASE_GAS, PHASE_LIQUID, PHASE_PLASMA, PHASE_SOLID, Q_KG
    props = get_compound_properties(compound_id)
    if props is None:
        raise ValueError(f"unknown compound id {compound_id}")
    if phase == PHASE_SOLID:
        d = props.density_solid
    elif phase == PHASE_LIQUID:
        d = props.density_liquid
    elif phase == PHASE_GAS or phase == PHASE_PLASMA:
        d = props.density_gas
    else:
        raise ValueError(f"unknown phase id {phase}")
    return d * (world.cell_size_m ** 3) / Q_KG


def compound_cell_energy_J(
    phase_mass_solid: float,
    phase_mass_liquid: float,
    phase_mass_gas: float,
    T_K: float,
    compound_id: int,
) -> float:
    """Internal energy in joules for a compound cell with the given
    per-phase hex-unit masses at temperature T. Mirrors derive's
    mass-weighted cp formula:

        E = T × Q_KG × Σ_p (phase_mass[p] × cp_p_compound)

    Use this to set energy_raw at scenario init so the cell decodes back
    to the intended T.
    """
    from .cell import Q_KG
    props = get_compound_properties(compound_id)
    if props is None:
        raise ValueError(f"unknown compound id {compound_id}")
    return T_K * Q_KG * (
        phase_mass_solid  * props.specific_heat_solid  +
        phase_mass_liquid * props.specific_heat_liquid +
        phase_mass_gas    * props.specific_heat_gas
    )


def set_compound(
    cells: CellArrays,
    cell_id: int,
    compound_id: int,
    element_table,
) -> None:
    """Expand a compound macro into the cell's composition slots and tag
    the cell with `compound_id`.

    Resets all 16 slots to (0, 0), then fills slot 0..N-1 with the
    compound's (element_id, fraction) pairs from COMPOUNDS[compound_id].
    Validates the symbol exists in the element_table and the fractions
    sum to exactly 255 (composition_sum_255 invariant).

    Also writes `compound_id` to `cells.compound_id[cell_id]` so the
    runtime (derive, transitions, region) can consult calibrated
    compound properties via `get_compound_properties` and bypass the
    per-element atomic blend.
    """
    if compound_id not in COMPOUNDS:
        raise ValueError(f"unknown compound id {compound_id}; known: {sorted(COMPOUNDS)}")
    spec = get_compound_recipe(compound_id)
    total = sum(frac for _, frac in spec)
    if total != 255:
        raise ValueError(
            f"compound {compound_id} fractions sum to {total} ≠ 255 — "
            "violates composition_sum_255 invariant"
        )
    if len(spec) > COMPOSITION_SLOTS:
        raise ValueError(
            f"compound {compound_id} has {len(spec)} elements; cell has "
            f"only {COMPOSITION_SLOTS} slots"
        )

    cells.composition[cell_id, :, :] = 0
    for slot, (sym, frac) in enumerate(spec):
        try:
            element = element_table[sym]
        except KeyError:
            raise ValueError(
                f"compound {compound_id} references {sym!r} but element_table "
                "does not contain it"
            )
        cells.composition[cell_id, slot, 0] = element.element_id
        cells.composition[cell_id, slot, 1] = frac
    cells.compound_id[cell_id] = compound_id


def is_water_cell(cells: CellArrays, cell_id: int, element_table) -> bool:
    """Quick check: does this cell's composition match the water recipe?
    Used by scenarios that need to recognise water cells post-init."""
    spec = get_compound_recipe(200)
    expected = {element_table[sym].element_id: frac for sym, frac in spec}
    have = {}
    for slot in range(COMPOSITION_SLOTS):
        eid = int(cells.composition[cell_id, slot, 0])
        frac = int(cells.composition[cell_id, slot, 1])
        if eid != 0 and frac > 0:
            have[eid] = frac
    return have == expected
