"""
Top-level simulation driver. Ties all the stages together into a tick loop.

Usage:
    python -m reference_sim.sim <scenario_module> [--ticks N] [--output DIR]

Where <scenario_module> is a dotted import path, e.g.
    reference_sim.scenarios.t0_static

Example:
    python -m reference_sim.sim reference_sim.scenarios.t0_static --ticks 5 --output runs/static_001
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

from .derive import DerivedFields, run_derive_stage
from .emit import emit_tick, new_run_id, write_emission
from .propagate import PropagateBuffers, run_propagate_stages
from .reconcile import run_reconcile_stage
from .resolve import run_resolve_stage
from .scenario import Scenario


def run_scenario(
    scenario: Scenario,
    ticks: int,
    run_id: str | None = None,
    verbose: bool = True,
) -> list[Path]:
    """Run a scenario for N ticks. Returns list of emitted JSON file paths.

    Tick 0 is the initial state (emitted before any step). Tick N is the
    post-step state after the Nth step.
    """
    if run_id is None:
        run_id = new_run_id(scenario.name)

    derived = DerivedFields.allocate(scenario.grid)
    buffers = PropagateBuffers.allocate(scenario.cells)

    emitted: list[Path] = []

    # Initial emission (tick 0) — baseline for verification
    if scenario.emission.mode != "off" and scenario.emission.output_dir is not None:
        payload = emit_tick(scenario, derived=None, buffers=None,
                            tick=0, stage="initial", cycle=0,
                            run_id=run_id, stage_timing_ms={})
        path = write_emission(payload, scenario.emission.output_dir)
        emitted.append(path)
        if verbose:
            print(f"[tick 0]  initial state  -> {path}")

    for tick in range(1, ticks + 1):
        timings: dict[str, float] = {}
        buffers.clear()

        t0 = time.perf_counter()
        run_derive_stage(scenario.cells, scenario.element_table, scenario.world, derived)
        t1 = time.perf_counter()
        run_resolve_stage(scenario.cells, scenario.element_table, derived, scenario.world)
        t2 = time.perf_counter()
        run_propagate_stages(scenario.cells, scenario.element_table, derived, buffers, scenario.world)
        t3 = time.perf_counter()
        run_reconcile_stage(scenario.cells, scenario.element_table, buffers, scenario.world)
        t4 = time.perf_counter()

        timings = {
            "stage_0_derive_ms":   (t1 - t0) * 1000.0,
            "stage_1_resolve_ms":  (t2 - t1) * 1000.0,
            "stage_2_4_propagate_ms": (t3 - t2) * 1000.0,
            "stage_5_reconcile_ms":   (t4 - t3) * 1000.0,
        }

        if scenario.emission.mode != "off" and scenario.emission.output_dir is not None:
            payload = emit_tick(scenario, derived=derived, buffers=buffers,
                                tick=tick, stage="post_stage_5", cycle=1,
                                run_id=run_id, stage_timing_ms=timings)
            path = write_emission(payload, scenario.emission.output_dir)
            emitted.append(path)
            if verbose:
                print(f"[tick {tick}]  {_format_timings(timings)}  -> {path.name}")

    return emitted


def _format_timings(timings: dict[str, float]) -> str:
    total = sum(timings.values())
    return f"total={total:5.2f}ms"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a VerdantSim scenario.")
    ap.add_argument("scenario", type=str,
                    help="Scenario module, e.g. reference_sim.scenarios.t0_static")
    ap.add_argument("--ticks", type=int, default=3,
                    help="Number of sim ticks to run (default 3)")
    ap.add_argument("--output", type=Path, default=None,
                    help="Output directory for JSON emissions")
    ap.add_argument("--emission", type=str, default="tick",
                    choices=["off", "tick", "stage", "cycle", "violation"],
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
