"""
verify_v2 — schema-v2 invariant checker for the gen5 reference simulator.

Independent re-verification of the sim's self-reported invariants. The full
gen5 invariant suite lands incrementally:

  M5'.0  — composition_sum_255, phase_fraction_sum_le_1, mass_per_element_per_phase
           conservation (vs baseline), schema_version, cell_count, petal_count
  M5'.1  — cohesion_in_unit_interval, identity-determinism
  M5'.2  — gravity_field_finite_bounded
  M5'.3  — flux_summation_symmetric_per_edge, vetoed_fluxes_zero, momentum_conservation
  M5'.4  — per-sub-pass conservation
  M5'.5  — mohs_monotonic, sustained_overpressure_decay, latent_heat_energy_balance,
           phase_transition_mass_conservation
  M5'.6  — culled_cells_emit_zero_flux, petal_stress_symmetric_on_intact_bonds,
           border_no_flow_channel_zero_mass, border_insulated_channel_zero_energy,
           fixed_state_cells_unchanged
  M5'.7  — element_table_hash_match_baseline, phase_diagram_hash_match,
           border_table_hash_match

Usage:
    python checker/verify_v2.py <target.json> --baseline <tick_0.json>
    python checker/verify_v2.py <target.json> --json-report

Exit codes:
    0 — all invariants pass (or skipped with warning)
    1 — at least one invariant fails independent verification
    2 — sim self-report disagrees with independent check (DIVERGENT bug signal)
    3 — schema error or unparseable JSON
    4 — baseline incompatible (run_id, element_table_hash, scenario, schema_version)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------------
# Independent checks (M5'.0 subset)
# ----------------------------------------------------------------------------

def check_schema_version(payload: dict) -> tuple[str, dict]:
    v = payload.get("schema_version")
    if v == 2:
        return ("pass", {"schema_version": v})
    return ("fail", {"expected": 2, "actual": v})


def check_composition_sums(cells: list[dict]) -> tuple[str, dict]:
    """Every non-void cell's composition fractions must sum to exactly 255."""
    violations = []
    for cell in cells:
        comp = cell.get("composition", [])
        nonvoid = bool(comp)
        total = sum(int(frac) for _, frac in comp)
        if nonvoid and total != 255:
            violations.append({"cell_id": cell["id"], "sum": total})
    return (
        "pass" if not violations else "fail",
        {"cells_checked": len(cells), "violations": violations},
    )


def check_phase_fraction_sum_le_1(cells: list[dict]) -> tuple[str, dict]:
    """Sum of the four phase fractions must be ≤ 1.0 (vacuum is the complement)."""
    violations = []
    for cell in cells:
        pf = cell.get("phase_fraction", [])
        if len(pf) != 4:
            violations.append({"cell_id": cell["id"], "issue": "phase_fraction must be 4 entries",
                               "actual_len": len(pf)})
            continue
        total = sum(float(x) for x in pf)
        if total > 1.0 + 1e-5:
            violations.append({"cell_id": cell["id"], "sum": total})
    return (
        "pass" if not violations else "fail",
        {"cells_checked": len(cells), "violations": violations},
    )


def check_petal_count(cells: list[dict]) -> tuple[str, dict]:
    """Each cell, when petals are emitted, must have exactly 6 petals (one per
    hex direction). When emission strips petals, the field is absent — skip."""
    violations = []
    cells_with_petals = 0
    for cell in cells:
        petals = cell.get("petals")
        if petals is None:
            continue
        cells_with_petals += 1
        if len(petals) != 6:
            violations.append({"cell_id": cell["id"], "petal_count": len(petals)})
    return (
        "pass" if not violations else "fail",
        {"cells_checked_with_petals": cells_with_petals, "violations": violations},
    )


def check_phase_mass_non_negative(cells: list[dict]) -> tuple[str, dict]:
    violations = []
    for cell in cells:
        pm = cell.get("phase_mass", [])
        for idx, val in enumerate(pm):
            if val < 0.0:
                violations.append({"cell_id": cell["id"], "phase_index": idx, "value": val})
    return (
        "pass" if not violations else "fail",
        {"violations": violations},
    )


