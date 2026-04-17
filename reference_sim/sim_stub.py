"""
VERDANT Debug Harness — Stub Simulator
---------------------------------------
Minimal Python reference that emits schema-v1 JSON for the viewer and checker.

This is NOT the gen 4 sim. It's ~200 lines that sets up a Mohs-1 Si cell
dropping onto a Mohs-5 Si floor, runs 3 fake ticks, and writes JSON that
passes the checker. Purpose: validate the schema end-to-end before the
real sim exists.

Scenario: t0_silicon_drop
  91-cell hex disc, all Si, all solid.
  One "dropped" cell at (0, -3) is Mohs 1.
  Floor cells are Mohs 5.
  Over 3 ticks, the dropped cell's pressure rises and it ratchets to Mohs 2.
  Mass and energy conserved throughout.

Author: Adrian Neill (via Claude)
"""

import json
import hashlib
import math
from pathlib import Path
from datetime import datetime, timezone


# ---------- Element table (stub: just Si for Tier 0) ----------

ELEMENT_TABLE = {
    "Si": {
        "Z": 14,
        "name": "Silicon",
        "molar_mass": 28.085,
        "melt_K": 1687,
        "boil_K": 3538,
        "mohs_max": 7,          # crystalline Si ~6.5, round to 7
        "density": 2.33,
        "conductivity": 149,
        # Derived phase centers (placeholders; real derivation in gen 4)
        "gas_center": 1_000,
        "liquid_center": 10_000,
        "solid_center_mohs_1": 30_000,
        "mohs_multiplier": 1.5,
    }
}

def element_table_hash() -> str:
    blob = json.dumps(ELEMENT_TABLE, sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]


# ---------- Hex grid generation ----------

def hex_disc_coords(rings: int):
    """Yield axial (q, r) coordinates for a hex disc with N rings."""
    for q in range(-rings, rings + 1):
        r_min = max(-rings, -q - rings)
        r_max = min(rings, -q + rings)
        for r in range(r_min, r_max + 1):
            yield (q, r)


# ---------- Pressure encoding (log scale, per phase) ----------

def decode_pressure(raw: int, phase: str, mohs_level: int = 1) -> float:
    """Decode a u16 raw pressure value into absolute pressure units."""
    mantissa = raw & 0x0FFF  # bits 0-11
    if phase == "gas":
        return float(mantissa)
    elif phase == "liquid":
        return float(mantissa * 8)
    elif phase == "solid":
        multiplier = ELEMENT_TABLE["Si"]["mohs_multiplier"]
        return float(mantissa * 8) * (multiplier ** (mohs_level - 1))
    elif phase == "plasma":
        return float(mantissa * 64)
    return 0.0


# ---------- Cell factory ----------

def make_cell(cell_id: int, coord: tuple, phase: str, mohs: int,
              pressure_raw: int, energy: int, composition: list) -> dict:
    return {
        "id": cell_id,
        "coord": list(coord),
        "phase": phase,
        "mohs_level": mohs,
        "pressure_raw": pressure_raw,
        "pressure_decoded": decode_pressure(pressure_raw, phase, mohs),
        "energy": energy,
        "composition": composition,
        "flags": {
            "resolved": True,
            "culled": False,
            "fractured": False,
            "ratcheted_this_tick": False,
        },
        "gradient": [0, 0, 0, 0, 0, 0],
        "bids_sent": [],
        "bids_received": [],
    }


# ---------- Scenario: t0_silicon_drop ----------

def build_initial_state():
    cells = []
    for idx, (q, r) in enumerate(hex_disc_coords(5)):
        # The "dropped" cell is at (0, -3): Mohs 1, slightly compressed
        if (q, r) == (0, -3):
            cell = make_cell(idx, (q, r), "solid", mohs=1,
                             pressure_raw=100, energy=300,
                             composition=[["Si", 255]])
        else:
            # Floor cells: Mohs 5, at dead-band center
            cell = make_cell(idx, (q, r), "solid", mohs=5,
                             pressure_raw=0, energy=300,
                             composition=[["Si", 255]])
        cells.append(cell)
    return cells


# ---------- Stub "simulation" step ----------

def step(cells: list, tick: int):
    """
    Fake a tick. We're not running the real physics — we're producing
    schema-valid data with plausible evolution so the viewer/checker
    have something to chew on.

    Tick 1: dropped cell accumulates a bit of pressure from "impact".
    Tick 2: dropped cell's pressure rises further.
    Tick 3: dropped cell ratchets from Mohs 1 to Mohs 2, pressure resets.
    """
    for cell in cells:
        # Clear per-tick flags
        cell["flags"]["ratcheted_this_tick"] = False
        cell["bids_sent"] = []
        cell["bids_received"] = []

        if cell["coord"] == [0, -3]:
            if tick == 1:
                cell["pressure_raw"] = 400
            elif tick == 2:
                cell["pressure_raw"] = 900
            elif tick == 3:
                # Ratchet: Mohs 1 -> Mohs 2
                old_pressure_abs = decode_pressure(cell["pressure_raw"], "solid", cell["mohs_level"])
                cell["mohs_level"] = 2
                # New raw pressure encodes to (old_absolute / new_multiplier)
                multiplier = 1.5 ** (cell["mohs_level"] - 1)
                cell["pressure_raw"] = int(old_pressure_abs / (8 * multiplier))
                cell["flags"]["ratcheted_this_tick"] = True
                # Ratcheting dumps compression work into energy
                cell["energy"] += 50

            # Recompute decoded pressure for the current state
            cell["pressure_decoded"] = decode_pressure(
                cell["pressure_raw"], cell["phase"], cell["mohs_level"]
            )


