"""
regression — run every Tier 0 scenario and diff against its golden emission.

Drives the reference sim end-to-end:
  1. Runs each registered scenario for its required tick count.
  2. Verifies tick-N invariants against tick-0 baseline (mass conservation,
     composition sums, etc.) via checker/verify.py.
  3. Diffs the chosen golden tick against the recorded `golden/<scenario>.json`
     via checker/diff_ticks.py.

Failing diff or failing verify → regression failure (non-zero exit).

Run from repo root:
    python -m checker.regression
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "golden"
RUNS_DIR = REPO_ROOT / "runs" / "regression"


@dataclass(frozen=True)
class ScenarioCheck:
    name: str
    module: str           # importable scenario module
    tick_count: int       # how many ticks to run
    golden_tick: int      # which tick is the regression-diff golden


SCENARIOS: tuple[ScenarioCheck, ...] = (
    ScenarioCheck("t0_static",      "reference_sim.scenarios.t0_static",      5, 5),
    ScenarioCheck("t0_compression", "reference_sim.scenarios.t0_compression", 5, 5),
    ScenarioCheck("t0_ratchet",     "reference_sim.scenarios.t0_ratchet",     3, 1),
    ScenarioCheck("t0_fracture",    "reference_sim.scenarios.t0_fracture",    3, 1),
    ScenarioCheck("t0_radiate",     "reference_sim.scenarios.t0_radiate",     5, 5),
)


def _run_scenario(check: ScenarioCheck, output_dir: Path) -> bool:
    cmd = [
        sys.executable, "-m", "reference_sim.sim", check.module,
        "--ticks", str(check.tick_count),
        "--output", str(output_dir),
        "--quiet",
    ]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [run]    FAIL: {result.stderr}", file=sys.stderr)
        return False
    return True


def _verify(check: ScenarioCheck, output_dir: Path) -> bool:
    target = output_dir / f"tick_{check.tick_count:05d}_post_stage_5.json"
    baseline = output_dir / "tick_00000_initial.json"
    cmd = [
        sys.executable, "-m", "checker.verify",
        str(target), "--baseline", str(baseline),
    ]
    # checker.verify is a script-style module; invoke via its file path
    cmd = [sys.executable, str(REPO_ROOT / "checker" / "verify.py"),
           str(target), "--baseline", str(baseline)]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [verify] FAIL (exit {result.returncode}):")
        print("    " + result.stdout.replace("\n", "\n    "))
        return False
    return True


def _diff_golden(check: ScenarioCheck, output_dir: Path) -> bool:
    golden = GOLDEN_DIR / f"{check.name}_tick_{check.golden_tick:05d}.json"
    actual = output_dir / f"tick_{check.golden_tick:05d}_post_stage_5.json"
    if not golden.exists():
        print(f"  [diff]   FAIL: golden missing at {golden}")
        return False
    if not actual.exists():
        print(f"  [diff]   FAIL: actual missing at {actual}")
        return False
    cmd = [sys.executable, str(REPO_ROOT / "checker" / "diff_ticks.py"),
           str(golden), str(actual)]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [diff]   FAIL (exit {result.returncode}):")
        print("    " + result.stdout.replace("\n", "\n    "))
        return False
    return True


def regression_run() -> int:
    print(f"Regression: {len(SCENARIOS)} scenarios")
    print(f"  golden dir: {GOLDEN_DIR}")
    print(f"  runs dir:   {RUNS_DIR}")
    print()

    if RUNS_DIR.exists():
        shutil.rmtree(RUNS_DIR)

    failed: list[str] = []
    for check in SCENARIOS:
        print(f"== {check.name} (tick_count={check.tick_count}, golden={check.golden_tick})")
        out = RUNS_DIR / check.name
        ok_run = _run_scenario(check, out)
        if not ok_run:
            failed.append(f"{check.name}: run failed")
            continue
        ok_verify = _verify(check, out)
        if not ok_verify:
            failed.append(f"{check.name}: verify failed")
        ok_diff = _diff_golden(check, out)
        if not ok_diff:
            failed.append(f"{check.name}: diff failed")
        if ok_verify and ok_diff:
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