def check_temperature_positive(cells: list[dict]) -> tuple[str, dict]:
    """Every cell with mass > 0 must have T > 0 (M5'.1 invariant). Cells
    without `temperature_K` in their JSON are skipped (the field is only
    emitted when derive ran)."""
    violations = []
    cells_checked = 0
    for cell in cells:
        if "temperature_K" not in cell:
            continue
        cells_checked += 1
        T = float(cell["temperature_K"])
        # Mass present? Use phase_mass total > 0 as proxy
        pm = cell.get("phase_mass", [0, 0, 0, 0])
        has_mass = sum(float(x) for x in pm) > 0
        if has_mass and T <= 0:
            violations.append({"cell_id": cell["id"], "T": T,
                               "phase_mass_total": sum(float(x) for x in pm)})
    return (
        "pass" if not violations else "fail",
        {"cells_checked": cells_checked, "violations": violations},
    )


def check_gravity_field_finite_bounded(
    cells: list[dict],
    g_max_bound: float = 1e6,
) -> tuple[str, dict]:
    """Gravity vector at every cell must be finite (no NaN/Inf) and bounded
    by `g_max_bound` (default 1e6 m/s² — well above any realistic scenario).
    Skipped when scenarios don't emit gravity_vec."""
    import math
    violations = []
    cells_checked = 0
    for cell in cells:
        gv = cell.get("gravity_vec")
        if gv is None:
            continue
        cells_checked += 1
        gx, gy = float(gv[0]), float(gv[1])
        if not math.isfinite(gx) or not math.isfinite(gy):
            violations.append({"cell_id": cell["id"], "gx": gx, "gy": gy,
                               "issue": "non-finite"})
            continue
        mag = (gx * gx + gy * gy) ** 0.5
        if mag > g_max_bound:
            violations.append({"cell_id": cell["id"], "magnitude": mag,
                               "issue": f"exceeds bound {g_max_bound}"})
    if cells_checked == 0:
        return ("skipped", {"reason": "no gravity_vec emitted in this scenario"})
    return (
        "pass" if not violations else "fail",
        {"cells_checked": cells_checked, "violations": violations,
         "g_max_bound": g_max_bound},
    )


def check_cohesion_in_unit_interval(cells: list[dict]) -> tuple[str, dict]:
    """Cohesion must be in [0, 1] per cell per direction, and exactly 0
    when the neighbor doesn't exist (grid edge). Skipped when cohesion
    isn't emitted."""
    violations = []
    edge_violations = []
    cells_checked = 0
    for cell in cells:
        if "cohesion" not in cell:
            continue
        cells_checked += 1
        coh = cell["cohesion"]
        petals = cell.get("petals", [])
        for d, c in enumerate(coh):
            cv = float(c)
            if cv < 0.0 or cv > 1.0 + 1e-6:
                violations.append({"cell_id": cell["id"], "direction": d, "value": cv})
            # Grid-edge directions must have cohesion == 0
            if petals and d < len(petals):
                topo = petals[d].get("topology", {})
                if topo.get("is_grid_edge") and cv != 0.0:
                    edge_violations.append({"cell_id": cell["id"], "direction": d,
                                            "value": cv,
                                            "issue": "cohesion non-zero across grid edge"})
    all_v = violations + edge_violations
    return (
        "pass" if not all_v else "fail",
        {"cells_checked": cells_checked, "violations": all_v},
    )


