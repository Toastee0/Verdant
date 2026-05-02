"""
Smoke tests for checker/diff_ticks.py.

Run from repo root:
    python -m checker.test_diff_ticks

No test framework dependency — these are lightweight assertions that exit
non-zero on failure, suitable for CI without pytest.
"""

from __future__ import annotations

import sys

from .diff_ticks import diff_emissions


def _emission(cells, **overrides):
    base = {
        "schema_version": 1,
        "scenario": "test",
        "element_table_hash": "sha256:test",
        "grid": {"cell_count": len(cells)},
        "cells": cells,
    }
    base.update(overrides)
    return base


def _cell(cid: int, **overrides):
    base = {
        "id": cid,
        "phase": "solid",
        "mohs_level": 5,
        "pressure_raw": 0,
        "pressure_decoded": 0.0,
        "energy": 300,
        "composition": [["Si", 255]],
        "flags": {"no_flow": False, "radiates": False, "insulated": False,
                  "fixed_state": False, "culled": False, "fractured": False,
                  "ratcheted_this_tick": False, "excluded": False},
        "elastic_strain": 0,
        "magnetization": 0,
    }
    base.update(overrides)
    return base


def _expect(actual: str, expected: str) -> None:
    assert actual == expected, f"expected {expected!r}, got {actual!r}"


def test_identical_emissions() -> None:
    a = _emission([_cell(0), _cell(1)])
    b = _emission([_cell(0), _cell(1)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "identical")
    assert rep["diffs"] == []


def test_differs_on_pressure_raw() -> None:
    a = _emission([_cell(0, pressure_raw=100)])
    b = _emission([_cell(0, pressure_raw=101)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")
    assert any(d["field"] == "pressure_raw" for d in rep["diffs"])


def test_float_tolerance_allows_small_drift() -> None:
    a = _emission([_cell(0, energy=300, pressure_decoded=1.0)])
    b = _emission([_cell(0, energy=300, pressure_decoded=1.0 + 1e-9)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "identical")


def test_float_tolerance_catches_real_drift() -> None:
    a = _emission([_cell(0, pressure_decoded=1.0)])
    b = _emission([_cell(0, pressure_decoded=1.5)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")
    assert any(d["field"] == "pressure_decoded" for d in rep["diffs"])


def test_composition_exact() -> None:
    a = _emission([_cell(0, composition=[["Si", 255]])])
    b = _emission([_cell(0, composition=[["Si", 254]])])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")


def test_flags_exact() -> None:
    base = _cell(0)
    fa = {**base["flags"], "fractured": True}
    a = _emission([_cell(0, flags=fa)])
    b = _emission([_cell(0)])  # default flags (fractured=False)
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")


def test_incompatible_schema_version() -> None:
    a = _emission([_cell(0)], schema_version=1)
    b = _emission([_cell(0)], schema_version=2)
    rep = diff_emissions(a, b)
    _expect(rep["status"], "incompatible")


def test_incompatible_cell_count() -> None:
    a = _emission([_cell(0), _cell(1)])
    b = _emission([_cell(0)])
    # cell_count comes from grid; override
    b["grid"] = {"cell_count": 1}
    rep = diff_emissions(a, b)
    _expect(rep["status"], "incompatible")


TESTS = [
    test_identical_emissions,
    test_differs_on_pressure_raw,
    test_float_tolerance_allows_small_drift,
    test_float_tolerance_catches_real_drift,
    test_composition_exact,
    test_flags_exact,
    test_incompatible_schema_version,
    test_incompatible_cell_count,
]


def main() -> int:
    failed: list[str] = []
    for t in TESTS:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed.append(f"{t.__name__}: {e}")
            print(f"  FAIL  {t.__name__}: {e}")
    if failed:
        print(f"\n{len(failed)}/{len(TESTS)} test(s) failed")
        return 1
    print(f"\n{len(TESTS)}/{len(TESTS)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
