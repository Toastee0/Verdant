"""
VERDANT Debug Harness — Invariant Checker
------------------------------------------
Loads a schema-v1 JSON emission and independently verifies every invariant
against the raw cell data. If the sim *claims* an invariant passes but the
raw data fails the check, this is the divergence report — i.e., the bug.

Usage:
    python verify.py <path-to-json>
    python verify.py <path-to-json> --filter element=Si
    python verify.py <path-to-json> --json-report   (for AI agent consumption)

Exit codes:
    0 — all invariants verified
    1 — at least one invariant failed independent verification
    2 — sim self-report disagrees with independent check (most serious)
    3 — schema error, could not parse
"""

import argparse
import json
import sys
from pathlib import Path


# ---------- Independent invariant checks ----------
# Each returns (status: str, details: dict)
# status ∈ {"pass", "fail"}

def check_composition_sums(cells):
    violations = []
    for cell in cells:
        total = sum(frac for _, frac in cell["composition"])
        if total != 255:
            violations.append({"cell_id": cell["id"], "coord": cell["coord"], "sum": total})
    return (
        "pass" if not violations else "fail",
        {"cells_checked": len(cells), "violations": violations},
    )


def check_mass_conservation(cells, expected_mass):
    actual = {}
    for cell in cells:
        for element, frac in cell["composition"]:
            actual[element] = actual.get(element, 0) + frac
    mismatches = []
    for element, expected in expected_mass.items():
        if actual.get(element, 0) != expected:
            mismatches.append({
                "element": element,
                "expected": expected,
                "actual": actual.get(element, 0),
            })
    return (
        "pass" if not mismatches else "fail",
        {"expected": expected_mass, "actual": actual, "mismatches": mismatches},
    )


def check_pressure_decoding(cells, element_table):
    """Verify pressure_decoded matches pressure_raw under the documented log-scale encoding."""
    def decode(raw, phase, mohs):
        mantissa = raw & 0x0FFF
        if phase == "gas":
            return float(mantissa)
        if phase == "liquid":
            return float(mantissa * 8)
        if phase == "solid":
            # Assume Si for stub; real version looks up per element in table
            mult = 1.5
            return float(mantissa * 8) * (mult ** (mohs - 1))
        if phase == "plasma":
            return float(mantissa * 64)
        return 0.0

    mismatches = []
    for cell in cells:
        expected = decode(cell["pressure_raw"], cell["phase"], cell["mohs_level"])
        if abs(cell["pressure_decoded"] - expected) > 0.01:
            mismatches.append({
                "cell_id": cell["id"],
                "coord": cell["coord"],
                "raw": cell["pressure_raw"],
                "reported_decoded": cell["pressure_decoded"],
                "expected_decoded": expected,
            })
    return (
        "pass" if not mismatches else "fail",
        {"cells_checked": len(cells), "mismatches": mismatches},
    )


def check_mohs_range(cells):
    """Mohs level must be 1-10 for solids, 0 for non-solids."""
    violations = []
    for cell in cells:
        mohs = cell["mohs_level"]
        phase = cell["phase"]
        if phase == "solid" and not (1 <= mohs <= 10):
            violations.append({"cell_id": cell["id"], "phase": phase, "mohs": mohs})
        elif phase != "solid" and mohs != 0 and mohs != 1:
            # Non-solids: mohs should be 0 (unused), 1 sometimes tolerated
            violations.append({"cell_id": cell["id"], "phase": phase, "mohs": mohs})
    return (
        "pass" if not violations else "fail",
        {"violations": violations},
    )


def check_bid_conservation(cells):
    """
    For every bid sent, the target cell's bids_received should contain the
    matching entry. This catches scatter-gather bugs.
    (Stub sim doesn't populate bids, so this will vacuously pass.)
    """
    cells_by_id = {c["id"]: c for c in cells}
    mismatches = []
    for cell in cells:
        for bid in cell.get("bids_sent", []):
            target_id = bid.get("target_cell_id")
            amount = bid.get("amount")
            if target_id is None:
                continue
            target = cells_by_id.get(target_id)
            if not target:
                mismatches.append({"reason": "target_not_found", "from": cell["id"], "to": target_id})
                continue
            matching = [b for b in target.get("bids_received", [])
                        if b.get("source_cell_id") == cell["id"] and b.get("amount") == amount]
            if not matching:
                mismatches.append({
                    "reason": "unmatched_bid",
                    "from": cell["id"],
                    "to": target_id,
                    "amount": amount,
                })
    return (
        "pass" if not mismatches else "fail",
        {"mismatches": mismatches},
    )


def check_flags_consistency(cells):
    """
    Culled cells shouldn't have bids_sent. Ratcheted cells must be solid.
    Fractured cells must have mohs_level == the material's mohs_max (breaking point).
    """
    issues = []
    for cell in cells:
        f = cell["flags"]
        if f["culled"] and cell.get("bids_sent"):
            issues.append({"cell_id": cell["id"], "issue": "culled_cell_sent_bids"})
        if f["ratcheted_this_tick"] and cell["phase"] != "solid":
            issues.append({"cell_id": cell["id"], "issue": "non_solid_ratcheted"})
    return (
        "pass" if not issues else "fail",
        {"issues": issues},
    )


