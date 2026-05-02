"""
Emit v2 — schema-v2 JSON writer.

Schema-v2 is the gen5 contract between the reference sim, the eventual CUDA
port, the v2 viewer, and verify_v2.py. Major differences from schema-v1:

  - schema_version: 2
  - composition is up to 16 (element, fraction) pairs, not 4
  - phase_fraction (4-channel) and phase_mass (per-phase) replace the single
    phase enum
  - identity is computed and emitted as a derived field (informational)
  - petals (6 per cell) emit per-direction stress, velocity, topology
  - gravity_vec (per-cell f32 [gx, gy]) emits when scenario uses gravity
  - cohesion (per-cell-per-direction f32) emits in debug mode only
  - totals.mass_by_element_by_phase replaces totals.mass_by_element
  - sub_pass field added alongside tick to track within-cycle granularity

See gen5_roadmap.md §3.2 for the full schema reference.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .cell import (
    COMPOSITION_SLOTS,
    CellArrays,
    EQUILIBRIUM_CENTER,
    N_PETAL_DIRS,
    N_PHASES,
    PETAL_TOPO_BORDER_TYPE_MASK,
    PETAL_TOPO_BORDER_TYPE_SHIFT,
    PETAL_TOPO_IS_BORDER,
    PETAL_TOPO_IS_GRID_EDGE,
    PETAL_TOPO_IS_INERT,
    PHASE_NAMES,
    composition_pairs,
    compute_identity,
)
from .scenario import Scenario


SCHEMA_VERSION = 2


# Persistent flag bit positions (subset of Tier 0). These are the four flags
# gen5's border-properties model relies on; transient flags (CULLED etc.)
# from Tier 0 are de-emphasized but kept for backward inspection.
FLAG_NAMES = (
    (1 << 0, "no_flow"),
    (1 << 1, "radiates"),
    (1 << 2, "insulated"),
    (1 << 3, "fixed_state"),
    (1 << 4, "culled"),
    (1 << 5, "fractured"),
    (1 << 6, "ratcheted_this_tick"),
    (1 << 7, "excluded"),
)


def emit_cycle(
    scenario: Scenario,
    tick: int,
    cycle: int,
    sub_pass: int,
    stage: str,
    run_id: str,
    derived=None,
    cycle_timing_ms: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build the schema-v2 payload for one emission point.

    `derived` is an optional DerivedFields snapshot. When provided, we use
    its precomputed identity / cohesion / T / gravity_vec so the JSON
    matches what the sim's own derive stage produced (no recomputation
    drift between sim and emit). When None, identity is recomputed inline
    and other derived fields are skipped.
    """

    cells = scenario.cells
    grid = scenario.grid
    table = scenario.element_table

    id_to_symbol = {e.element_id: e.symbol for e in table}

    if derived is not None:
        majority_phase = derived.majority_phase
        majority_element = derived.majority_element
        temperature = derived.temperature
        gravity_vec = derived.gravity_vec
        cohesion = derived.cohesion
    else:
        majority_phase, majority_element = compute_identity(cells)
        temperature = None
        gravity_vec = None
        cohesion = None

    cell_objs = []
    for cid in range(grid.cell_count):
        cell_objs.append(_build_cell_object(
            cells=cells,
            cid=cid,
            coord=grid.coords[cid],
            id_to_symbol=id_to_symbol,
            majority_phase=int(majority_phase[cid]),
            majority_element_id=int(majority_element[cid]),
            temperature=temperature,
            gravity_vec=gravity_vec,
            cohesion=cohesion,
            include_petals=scenario.emission.include_petals,
            include_gravity_vec=scenario.emission.include_gravity_vec,
            include_cohesion=scenario.emission.include_cohesion,
        ))

    totals = _compute_totals(scenario, id_to_symbol, majority_phase)
    invariants = _self_report_invariants(scenario, totals)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "scenario": scenario.name,
        "tick": tick,
        "cycle": cycle,
        "sub_pass": sub_pass,
        "stage": stage,
        "grid": {
            "shape": grid.shape,
            "rings": grid.rings,
            "cell_count": grid.cell_count,
            "coordinate_system": "axial_qr",
        },
        "element_table_hash": table.source_hash,
        "phase_diagram_hash": getattr(table, "phase_diagram_hash", "sha256:none"),
        "border_table_hash": getattr(table, "border_table_hash", "sha256:none"),
        "allowed_elements": list(scenario.allowed_elements),
        "cells": cell_objs,
        "totals": totals,
        "invariants": invariants,
        "cycle_timing_ms": cycle_timing_ms or {},
    }
    return payload


