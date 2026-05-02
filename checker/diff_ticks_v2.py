"""
diff_ticks_v2 — schema-v2 cell-by-cell emission comparator.

Schema-v2-aware cousin of checker/diff_ticks.py. Same authorship pattern
(per-field tolerances, exit codes, JSON-report mode), with the gen5 cell
shape:

  exact:           pressure_raw, energy_raw, mohs_level, flags,
                   petal_topology, composition (element, fraction) pairs,
                   identity (phase, element)
  rel-tol 1e-6:    phase_fraction[4], phase_mass[4], pressure_decoded?
                   (omitted from emissions for now), temperature_K,
                   sustained_overpressure
  rel-tol 1e-5:    petal_stress[6], petal_velocity[6,2],
                   gravity_vec[2], cohesion[6]   (more permissive — these
                   are derived/working state subject to f32 accumulation)

Compatibility checks: schema_version, scenario, element_table_hash, grid
cell_count. Refuses to compare a v1 emission against a v2 emission
(schema_version mismatch).

Exit codes match the v1 conventions:
  0 — identical within tolerance
  1 — differs
  2 — incompatible (schema/hash/scenario mismatch)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REL_TOL_TIGHT = 1e-6     # phase_fraction, phase_mass, T, sustained_overpressure
REL_TOL_LOOSE = 1e-5     # petal stress/velocity, gravity, cohesion


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _compatible(a: dict, b: dict) -> list[str]:
    issues = []
    if a.get("schema_version") != 2 or b.get("schema_version") != 2:
        issues.append(
            f"schema_version mismatch: {a.get('schema_version')} vs {b.get('schema_version')} "
            "(diff_ticks_v2 only handles schema-v2)"
        )
    if a.get("element_table_hash") != b.get("element_table_hash"):
        issues.append(
            f"element_table_hash differs: {a.get('element_table_hash')!r} vs {b.get('element_table_hash')!r}"
        )
    if a.get("scenario") != b.get("scenario"):
        issues.append(f"scenario differs: {a.get('scenario')!r} vs {b.get('scenario')!r}")
    grid_a = a.get("grid", {}); grid_b = b.get("grid", {})
    if grid_a.get("cell_count") != grid_b.get("cell_count"):
        issues.append(
            f"cell_count differs: {grid_a.get('cell_count')} vs {grid_b.get('cell_count')}"
        )
    return issues


def _rel_close(x: float, y: float, rel_tol: float) -> bool:
    if x == y:
        return True
    scale = max(abs(x), abs(y))
    if scale < 1e-12:
        return abs(x - y) < 1e-12
    return abs(x - y) / scale <= rel_tol


def _list_rel_close(a: list, b: list, rel_tol: float) -> bool:
    if len(a) != len(b):
        return False
    return all(_rel_close(float(x), float(y), rel_tol) for x, y in zip(a, b))


def _diff_cell(cell_a: dict, cell_b: dict) -> list[dict]:
    diffs: list[dict] = []
    cid = cell_a.get("id", cell_b.get("id"))

    # ---- exact-match scalars ----
    for field in ("pressure_raw", "energy_raw", "mohs_level"):
        if cell_a.get(field) != cell_b.get(field):
            diffs.append({"cell_id": cid, "field": field,
                          "a": cell_a.get(field), "b": cell_b.get(field)})

    # ---- composition: exact list of (element, fraction) pairs ----
    comp_a = sorted([tuple(p) for p in cell_a.get("composition", [])])
    comp_b = sorted([tuple(p) for p in cell_b.get("composition", [])])
    if comp_a != comp_b:
        diffs.append({"cell_id": cid, "field": "composition", "a": comp_a, "b": comp_b})

    # ---- identity: exact ----
    if cell_a.get("identity") != cell_b.get("identity"):
        diffs.append({"cell_id": cid, "field": "identity",
                      "a": cell_a.get("identity"), "b": cell_b.get("identity")})

    # ---- flags: exact ----
    if cell_a.get("flags") != cell_b.get("flags"):
        diffs.append({"cell_id": cid, "field": "flags",
                      "a": cell_a.get("flags"), "b": cell_b.get("flags")})

    # ---- tight rel-tol: phase_fraction[4], phase_mass[4], temperature_K, sustained_overpressure ----
    for field in ("phase_fraction", "phase_mass"):
        a_v = cell_a.get(field); b_v = cell_b.get(field)
        if a_v is None and b_v is None:
            continue
        if (a_v is None) != (b_v is None) or not _list_rel_close(a_v, b_v, REL_TOL_TIGHT):
            diffs.append({"cell_id": cid, "field": field, "a": a_v, "b": b_v})

    for field in ("temperature_K", "sustained_overpressure"):
        a_v = cell_a.get(field); b_v = cell_b.get(field)
        if a_v is None and b_v is None:
            continue
        if (a_v is None) != (b_v is None) or not _rel_close(float(a_v), float(b_v), REL_TOL_TIGHT):
            diffs.append({"cell_id": cid, "field": field, "a": a_v, "b": b_v})

    # ---- loose rel-tol: gravity_vec, cohesion ----
    for field in ("gravity_vec", "cohesion"):
        a_v = cell_a.get(field); b_v = cell_b.get(field)
        if a_v is None and b_v is None:
            continue
        if (a_v is None) != (b_v is None) or not _list_rel_close(a_v, b_v, REL_TOL_LOOSE):
            diffs.append({"cell_id": cid, "field": field, "a": a_v, "b": b_v})

    # ---- petals: per-petal exact topology, loose rel-tol on stress/velocity ----
    petals_a = cell_a.get("petals"); petals_b = cell_b.get("petals")
    if petals_a is not None and petals_b is not None:
        for d, (pa, pb) in enumerate(zip(petals_a, petals_b)):
            if pa.get("topology") != pb.get("topology"):
                diffs.append({"cell_id": cid, "field": f"petal[{d}].topology",
                              "a": pa.get("topology"), "b": pb.get("topology")})
            if not _rel_close(float(pa.get("stress", 0)), float(pb.get("stress", 0)), REL_TOL_LOOSE):
                diffs.append({"cell_id": cid, "field": f"petal[{d}].stress",
                              "a": pa.get("stress"), "b": pb.get("stress")})
            if not _list_rel_close(pa.get("velocity", [0, 0]),
                                   pb.get("velocity", [0, 0]),
                                   REL_TOL_LOOSE):
                diffs.append({"cell_id": cid, "field": f"petal[{d}].velocity",
                              "a": pa.get("velocity"), "b": pb.get("velocity")})

    return diffs


def diff_emissions(a: dict, b: dict) -> dict[str, Any]:
    incompat = _compatible(a, b)
    if incompat:
        return {"status": "incompatible", "issues": incompat}
    cells_a = a.get("cells", []); cells_b = b.get("cells", [])
    if len(cells_a) != len(cells_b):
        return {"status": "incompatible",
                "issues": [f"cell array length: {len(cells_a)} vs {len(cells_b)}"]}
    all_diffs: list[dict] = []
    for ca, cb in zip(cells_a, cells_b):
        all_diffs.extend(_diff_cell(ca, cb))
    return {
        "status": "differs" if all_diffs else "identical",
        "diffs": all_diffs,
        "cells_compared": len(cells_a),
    }


def format_report(rep: dict, a_path: Path, b_path: Path) -> str:
    if rep["status"] == "incompatible":
        return "INCOMPATIBLE:\n  " + "\n  ".join(rep["issues"])
    if rep["status"] == "identical":
        return f"IDENTICAL: {rep['cells_compared']} cells match within tolerance"
    lines = [f"DIFFERS: {len(rep['diffs'])} differences across {rep['cells_compared']} cells"]
    for d in rep["diffs"][:20]:
        lines.append(f"  cell {d['cell_id']:3d} {d['field']:24s}  a={d.get('a')!r}  b={d.get('b')!r}")
    if len(rep["diffs"]) > 20:
        lines.append(f"  ... and {len(rep['diffs']) - 20} more")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Diff two schema-v2 emissions.")
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--json-report", action="store_true")
    args = ap.parse_args()

    try:
        a = _load(args.a)
        b = _load(args.b)
    except Exception as e:
        print(f"SCHEMA ERROR: {e}", file=sys.stderr)
        return 2

    rep = diff_emissions(a, b)
    if args.json_report:
        print(json.dumps(rep, indent=2))
    else:
        print(format_report(rep, args.a, args.b))

    if rep["status"] == "incompatible":
        return 2
    if rep["status"] == "differs":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