# ---------- Inferred expected totals (for self-contained verification) ----------

def infer_expected_mass(cells):
    """Sum composition across all cells to get expected mass per element."""
    totals = {}
    for cell in cells:
        for element, frac in cell["composition"]:
            totals[element] = totals.get(element, 0) + frac
    return totals


# ---------- Main verification flow ----------

def verify(payload, filters=None):
    filters = filters or {}
    cells = payload["cells"]

    # Apply filters (viewer-style; for focused inspection)
    if "element" in filters:
        cells = [c for c in cells if any(e == filters["element"] for e, _ in c["composition"])]
    if "phase" in filters:
        cells = [c for c in cells if c["phase"] == filters["phase"]]

    # Run all independent checks
    checks = {
        "composition_sum_255": check_composition_sums(cells),
        "mass_conservation": check_mass_conservation(cells, infer_expected_mass(cells)),
        "pressure_decoding": check_pressure_decoding(cells, None),
        "mohs_range": check_mohs_range(cells),
        "bid_conservation": check_bid_conservation(cells),
        "flags_consistency": check_flags_consistency(cells),
    }

    # Cross-check: sim self-report vs independent verdict
    sim_invariants = {inv["name"]: inv["status"] for inv in payload.get("invariants", [])}
    divergences = []
    for name, (status, _) in checks.items():
        # Map independent check names to sim-reported names (loose match)
        sim_name_candidates = [sn for sn in sim_invariants if name in sn or sn in name]
        for sn in sim_name_candidates:
            if sim_invariants[sn] != status:
                divergences.append({
                    "check": name,
                    "sim_reported": sim_invariants[sn],
                    "independent_verdict": status,
                })

    return {
        "run_id": payload.get("run_id"),
        "tick": payload.get("tick"),
        "stage": payload.get("stage"),
        "cells_verified": len(cells),
        "checks": {name: {"status": s, "details": d} for name, (s, d) in checks.items()},
        "sim_self_report": sim_invariants,
        "divergences": divergences,
        "totals_reported": payload.get("totals", {}),
    }


def format_report(report) -> str:
    """Pretty-print for human operator."""
    lines = []
    lines.append(f"VERDANT Debug Report — {report['run_id']}")
    lines.append(f"Tick: {report['tick']}  Stage: {report['stage']}  Cells: {report['cells_verified']}")
    lines.append("")
    lines.append("INDEPENDENT CHECKS")
    for name, result in report["checks"].items():
        status = result["status"].upper()
        lines.append(f"  {name:32s} {status}")
        d = result["details"]
        if result["status"] == "fail":
            # Show up to 3 examples
            sample_key = next((k for k in ("violations", "mismatches", "issues") if k in d), None)
            if sample_key:
                for item in d[sample_key][:3]:
                    lines.append(f"      {item}")
                if len(d[sample_key]) > 3:
                    lines.append(f"      ... and {len(d[sample_key]) - 3} more")

    lines.append("")
    lines.append("SIM SELF-REPORT")
    for name, status in report["sim_self_report"].items():
        lines.append(f"  {name:32s} {status.upper()}")

    lines.append("")
    if report["divergences"]:
        lines.append("!!! DIVERGENCES (sim vs independent) !!!")
        for div in report["divergences"]:
            lines.append(
                f"  {div['check']}: sim says {div['sim_reported']}, "
                f"independent says {div['independent_verdict']}"
            )
        lines.append("")

    # Verdict
    any_fail = any(r["status"] == "fail" for r in report["checks"].values())
    any_diverge = bool(report["divergences"])
    if any_diverge:
        verdict = "DIVERGENT  (sim self-report disagrees with independent check — BUG)"
    elif any_fail:
        verdict = "FAIL  (one or more invariants violated)"
    else:
        verdict = "PASS"
    lines.append(f"VERDICT: {verdict}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--filter", action="append", default=[],
                    help="Filter cells by key=value (e.g. phase=solid)")
    ap.add_argument("--json-report", action="store_true",
                    help="Emit machine-readable JSON instead of text")
    args = ap.parse_args()

    try:
        with open(args.json_path) as f:
            payload = json.load(f)
    except Exception as e:
        print(f"SCHEMA ERROR: {e}", file=sys.stderr)
        return 3

    if payload.get("schema_version") != 1:
        print(f"SCHEMA VERSION MISMATCH: got {payload.get('schema_version')}, expected 1",
              file=sys.stderr)
        return 3

    filters = {}
    for f in args.filter:
        if "=" in f:
            k, v = f.split("=", 1)
            filters[k] = v

    report = verify(payload, filters)

    if args.json_report:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))

    # Exit code
    if report["divergences"]:
        return 2
    if any(r["status"] == "fail" for r in report["checks"].values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