def _build_cell_object(
    cells: CellArrays,
    cid: int,
    coord: tuple[int, int],
    id_to_symbol: dict[int, str],
    majority_phase: int,
    majority_element_id: int,
    temperature: np.ndarray | None,
    gravity_vec: np.ndarray | None,
    cohesion: np.ndarray | None,
    include_petals: bool,
    include_gravity_vec: bool,
    include_cohesion: bool,
) -> dict[str, Any]:
    flags_u8 = int(cells.flags[cid])

    obj: dict[str, Any] = {
        "id": cid,
        "coord": list(coord),
        "composition": composition_pairs(cells, cid, id_to_symbol),
        "phase_fraction": [float(cells.phase_fraction[cid, p]) for p in range(N_PHASES)],
        "phase_mass":     [float(cells.phase_mass[cid, p])     for p in range(N_PHASES)],
        "pressure_raw":   int(cells.pressure_raw[cid]),
        "energy_raw":     int(cells.energy_raw[cid]),
        "mohs_level":     int(cells.mohs_level[cid]),
        "sustained_overpressure": float(cells.sustained_overpressure[cid]),
        "identity": {
            "phase":   PHASE_NAMES.get(majority_phase, "void"),
            "element": id_to_symbol.get(majority_element_id, "" if majority_element_id == 0 else str(majority_element_id)),
        },
        "flags": _flags_to_dict(flags_u8),
    }

    if temperature is not None:
        obj["temperature_K"] = float(temperature[cid])

    if include_petals:
        petals = []
        for d in range(N_PETAL_DIRS):
            topo_u8 = int(cells.petal_topology[cid, d])
            border_idx = (topo_u8 & PETAL_TOPO_BORDER_TYPE_MASK) >> PETAL_TOPO_BORDER_TYPE_SHIFT
            petals.append({
                "direction": d,
                "stress":   float(cells.petal_stress[cid, d]),
                "velocity": [float(cells.petal_velocity[cid, d, 0]),
                             float(cells.petal_velocity[cid, d, 1])],
                "topology": {
                    "is_border":    bool(topo_u8 & PETAL_TOPO_IS_BORDER),
                    "is_grid_edge": bool(topo_u8 & PETAL_TOPO_IS_GRID_EDGE),
                    "is_inert":     bool(topo_u8 & PETAL_TOPO_IS_INERT),
                    "border_type":  int(border_idx) if border_idx else None,
                },
            })
        obj["petals"] = petals

    if include_gravity_vec and gravity_vec is not None:
        obj["gravity_vec"] = [float(gravity_vec[cid, 0]), float(gravity_vec[cid, 1])]

    if include_cohesion and cohesion is not None:
        obj["cohesion"] = [float(cohesion[cid, d]) for d in range(N_PETAL_DIRS)]

    return obj


def _flags_to_dict(flags_u8: int) -> dict[str, bool]:
    return {name: bool(flags_u8 & bit) for bit, name in FLAG_NAMES}


