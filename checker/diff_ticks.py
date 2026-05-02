"""
diff_ticks — cell-by-cell JSON emission comparator.

Loads two schema-v1 emissions and walks their cells in id order, reporting
any field difference above the per-field tolerance. Used for:
  - Regression testing: each scenario has a "golden" emission; the current
    sim's output for that scenario must diff-match the golden within
    tolerance, or the regression fails.
  - Cross-validation (eventual): the CUDA port and the Python reference
    should produce diff-match output for the same scenario.

Field tolerances (per Claude Code Handoff Brief §M4):
  pressure_raw:      exact (u16 — any drift is a bug)
  pressure_decoded:  float relative 1e-6
  energy:            float relative 1e-6
  composition:       exact on (element, fraction) pairs
  flags:             exact
  mohs_level:        exact
  elastic_strain:    exact (i8)
  magnetization:     exact (i8)

Exit codes:
  0 — identical within tolerance
  1 — at least one cell differs
  2 — schema or compatibility mismatch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REL_TOL = 1e-6


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _compatible(a: dict, b: dict) -> list[str]:
    issues = []
    if a.get("schema_version") != b.get("schema_version"):
        issues.append(f"schema_version differs: {a.get('schema_version')} vs {b.get('schema_version')}")
    if a.get("element_table_hash") != b.get("element_table_hash"):
        issues.append(
            f"element_table_hash differs: {a.get('element_table_hash')!r} vs {b.get('element_table_hash')!r}"
        )
    if a.get("scenario") != b.get("scenario"):
        issues.append(f"scenario differs: {a.get('scenario')!r} vs {b.get('scenario')!r}")
    grid_a, grid_b = a.get("grid", {}), b.get("grid", {})
    if grid_a.get("cell_count") != grid_b.get("cell_count"):
        issues.append(
            f"cell_count differs: {grid_a.get('cell_count')} vs {grid_b.get('cell_count')}"
        )
    return issues


def _rel_close(x: float, y: float, rel_tol: float = REL_TOL) -> bool:
    """True if |x − y| ≤ rel_tol × max(|x|, |y|), with a small absolute floor
    so two zeros compare equal."""
    if x == y:
        return True
    scale = max(abs(x), abs(y))
    if scale < 1e-12:
        return abs(x - y) < 1e-12
    return abs(x - y) / scale <= rel_tol


def _diff_cell(cell_a: dict, cell_b: dict) -> list[dict]:
    """Compare two cell dicts. Returns a list of per-field difference records."""
    diffs: list[dict] = []
    cid = cell_a.get("id", cell_b.get("id"))

    # Exact-match scalar fields
    for field in ("pressure_raw", "phase", "mohs_level", "elastic_strain", "magnetization"):
        if cell_a.get(field) != cell_b.get(field):
            diffs.append({"cell_id": cid, "field": field,
                          "a": cell_a.get(field), "b": cell_b.get(field)})

    # Float-tolerance fields
    for field in ("pressure_decoded", "energy"):
        va = cell_a.get(field)
        vb = cell_b.get(field)
        if va is None or vb is None:
            if va != vb:
                diffs.append({"cell_id": cid, "field": field, "a": va, "b": vb})
            continue
        if not _rel_close(float(va), float(vb)):
            diffs.append({"cell_id": cid, "field": field,
                          "a": va, "b": vb,
                          "rel_diff": abs(float(va) - float(vb)) / max(abs(float(va)), abs(float(vb)), 1e-12)})

    # composition: exact list of (element, fraction) pairs
    comp_a = sorted([tuple(p) for p in cell_a.get("composition", [])])
    comp_b = sorted([tuple(p) for p in cell_b.get("composition", [])])
    if comp_a != comp_b:
        diffs.append({"cell_id": cid, "field": "composition",
                      "a": comp_a, "b": comp_b})

    # flags: exact dict equality
    if cell_a.get("flags") != cell_b.get("flags"):
        diffs.append({"cell_id": cid, "field": "flags",
                      "a": cell_a.get("flags"), "b": cell_b.get("flags")})

    return diffs


def diff_emissions(a: dict, b: dict) -> dict[str, Any]:
    """Compare two emission payloads. Returns a report dict."""
    incompat = _compatible(a, b)
    if incompat:
        return {"status": "incompatible", "issues": incompat}

    cells_a = a.get("cells", [])
    cells_b = b.get("cells", [])
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
        lines.append(f"  cell {d['cell_id']:3d} {d['field']:15s}  a={d.get('a')!r}  b={d.get('b')!r}")
    if len(rep["diffs"]) > 20:
        lines.append(f"  ... and {len(rep['diffs']) - 20} more")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Diff two schema-v1 emissions.")
    ap.add_argument("a", type=Path, help="first emission JSON")
    ap.add_argument("b", type=Path, help="second emission JSON")
    ap.add_argument("--json-report", action="store_true",
                    help="emit machine-readable JSON")
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