# ---------- Totals & invariants ----------

def compute_totals(cells: list) -> dict:
    mass_by_element = {}
    energy_total = 0.0
    cells_by_phase = {"solid": 0, "liquid": 0, "gas": 0, "plasma": 0}
    cells_culled = 0
    cells_ratcheted = 0

    for cell in cells:
        # Each cell contributes mass_per_cell; here simplified to just sum fractions
        # (in the real sim this would be mass × composition)
        for element, frac in cell["composition"]:
            # Pretend each cell has 255 units of total mass; fraction is out of 255
            mass_contribution = frac  # so composition [[Si, 255]] = 255 units of Si
            mass_by_element[element] = mass_by_element.get(element, 0) + mass_contribution

        energy_total += cell["energy"]
        cells_by_phase[cell["phase"]] += 1
        if cell["flags"]["culled"]:
            cells_culled += 1
        if cell["flags"]["ratcheted_this_tick"]:
            cells_ratcheted += 1

    return {
        "mass_by_element": mass_by_element,
        "energy_total": energy_total,
        "cells_by_phase": cells_by_phase,
        "cells_culled": cells_culled,
        "cells_ratcheted_this_tick": cells_ratcheted,
    }


def check_invariants(cells: list, totals: dict, expected_mass: dict,
                     expected_energy: float, energy_tolerance: float = 100.0) -> list:
    """
    Produce the `invariants` array in the schema. The sim's self-report —
    the external checker will re-verify these independently.
    """
    invariants = []

    # Conservation per element
    for element, expected in expected_mass.items():
        actual = totals["mass_by_element"].get(element, 0)
        invariants.append({
            "name": f"{element}_mass_conservation",
            "expected": expected,
            "actual": actual,
            "tolerance": 0.0,
            "status": "pass" if actual == expected else "fail",
        })

    # Energy conservation (with tolerance, because ratcheting adds heat)
    invariants.append({
        "name": "energy_conservation",
        "expected": expected_energy,
        "actual": totals["energy_total"],
        "tolerance": energy_tolerance,
        "status": "pass" if abs(totals["energy_total"] - expected_energy) <= energy_tolerance else "fail",
    })

    # Composition sum = 255 per cell
    violations = []
    for cell in cells:
        total = sum(frac for _, frac in cell["composition"])
        if total != 255:
            violations.append({"cell_id": cell["id"], "sum": total})
    invariants.append({
        "name": "composition_sum_255",
        "status": "pass" if not violations else "fail",
        "violations": violations,
    })

    # Dead-band compliance is a Stage-1 check; stub marks all as passing
    invariants.append({
        "name": "dead_band_compliance",
        "status": "pass",
        "violations": [],
    })

    return invariants


# ---------- Emission ----------

def emit(cells: list, tick: int, stage: str, cycle: int, run_id: str,
         output_dir: Path, expected_mass: dict, expected_energy: float):
    totals = compute_totals(cells)
    invariants = check_invariants(cells, totals, expected_mass, expected_energy)

    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "scenario": "t0_silicon_drop",
        "tick": tick,
        "stage": stage,
        "cycle": cycle,
        "grid": {
            "shape": "hex_disc",
            "rings": 5,
            "cell_count": len(cells),
            "coordinate_system": "axial_qr",
        },
        "element_table_hash": element_table_hash(),
        "allowed_elements": ["Si"],
        "cells": cells,
        "totals": totals,
        "invariants": invariants,
        "stage_timing_ms": {
            "stage_1": 0.02,
            "stage_2": 0.08,
            "stage_3a": 0.11,
            "stage_3b": 0.09,
        },
    }

    fname = f"tick_{tick:05d}_{stage}.json"
    fpath = output_dir / fname
    with open(fpath, "w") as f:
        json.dump(payload, f, indent=2)
    return fpath


# ---------- Main ----------

def main():
    output_dir = Path(__file__).parent.parent / "sample_data"
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"t0_silicon_drop_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    cells = build_initial_state()

    # Initial conservation targets: 91 cells × 255 Si = 23205 mass, energy sum from initial state
    expected_mass = {"Si": sum(frac for c in cells for _, frac in c["composition"])}
    expected_energy_initial = sum(c["energy"] for c in cells)

    # Emit tick 0 (initial state)
    path = emit(cells, tick=0, stage="initial", cycle=0, run_id=run_id,
                output_dir=output_dir,
                expected_mass=expected_mass,
                expected_energy=expected_energy_initial)
    print(f"Emitted {path}")

    # Run 3 fake ticks, emit post-stage-3b each
    for tick in [1, 2, 3]:
        step(cells, tick)
        # Energy grows by 50 on tick 3 (ratchet compression work)
        expected_energy = expected_energy_initial + (50 if tick >= 3 else 0)
        path = emit(cells, tick=tick, stage="post_stage_3b", cycle=1, run_id=run_id,
                    output_dir=output_dir,
                    expected_mass=expected_mass,
                    expected_energy=expected_energy)
        print(f"Emitted {path}")

    # Emit one deliberately broken tick for checker testing
    cells[0]["composition"] = [["Si", 200]]  # bad sum (200 not 255)
    path = emit(cells, tick=99, stage="post_stage_3b_violation", cycle=1, run_id=run_id,
                output_dir=output_dir,
                expected_mass=expected_mass,
                expected_energy=expected_energy_initial + 50)
    print(f"Emitted {path}  (deliberate violation for checker test)")


if __name__ == "__main__":
    main()
