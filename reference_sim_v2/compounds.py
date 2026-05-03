"""
Compound macros — gen5 §"Periodic table data strategy".

Compounds are AUTHORING CONVENIENCE only — they expand to element-level
composition vectors at scenario init and don't exist past that point.
After expansion the kernel sees only periodic-table elements (gen5
"Material identity = periodic table"; the user confirmed this is the
runtime-data choice in the gen5 session).

Material IDs ≥ 200 are reserved for compound aliases. Each entry maps
to a list of (element_symbol, fraction) pairs that sum to 255.

Tier 1 inventory:
    200 — water (H₂O), [(H, 114), (O, 141)]   ← gen5 §"Periodic table data strategy"

The H/O ratio (114:141) is gen5's prescribed value. It does NOT match
the H₂O atom ratio (2:1 → 170:85) or the mass ratio (~11:89). gen5
uses these as composition-fraction units calibrated for the framework's
phase-density-equilibrium model rather than as direct physical ratios.
Documented as the gen5 default; M6'.x calibration may revisit if scenario
behaviour disagrees with reality.
"""

from __future__ import annotations

import numpy as np

from .cell import COMPOSITION_SLOTS, CellArrays


# Compound recipe: list of (symbol, fraction) pairs that sum to 255.
COMPOUNDS: dict[int, list[tuple[str, int]]] = {
    200: [("H", 114), ("O", 141)],   # water
}


def set_compound(
    cells: CellArrays,
    cell_id: int,
    compound_id: int,
    element_table,
) -> None:
    """Expand a compound macro into the cell's composition slots.

    Resets all 16 slots to (0, 0), then fills slot 0..N-1 with the
    compound's (element_id, fraction) pairs from COMPOUNDS[compound_id].
    Validates the symbol exists in the element_table and the fractions
    sum to exactly 255 (composition_sum_255 invariant).
    """
    if compound_id not in COMPOUNDS:
        raise ValueError(f"unknown compound id {compound_id}; known: {sorted(COMPOUNDS)}")
    spec = COMPOUNDS[compound_id]
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


def is_water_cell(cells: CellArrays, cell_id: int, element_table) -> bool:
    """Quick check: does this cell's composition match the water recipe?
    Used by scenarios that need to recognise water cells post-init."""
    spec = COMPOUNDS[200]
    expected = {element_table[sym].element_id: frac for sym, frac in spec}
    have = {}
    for slot in range(COMPOSITION_SLOTS):
        eid = int(cells.composition[cell_id, slot, 0])
        frac = int(cells.composition[cell_id, slot, 1])
        if eid != 0 and frac > 0:
            have[eid] = frac
    return have == expected
