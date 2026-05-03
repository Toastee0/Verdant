"""
regression_v2 — gen5 regression runner.

Drives the schema-v2 reference simulator end-to-end:
  1. Runs each registered scenario.
  2. Verifies each tick-N invariant suite against tick-0 baseline via
     checker/verify_v2.py.
  3. Diffs the chosen golden tick against `golden_v2/<scenario>.json` via
     checker/diff_ticks_v2.py.

Failing diff or failing verify → regression failure (non-zero exit).

Run from repo root:
    python -m checker.regression_v2
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "golden_v2"
RUNS_DIR = REPO_ROOT / "runs" / "regression_v2"


@dataclass(frozen=True)
class ScenarioCheck:
    name: str
    module: str
    tick_count: int
    golden_tick: int


SCENARIOS: tuple[ScenarioCheck, ...] = (
    ScenarioCheck("g5_static",             "reference_sim_v2.scenarios.g5_static",             5,  5),
    ScenarioCheck("g5_temp_gradient",      "reference_sim_v2.scenarios.g5_temp_gradient",      5,  5),
    ScenarioCheck("g5_grav_uniform",       "reference_sim_v2.scenarios.g5_grav_uniform",       3,  3),
    ScenarioCheck("g5_grav_two_body",      "reference_sim_v2.scenarios.g5_grav_two_body",      3,  3),
    ScenarioCheck("g5_pressure_drop",      "reference_sim_v2.scenarios.g5_pressure_drop",      5,  5),
    ScenarioCheck("g5_mixed_phase",        "reference_sim_v2.scenarios.g5_mixed_phase",        5,  5),
    ScenarioCheck("g5_melt",               "reference_sim_v2.scenarios.g5_melt",               3,  3),
    ScenarioCheck("g5_ratchet",            "reference_sim_v2.scenarios.g5_ratchet",           30, 30),
    ScenarioCheck("g5_radiative_boundary", "reference_sim_v2.scenarios.g5_radiative_boundary", 5,  5),
    ScenarioCheck("g6_water_static",       "reference_sim_v2.scenarios.g6_water_static",       5,  5),
)


def _run_scenario(check: ScenarioCheck, output_dir: Path) -> bool:
    cmd = [sys.executable, "-m", "reference_sim_v2.sim", check.module,
           "--ticks", str(check.tick_count), "--output", str(output_dir), "--quiet"]
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [run]    FAIL: {r.stderr}", file=sys.stderr)
        return False
    return True


def _verify(check: ScenarioCheck, output_dir: Path) -> bool:
    target = output_dir / f"tick_{check.tick_count:05d}_post_integration.json"
    baseline = output_dir / "tick_00000_initial.json"
    cmd = [sys.executable, str(REPO_ROOT / "checker" / "verify_v2.py"),
           str(target), "--baseline", str(baseline)]
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [verify] FAIL (exit {r.returncode}):")
        print("    " + r.stdout.replace("\n", "\n    "))
        return False
    return True


def _diff_golden(check: ScenarioCheck, output_dir: Path) -> bool:
    golden = GOLDEN_DIR / f"{check.name}_tick_{check.golden_tick:05d}.json"
    actual = output_dir / f"tick_{check.golden_tick:05d}_post_integration.json"
    if not golden.exists():
        print(f"  [diff]   FAIL: golden missing at {golden}")
        return False
    if not actual.exists():
        print(f"  [diff]   FAIL: actual missing at {actual}")
        return False
    cmd = [sys.executable, str(REPO_ROOT / "checker" / "diff_ticks_v2.py"),
           str(golden), str(actual)]
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [diff]   FAIL (exit {r.returncode}):")
        print("    " + r.stdout.replace("\n", "\n    "))
        return False
    return True


def regression_run() -> int:
    print(f"Regression v2: {len(SCENARIOS)} scenarios")
    print(f"  golden dir: {GOLDEN_DIR}")
    print(f"  runs dir:   {RUNS_DIR}")
    print()

    if RUNS_DIR.exists():
        shutil.rmtree(RUNS_DIR)

    failed: list[str] = []
    for check in SCENARIOS:
        print(f"== {check.name} (tick_count={check.tick_count}, golden={check.golden_tick})")
        out = RUNS_DIR / check.name
        if not _run_scenario(check, out):
            failed.append(f"{check.name}: run failed")
            continue
        ok_v = _verify(check, out)
        if not ok_v:
            failed.append(f"{check.name}: verify failed")
        ok_d = _diff_golden(check, out)
        if not ok_d:
            failed.append(f"{check.name}: diff failed")
        if ok_v and ok_d:
            print("  PASS (verify + diff)")
        print()

    print("=" * 60)
    if failed:
        print(f"REGRESSION FAILED: {len(failed)} issue(s)")
        for f in failed:
            print(f"  - {f}")
        return 1
    print(f"REGRESSION PASSED: {len(SCENARIOS)} scenarios green")
    return 0


if __name__ == "__main__":
    sys.exit(regression_run())
