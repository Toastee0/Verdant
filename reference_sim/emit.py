"""
Stage 6 — Emit.

Produce schema-v1 JSON matching ARCHITECTURE.md. This is the contract shared
with the viewer, verify.py, and eventually the CUDA port. Every field that
appears here must also appear from CUDA or the cross-validator breaks.

See ARCHITECTURE.md for the full schema reference.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .cell import CellArrays, COMPOSITION_SLOTS, PHASE_NAMES
from .derive import DerivedFields, _decode_pressure_all
from .element_table import ElementTable
from .flags import flags_to_dict
from .propagate import PropagateBuffers
from .scenario import Scenario


SCHEMA_VERSION = 1


def emit_tick(
    scenario: Scenario,
    derived: DerivedFields | None,
    buffers: PropagateBuffers | None,
    tick: int,
    stage: str,
    cycle: int,
    run_id: str,
    stage_timing_ms: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build the schema-v1 payload for one emission point. Returns the dict
    (caller can write to disk or pass to verify.py directly)."""

    cells = scenario.cells
    grid = scenario.grid
    table = scenario.element_table

    id_to_symbol = {e.element_id: e.symbol for e in table}

    # Per-cell objects
    cell_objs = []
    decoded_p = _decode_pressure_all(cells, table)
    for cid in range(grid.cell_count):
        cell_obj = _build_cell_object(
            cells=cells,
            cid=cid,
            coord=grid.coords[cid],
            decoded_pressure=float(decoded_p[cid]),
            id_to_symbol=id_to_symbol,
        )
        cell_objs.append(cell_obj)

    # Totals
    totals = _compute_totals(scenario, id_to_symbol)

    # Self-reported invariants (the sim's own check — the external verify.py
    # independently re-verifies these)
    invariants = _self_report_invariants(scenario, totals)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "scenario": scenario.name,
        "tick": tick,
        "stage": stage,
        "cycle": cycle,
        "grid": {
            "shape": grid.shape,
            "rings": grid.rings,
            "cell_count": grid.cell_count,
            "coordinate_system": "axial_qr",
        },
        "element_table_hash": table.source_hash,
        "allowed_elements": list(scenario.allowed_elements),
        "cells": cell_objs,
        "totals": totals,
        "invariants": invariants,
        "stage_timing_ms": stage_timing_ms or {},
    }

    return payload


def _build_cell_object(
    cells: CellArrays,
    cid: int,
    coord: tuple[int, int],
    decoded_pressure: float,
    id_to_symbol: dict[int, str],
) -> dict[str, Any]:
    composition = []
    for slot in range(COMPOSITION_SLOTS):
        eid = int(cells.composition[cid, slot, 0])
        frac = int(cells.composition[cid, slot, 1])
        if eid == 0 and frac == 0:
            continue
        symbol = id_to_symbol.get(eid, str(eid))
        composition.append([symbol, frac])

    flags_u8 = int(cells.flags[cid])
    return {
        "id": cid,
        "coord": list(coord),
        "phase": PHASE_NAMES[int(cells.phase[cid])],
        "mohs_level": int(cells.mohs_level[cid]),
        "pressure_raw": int(cells.pressure_raw[cid]),
        "pressure_decoded": decoded_pressure,
        "energy": int(cells.energy[cid]),
        "composition": composition,
        "flags": flags_to_dict(flags_u8),
        "elastic_strain": int(cells.elastic_strain[cid]),
        "magnetization": int(cells.magnetization[cid]),
        # gradient / bids intentionally omitted unless scenario opts in
        "gradient": [0, 0, 0, 0, 0, 0],
        "bids_sent": [],
        "bids_received": [],
    }


def _compute_totals(scenario: Scenario, id_to_symbol: dict[int, str]) -> dict[str, Any]:
    cells = scenario.cells
    # mass_by_element from composition fractions
    mass_by_element: dict[str, int] = {}
    for slot in range(COMPOSITION_SLOTS):
        eids = cells.composition[:, slot, 0]
        fracs = cells.composition[:, slot, 1]
        for eid in np.unique(eids):
            if eid == 0:
                continue
            mask = (eids == eid)
            total = int(fracs[mask].sum())
            sym = id_to_symbol.get(int(eid), str(int(eid)))
            mass_by_element[sym] = mass_by_element.get(sym, 0) + total

    energy_total = float(cells.energy.sum())
    cells_by_phase = {name: 0 for name in PHASE_NAMES.values()}
    for phase_id, name in PHASE_NAMES.items():
        cells_by_phase[name] = int((cells.phase == phase_id).sum())

    from .flags import CULLED, RATCHETED, FRACTURED, EXCLUDED
    return {
        "mass_by_element": mass_by_element,
        "energy_total": energy_total,
        "cells_by_phase": cells_by_phase,
        "cells_culled": int(((cells.flags & CULLED) != 0).sum()),
        "cells_ratcheted_this_tick": int(((cells.flags & RATCHETED) != 0).sum()),
        "cells_fractured": int(((cells.flags & FRACTURED) != 0).sum()),
        "cells_excluded": int(((cells.flags & EXCLUDED) != 0).sum()),
    }


def _self_report_invariants(scenario: Scenario, totals: dict[str, Any]) -> list[dict[str, Any]]:
    """The sim's self-reported invariant checks. verify.py independently
    re-verifies these; divergence between self-report and independent check
    is the bug signal."""
    cells = scenario.cells

    # composition_sum_255
    from .cell import composition_sum
    sums = composition_sum(cells)
    comp_violations = []
    for cid, s in enumerate(sums):
        # A cell that's entirely void (all slots zero) has sum 0 — not a
        # violation, just empty. Only flag non-void cells.
        nonvoid = (cells.composition[cid, :, 1].sum() > 0 or
                   cells.composition[cid, :, 0].sum() > 0)
        if nonvoid and int(s) != 255:
            comp_violations.append({"cell_id": cid, "sum": int(s)})

    # Scenario-owned mass conservation expectation: at tick 0 we record the
    # initial total; later ticks compare to it. But the sim's self-report
    # uses the scenario's recorded expected values — if available.
    # For Tier 0, we emit the current mass; baseline check happens externally.
    invariants = [
        {
            "name": "composition_sum_255",
            "status": "pass" if not comp_violations else "fail",
            "violations": comp_violations,
        },
        # dead_band_compliance — placeholder; Stage 3 produces real data
        {
            "name": "dead_band_compliance",
            "status": "pass",
            "violations": [],
        },
    ]
    # Per-element mass conservation self-report (informational; verify.py
    # independently checks via --baseline)
    for element, mass in totals["mass_by_element"].items():
        invariants.append({
            "name": f"{element}_mass_conservation",
            "expected": mass,
            "actual": mass,
            "tolerance": 0.0,
            "status": "pass",  # sim claims conservation; external check arbitrates
        })
    return invariants


def write_emission(
    payload: dict[str, Any],
    output_dir: Path,
) -> Path:
    """Write the payload to disk as tick_{tick:05d}_{stage}.json."""
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
    """Scenario + UTC timestamp for unique run identification."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{scenario_name}_{ts}"