def check_mass_per_element_per_phase(
    cells: list[dict],
    expected: dict[str, dict[str, float]] | None,
) -> tuple[str, dict]:
    """Mass of (element, phase) pair must equal the baseline value, exact.

    Computed independently from cells: per cell, per slot, fraction × phase_mass[p]
    contributes (element_at_slot, phase) to that bucket.
    """
    if expected is None:
        return ("skipped", {"reason": "no baseline provided"})

    actual: dict[str, dict[str, float]] = {}
    for cell in cells:
        comp = cell.get("composition", [])
        pm = cell.get("phase_mass", [0.0, 0.0, 0.0, 0.0])
        for elem, frac in comp:
            f = int(frac) / 255.0
            entry = actual.setdefault(elem, {"solid": 0.0, "liquid": 0.0, "gas": 0.0, "plasma": 0.0})
            for p_name, p_val in zip(("solid", "liquid", "gas", "plasma"), pm):
                entry[p_name] += f * float(p_val)

    mismatches = []
    for elem, expected_per_phase in expected.items():
        for p_name, exp_val in expected_per_phase.items():
            act_val = actual.get(elem, {}).get(p_name, 0.0)
            # Tolerance: f32 round-trip plus accumulation. 1e-4 absolute is
            # generous for the small grids we run.
            if abs(act_val - exp_val) > max(1e-4, abs(exp_val) * 1e-6):
                mismatches.append({"element": elem, "phase": p_name,
                                   "expected": exp_val, "actual": act_val,
                                   "delta": act_val - exp_val})
    return (
        "pass" if not mismatches else "fail",
        {"expected": expected, "actual": actual, "mismatches": mismatches},
    )


# ----------------------------------------------------------------------------
# Baseline + compatibility
# ----------------------------------------------------------------------------

def load_baseline_expected_mass(baseline_path: Path) -> tuple[dict, dict]:
    with baseline_path.open("r", encoding="utf-8") as f:
        baseline = json.load(f)
    totals = baseline.get("totals", {})
    expected = totals.get("mass_by_element_by_phase", {})
    return expected, baseline


def check_baseline_compatible(baseline: dict, target: dict) -> list[str]:
    issues = []
    if baseline.get("schema_version") != target.get("schema_version"):
        issues.append(f"schema_version mismatch: {baseline.get('schema_version')} vs {target.get('schema_version')}")
    if baseline.get("element_table_hash") != target.get("element_table_hash"):
        issues.append("element_table_hash mismatch")
    if baseline.get("scenario") != target.get("scenario"):
        issues.append(f"scenario mismatch: {baseline.get('scenario')!r} vs {target.get('scenario')!r}")
    b_run = baseline.get("run_id")
    t_run = target.get("run_id")
    if b_run and t_run and b_run != t_run:
        issues.append(f"run_id mismatch: {b_run!r} vs {t_run!r}")
    b_tick = baseline.get("tick")
    t_tick = target.get("tick")
    if b_tick is not None and t_tick is not None and b_tick > t_tick:
        issues.append(f"baseline tick ({b_tick}) is after target tick ({t_tick})")
    b_count = baseline.get("grid", {}).get("cell_count")
    t_count = target.get("grid", {}).get("cell_count")
    if b_count != t_count:
        issues.append(f"cell_count mismatch: {b_count} vs {t_count}")
    return issues


# ----------------------------------------------------------------------------
# Verifier driver
# ----------------------------------------------------------------------------

def verify(payload: dict, expected_mass: dict | None = None) -> dict:
    cells = payload.get("cells", [])

    checks = {
        "schema_version_2":            check_schema_version(payload),
        "composition_sum_255":         check_composition_sums(cells),
        "phase_fraction_sum_le_1":     check_phase_fraction_sum_le_1(cells),
        "phase_mass_non_negative":     check_phase_mass_non_negative(cells),
        "petal_count_6":               check_petal_count(cells),
        "temperature_positive":        check_temperature_positive(cells),
        "cohesion_in_unit_interval":   check_cohesion_in_unit_interval(cells),
        "gravity_field_finite_bounded": check_gravity_field_finite_bounded(cells),
        "mass_per_element_per_phase":  check_mass_per_element_per_phase(cells, expected_mass),
    }

    sim_self = {inv["name"]: inv["status"] for inv in payload.get("invariants", [])}
    divergences = []
    for name, (status, _) in checks.items():
        if status == "skipped":
            continue
        sim_candidates = [sn for sn in sim_self if name in sn or sn in name]
        for sn in sim_candidates:
            if sim_self[sn] != status:
                divergences.append({
                    "check": name,
                    "sim_reported": sim_self[sn],
                    "independent_verdict": status,
                })

    return {
        "run_id": payload.get("run_id"),
        "tick":   payload.get("tick"),
        "stage":  payload.get("stage"),
        "cells_verified": len(cells),
        "checks": {n: {"status": s, "details": d} for n, (s, d) in checks.items()},
        "sim_self_report": sim_self,
        "divergences": divergences,
    }


