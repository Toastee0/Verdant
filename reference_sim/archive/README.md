# Archive

Superseded files kept for historical reference.

## `sim_stub.py`

The original schema-reference stub simulator that predates the real reference
sim. It produced synthetic "plausibly physical" tick evolution in ~200 lines
of Python so the debug harness (viewer, verify.py) could be built and tested
before the real sim existed.

As of M3 it's superseded by `reference_sim/sim.py` and the scenario fixtures
in `reference_sim/scenarios/`. The real sim emits the same schema, passes
the same verifier, and actually implements the staged Jacobi auction.

Kept here rather than deleted because:
- Historical reference for how schema-v1 was first produced.
- The `sample_data/tick_*.json` files in the repo root were emitted by this
  stub; keeping the source close by helps explain those artifacts.
- The manual scenario-authoring pattern in `main()` is a decent template
  for quick one-off tests if the real sim framework ever gets in the way.

Do not extend this. New scenarios go in `reference_sim/scenarios/`.
