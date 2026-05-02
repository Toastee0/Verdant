"""
Smoke tests for checker/diff_ticks_v2.py.

Run from repo root:  python -m checker.test_diff_ticks_v2

Lightweight assertions, no pytest dependency.
"""

from __future__ import annotations

import sys

from .diff_ticks_v2 import diff_emissions


def _emission(cells, **overrides):
    base = {
        "schema_version": 2,
        "scenario": "test",
        "element_table_hash": "sha256:test",
        "grid": {"cell_count": len(cells)},
        "cells": cells,
    }
    base.update(overrides)
    return base


def _flags():
    return {"no_flow": False, "radiates": False, "insulated": False,
            "fixed_state": False, "culled": False, "fractured": False,
            "ratcheted_this_tick": False, "excluded": False}


def _topology():
    return {"is_border": False, "is_grid_edge": False, "is_inert": False,
            "border_type": None}


def _petal(d, stress=0.0, velocity=None):
    return {"direction": d, "stress": stress,
            "velocity": velocity if velocity is not None else [0.0, 0.0],
            "topology": _topology()}


def _cell(cid: int, **overrides):
    base = {
        "id": cid,
        "composition": [["Si", 255]],
        "phase_fraction": [1.0, 0.0, 0.0, 0.0],
        "phase_mass": [74088.0, 0.0, 0.0, 0.0],
        "pressure_raw": 0,
        "energy_raw": 300,
        "mohs_level": 6,
        "sustained_overpressure": 0.0,
        "identity": {"phase": "solid", "element": "Si"},
        "flags": _flags(),
        "petals": [_petal(d) for d in range(6)],
    }
    base.update(overrides)
    return base


def _expect(actual: str, expected: str) -> None:
    assert actual == expected, f"expected {expected!r}, got {actual!r}"


def test_identical() -> None:
    a = _emission([_cell(0), _cell(1)])
    b = _emission([_cell(0), _cell(1)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "identical")


def test_pressure_raw_exact() -> None:
    a = _emission([_cell(0, pressure_raw=100)])
    b = _emission([_cell(0, pressure_raw=101)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")


def test_phase_fraction_tight_tolerance() -> None:
    # Within tight tolerance — allowed
    a = _emission([_cell(0, phase_fraction=[1.0, 0.0, 0.0, 0.0])])
    b = _emission([_cell(0, phase_fraction=[1.0 + 1e-9, 0.0, 0.0, 0.0])])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "identical")


def test_phase_fraction_drift_caught() -> None:
    a = _emission([_cell(0, phase_fraction=[1.0, 0.0, 0.0, 0.0])])
    b = _emission([_cell(0, phase_fraction=[0.9, 0.1, 0.0, 0.0])])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")


def test_phase_mass_drift_caught() -> None:
    a = _emission([_cell(0, phase_mass=[74088.0, 0.0, 0.0, 0.0])])
    b = _emission([_cell(0, phase_mass=[74000.0, 88.0, 0.0, 0.0])])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")


def test_temperature_tight_tolerance() -> None:
    a = _emission([_cell(0, temperature_K=300.0)])
    b = _emission([_cell(0, temperature_K=300.0 + 1e-7)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "identical")


def test_identity_exact() -> None:
    a = _emission([_cell(0, identity={"phase": "solid", "element": "Si"})])
    b = _emission([_cell(0, identity={"phase": "liquid", "element": "Si"})])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")


def test_petal_stress_loose_tolerance() -> None:
    # Within 1e-5 — allowed
    p_a = [_petal(d, stress=1.0) for d in range(6)]
    p_b = [_petal(d, stress=1.0 + 1e-7) for d in range(6)]
    a = _emission([_cell(0, petals=p_a)])
    b = _emission([_cell(0, petals=p_b)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "identical")


def test_petal_topology_exact() -> None:
    p_a = [_petal(d) for d in range(6)]
    p_b_topo = {"is_border": True, "is_grid_edge": False,
                "is_inert": False, "border_type": None}
    p_b = [_petal(d) for d in range(6)]
    p_b[3]["topology"] = p_b_topo
    a = _emission([_cell(0, petals=p_a)])
    b = _emission([_cell(0, petals=p_b)])
    rep = diff_emissions(a, b)
    _expect(rep["status"], "differs")


def test_incompatible_schema_v1_v2() -> None:
    a = _emission([_cell(0)], schema_version=1)
    b = _emission([_cell(0)], schema_version=2)
    rep = diff_emissions(a, b)
    _expect(rep["status"], "incompatible")


def test_incompatible_cell_count() -> None:
    a = _emission([_cell(0), _cell(1)])
    b = _emission([_cell(0)], **{"grid": {"cell_count": 1}})
    rep = diff_emissions(a, b)
    _expect(rep["status"], "incompatible")


TESTS = [
    test_identical,
    test_pressure_raw_exact,
    test_phase_fraction_tight_tolerance,
    test_phase_fraction_drift_caught,
    test_phase_mass_drift_caught,
    test_temperature_tight_tolerance,
    test_identity_exact,
    test_petal_stress_loose_tolerance,
    test_petal_topology_exact,
    test_incompatible_schema_v1_v2,
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
        print(f"\n{len(failed)}/{len(TESTS)} tests failed")
        return 1
    print(f"\n{len(TESTS)}/{len(TESTS)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