def format_report(rep: dict) -> str:
    lines = [
        f"VERDANT v2 Debug Report — {rep['run_id']}",
        f"Tick: {rep['tick']}  Stage: {rep['stage']}  Cells: {rep['cells_verified']}",
        "",
        "INDEPENDENT CHECKS",
    ]
    for name, result in rep["checks"].items():
        status = result["status"].upper()
        lines.append(f"  {name:36s} {status}")
        d = result["details"]
        if result["status"] == "fail":
            sample_key = next((k for k in ("violations", "mismatches", "issues") if k in d), None)
            if sample_key:
                for item in d[sample_key][:3]:
                    lines.append(f"      {item}")
                if len(d[sample_key]) > 3:
                    lines.append(f"      ... and {len(d[sample_key]) - 3} more")
        elif result["status"] == "skipped":
            lines.append(f"      (skipped: {d.get('reason', 'no reason given')})")

    lines.extend(["", "SIM SELF-REPORT"])
    for name, status in rep["sim_self_report"].items():
        lines.append(f"  {name:36s} {status.upper()}")

    if rep["divergences"]:
        lines.extend(["", "!!! DIVERGENCES (sim vs independent) !!!"])
        for div in rep["divergences"]:
            lines.append(f"  {div['check']}: sim says {div['sim_reported']}, "
                         f"independent says {div['independent_verdict']}")

    skipped_names = [n for n, r in rep["checks"].items() if r["status"] == "skipped"]
    if skipped_names:
        lines.append("")
        lines.append(f"WARNINGS: {len(skipped_names)} check(s) skipped — {', '.join(skipped_names)}")

    any_fail = any(r["status"] == "fail" for r in rep["checks"].values())
    any_div = bool(rep["divergences"])
    if any_div:
        verdict = "DIVERGENT  (sim self-report disagrees with independent check — BUG)"
    elif any_fail:
        verdict = "FAIL  (one or more invariants violated)"
    elif skipped_names:
        verdict = f"PASS with warnings ({len(skipped_names)} check(s) skipped)"
    else:
        verdict = "PASS"
    lines.extend(["", f"VERDICT: {verdict}"])
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify a schema-v2 emission.")
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--baseline", type=Path, default=None,
                    help="Path to a tick-0 baseline JSON for mass-conservation check")
    ap.add_argument("--json-report", action="store_true",
                    help="Emit machine-readable JSON instead of text")
    args = ap.parse_args()

    try:
        with args.json_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        print(f"SCHEMA ERROR: {e}", file=sys.stderr)
        return 3

    if payload.get("schema_version") != 2:
        print(f"SCHEMA VERSION MISMATCH: got {payload.get('schema_version')}, expected 2",
              file=sys.stderr)
        return 3

    expected_mass = None
    if args.baseline is not None:
        try:
            expected_mass, baseline_payload = load_baseline_expected_mass(args.baseline)
        except Exception as e:
            print(f"BASELINE ERROR: could not load {args.baseline}: {e}", file=sys.stderr)
            return 4
        issues = check_baseline_compatible(baseline_payload, payload)
        if issues:
            print("BASELINE INCOMPATIBLE with target:", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)
            return 4

    rep = verify(payload, expected_mass=expected_mass)

    if args.json_report:
        print(json.dumps(rep, indent=2))
    else:
        print(format_report(rep))

    if rep["divergences"]:
        return 2
    if any(r["status"] == "fail" for r in rep["checks"].values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