def _compute_totals(
    scenario: Scenario,
    id_to_symbol: dict[int, str],
    majority_phase: np.ndarray,
) -> dict[str, Any]:
    cells = scenario.cells

    # Mass by (element, phase) — gen5 conservation invariant is per-element-
    # per-phase, since composition is multi-slot and phase is fractional.
    mass_by_element_by_phase: dict[str, dict[str, float]] = {}
    for slot in range(COMPOSITION_SLOTS):
        eids = cells.composition[:, slot, 0]
        fracs = cells.composition[:, slot, 1].astype(np.float32) / 255.0
        for eid in np.unique(eids):
            if eid == 0:
                continue
            sym = id_to_symbol.get(int(eid), str(int(eid)))
            mask = (eids == eid)
            if not mask.any():
                continue
            # Element's contribution per phase = element fraction × phase_mass
            contributions = fracs[mask][:, None] * cells.phase_mass[mask, :]   # (M, 4)
            per_phase_total = contributions.sum(axis=0)
            entry = mass_by_element_by_phase.setdefault(sym, {p: 0.0 for p in PHASE_NAMES.values()})
            for p_idx, p_name in PHASE_NAMES.items():
                entry[p_name] = float(entry[p_name] + per_phase_total[p_idx])

    energy_total = float(cells.energy_raw.sum())

    cells_by_dominant_phase = {p: 0 for p in PHASE_NAMES.values()}
    cells_by_dominant_phase["void"] = 0
    for cid in range(cells.n):
        mp = int(majority_phase[cid])
        if mp == 255:
            cells_by_dominant_phase["void"] += 1
        else:
            cells_by_dominant_phase[PHASE_NAMES[mp]] += 1

    flags = cells.flags
    return {
        "mass_by_element_by_phase": mass_by_element_by_phase,
        "energy_total": energy_total,
        "momentum_total": [
            float(cells.petal_velocity[:, :, 0].sum()),
            float(cells.petal_velocity[:, :, 1].sum()),
        ],
        "cells_by_dominant_phase": cells_by_dominant_phase,
        "cells_culled":              int(((flags & (1 << 4)) != 0).sum()),
        "cells_fractured":           int(((flags & (1 << 5)) != 0).sum()),
        "cells_ratcheted_this_tick": int(((flags & (1 << 6)) != 0).sum()),
        "cells_excluded":            int(((flags & (1 << 7)) != 0).sum()),
    }


def _self_report_invariants(
    scenario: Scenario,
    totals: dict[str, Any],
) -> list[dict[str, Any]]:
    """Sim's self-reported invariants. verify_v2.py independently re-checks
    these; divergence between self-report and external check is the bug
    signal."""
    cells = scenario.cells

    # composition_sum_255: every non-void cell must sum to 255
    sums = cells.composition[:, :, 1].sum(axis=1)
    comp_violations = []
    for cid in range(cells.n):
        nonvoid = bool((cells.composition[cid, :, 1] != 0).any() or
                       (cells.composition[cid, :, 0] != 0).any())
        if nonvoid and int(sums[cid]) != 255:
            comp_violations.append({"cell_id": cid, "sum": int(sums[cid])})

    # phase_fraction_sum_le_1: vacuum is the complement; sum must be ≤ 1.0
    # (with a tiny tolerance for f32 accumulation).
    pf_sums = cells.phase_fraction.sum(axis=1)
    phase_violations = []
    for cid in range(cells.n):
        if pf_sums[cid] > 1.0 + 1e-5:
            phase_violations.append({"cell_id": cid, "sum": float(pf_sums[cid])})

    invariants = [
        {
            "name": "composition_sum_255",
            "status": "pass" if not comp_violations else "fail",
            "violations": comp_violations,
        },
        {
            "name": "phase_fraction_sum_le_1",
            "status": "pass" if not phase_violations else "fail",
            "violations": phase_violations,
        },
        # Self-report on per-element-per-phase mass conservation. verify_v2
        # arbitrates against the baseline.
        {
            "name": "mass_by_element_by_phase_self_report",
            "status": "pass",
            "totals": totals["mass_by_element_by_phase"],
        },
    ]
    return invariants


def write_emission(payload: dict[str, Any], output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tick = payload["tick"]
    stage = payload["stage"]
    fname = f"tick_{tick:05d}_{stage}.json"
    path = output_dir / fname
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def new_run_id(scenario_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{scenario_name}_{ts}"
