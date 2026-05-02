"""
Top-level driver for the gen5 reference simulator.

Cycle structure per gen5 §"Cycle structure":

  1. Promotion pass (tiered memory) — out of scope for the Python reference.
  2. Sub-pass loop, per phase concurrently — gas: 3, liquid: 5, solid: 7,
     plasma: 3. Each sub-pass:
        a. derive (identity, cohesion, T, gravity-if-due)
        b. region kernels in parallel (per-cell 7-flower compute)
        c. veto stage (filter impossible fluxes against hard constraints)
        d. blind sum into the per-edge flux scratch
        e. integrate (apply incoming - outgoing per cell)
        f. swap canonical / scratch buffers
  3. Re-encoding f32 working state to canonical packed.
  4. Render sync (consumer-driven; not gated here).

For M5'.0 only step 2's outer loop is wired; the per-sub-pass body is a
no-op. Each milestone fills in one piece (M5'.1: derive; M5'.3: region
kernel + flux; M5'.4: scheduler; M5'.5: phase transitions; etc.).

Usage:
    python -m reference_sim_v2.sim <scenario_module> [--ticks N] [--output DIR]
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

from .derive import DerivedFields, run_derive
from .emit import emit_cycle, new_run_id, write_emission
from .flux import FluxBuffer, apply_veto, integrate
from .region import run_region_kernels
from .scenario import Scenario


# Gen5 universals (verdant_sim_design.md §"Concurrent phase sub-passes")
PHASE_BUDGETS = {
    "solid":  7,
    "liquid": 5,
    "gas":    3,
    "plasma": 3,
}
LONGEST_BUDGET = max(PHASE_BUDGETS.values())   # 7


def run_scenario(
    scenario: Scenario,
    ticks: int,
    run_id: str | None = None,
    verbose: bool = True,
) -> list[Path]:
    """Run a scenario for N ticks. Each tick is one full cycle (the
    `LONGEST_BUDGET` sub-pass loop). Tick 0 is the initial state, emitted
    before the first cycle runs."""
    if run_id is None:
        run_id = new_run_id(scenario.name)

    # Per-cycle scratch (allocated once, reused).
    derived = DerivedFields.allocate(scenario.cells.n)
    flux = FluxBuffer.allocate(scenario.cells.n)

    emitted: list[Path] = []

    # Tick 0 — initial state baseline. Run derive so the emission carries
    # identity / cohesion / T / gravity_vec for the initial state.
    run_derive(scenario.cells, scenario.element_table, scenario.world, derived)
    if scenario.emission.mode != "off" and scenario.emission.output_dir is not None:
        payload = emit_cycle(
            scenario,
            tick=0, cycle=0, sub_pass=0,
            stage="initial",
            run_id=run_id,
            derived=derived,
        )
        path = write_emission(payload, scenario.emission.output_dir)
        emitted.append(path)
        if verbose:
            print(f"[tick 0]  initial state  -> {path}")

    for tick in range(1, ticks + 1):
        cycle_timing: dict[str, float] = {}
        t_cycle_start = time.perf_counter()

        # ---- one cycle = LONGEST_BUDGET sub-passes -----------------------
        for sub_pass in range(LONGEST_BUDGET):
            t_sp = time.perf_counter()
            _run_sub_pass(scenario, sub_pass, derived, flux)
            cycle_timing[f"sub_pass_{sub_pass}_ms"] = (time.perf_counter() - t_sp) * 1000.0

        cycle_timing["cycle_total_ms"] = (time.perf_counter() - t_cycle_start) * 1000.0

        # Emit at end of cycle (post-integration, all sub-passes complete)
        if scenario.emission.mode != "off" and scenario.emission.output_dir is not None:
            payload = emit_cycle(
                scenario,
                tick=tick, cycle=tick, sub_pass=LONGEST_BUDGET - 1,
                stage="post_integration",
                run_id=run_id,
                derived=derived,
                cycle_timing_ms=cycle_timing,
            )
            path = write_emission(payload, scenario.emission.output_dir)
            emitted.append(path)
            if verbose:
                print(f"[tick {tick}]  cycle={cycle_timing['cycle_total_ms']:.2f}ms  -> {path.name}")

    return emitted


def _run_sub_pass(
    scenario: Scenario,
    sub_pass: int,
    derived: DerivedFields,
    flux: FluxBuffer,
) -> None:
    """Body of one sub-pass within a cycle.

    M5'.3 wiring:
      1. derive (every sub-pass — identity, cohesion, T, pressure, gravity)
      2. flux.clear()
      3. region kernels — compute per-cell outgoing mass flux
      4. veto — zero out fluxes across out-of-grid / NO_FLOW / etc edges
      5. integrate — apply incoming - outgoing per cell to canonical state

    Future milestones:
      M5'.4 — phase-aware active-set (run only cells whose dominant phase's
              budget hasn't expired this cycle); cull noise-floor regions
      M5'.5 — phase transitions in transitions.py; sustained-overpressure
              ratchet; energy-flux convection coupling
      M5'.6 — Tail-at-Scale culling formal; per-channel borders; petal
              stress + velocity integrate from momentum/stress flux
    """
    run_derive(scenario.cells, scenario.element_table, scenario.world, derived)
    flux.clear()
    run_region_kernels(scenario.cells, derived, scenario.world, flux)
    apply_veto(scenario.cells, flux)
    integrate(scenario.cells, flux, scenario.world)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a VerdantSim gen5 scenario.")
    ap.add_argument("scenario", type=str,
                    help="Scenario module, e.g. reference_sim_v2.scenarios.g5_static")
    ap.add_argument("--ticks", type=int, default=3, help="Number of cycles to run")
    ap.add_argument("--output", type=Path, default=None,
                    help="Output directory for JSON emissions")
    ap.add_argument("--emission", type=str, default="tick",
                    choices=["off", "tick", "sub_pass", "violation"],
                    help="Emission granularity")
    ap.add_argument("--quiet", action="store_true",
                    help="Suppress per-tick progress prints")
    args = ap.parse_args(argv)

    try:
        mod = importlib.import_module(args.scenario)
    except ImportError as e:
        print(f"Could not import scenario module {args.scenario!r}: {e}", file=sys.stderr)
        return 1
    if not hasattr(mod, "build"):
        print(f"Scenario module {args.scenario!r} has no `build()` function", file=sys.stderr)
        return 1

    scenario = mod.build(output_dir=args.output, emission_mode=args.emission)
    emitted = run_scenario(scenario, ticks=args.ticks, verbose=not args.quiet)

    print(f"\nRun complete: {len(emitted)} emissions")
    if emitted:
        print(f"  first: {emitted[0]}")
        print(f"  last:  {emitted[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
