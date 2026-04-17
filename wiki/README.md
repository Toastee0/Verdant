# VerdantSim Wiki

Reference knowledge for the VerdantSim physics engine. Organized for walkability — each page is narrow and self-contained, cross-linked where needed. Load the pages you care about, skip the rest.

For the roadmap (what to build next, milestones), see `../PLAN.md`.
For the debug harness (schema, viewer, verify), see `../ARCHITECTURE.md` and `debug-harness.md`.

---

## Start here

- [`framework.md`](framework.md) — the big picture: stored fields, derived fields, flows, the four-phase tick pipeline. **Read this first** if you're new to the design.
- [`glossary.md`](glossary.md) — quick definitions: Jacobi, μ, cavitation, ratcheting, dead-band, etc.

## Core data model

- [`cell-struct.md`](cell-struct.md) — the ~16 B stored state per cell
- [`flags.md`](flags.md) — the u8 flag field and its semantics (walls, CULLED, FRACTURED, EXCLUDED, …)
- [`derived-fields.md`](derived-fields.md) — Φ, T, B, μ — recomputed each frame, never stored

## Pipeline

- [`pipeline.md`](pipeline.md) — stage order: Derive → Resolve → Propagate → Reconcile
- [`convergence.md`](convergence.md) — per-phase iteration budgets; CULLED vs EXCLUDED
- [`dt-and-units.md`](dt-and-units.md) — 1 tick = 1/128 s; SI units throughout; CFL notes

## Flow mechanics

- [`auction.md`](auction.md) — the Jacobi bidding rules; sub-iteration semantics; cavitation between ticks
- [`overflow.md`](overflow.md) — three-tier cascade: cavitation → P↔U coupling → refund + EXCLUDED
- [`mass-flow.md`](mass-flow.md) — Stage 3, the unified μ-gradient flow. Diffusion, gravity, buoyancy, precipitation, cohesion — all one pass.
- [`energy-flow.md`](energy-flow.md) — Stage 4, T-gradient with conduction, convection, radiation
- [`elastic-flow.md`](elastic-flow.md) — Stage 2, stress propagation through cohesion network; pressure-wave / sound

## State-change mechanics

- [`phase-transitions.md`](phase-transitions.md) — Stage 1; latent-heat shedding; ratcheting; Curie demag
- [`precipitation.md`](precipitation.md) — solubility-driven deposition and dissolution; stalactites, caves
- [`cohesion.md`](cohesion.md) — implicit same-material solid bonds; support chains, stalactite tips
- [`walls.md`](walls.md) — walls as real cells with flag combos

## Field-specific

- [`gravity.md`](gravity.md) — Φ from Poisson-via-Jacobi; works for arbitrary mass distributions
- [`magnetism.md`](magnetism.md) — scalar magnetization per cell; B field via Poisson; Curie; hysteresis

## Reference data

- [`element-table.md`](element-table.md) — required columns, Tier ladder, sourcing
- [`debug-harness.md`](debug-harness.md) — schema-v1, viewer, verify.py, cross-validation plan

---

## How to use this wiki

**For design questions** ("why does mass flow down μ, not down pressure?"): start at `framework.md`, then `mass-flow.md`, then follow the μ term you're curious about.

**For implementation questions** ("how is the cell struct laid out?"): `cell-struct.md` + `flags.md`.

**For scenario authoring** ("how do I set up a radiative boundary?"): `walls.md` + `energy-flow.md`.

**For debugging** ("what does EXCLUDED mean?"): `flags.md` + `overflow.md`.

**For CUDA porting** (later): `pipeline.md`, `auction.md`, and `debug-harness.md` together.

---

## Maintenance

When the framework changes, update the affected wiki page(s) **in the same change** that updates the code. Drift is a bug.

When adding a new mechanism (e.g. electrostatics, phase chemistry, new flow primitive), add a new wiki page for it, link it from this README under the right section, and update `framework.md` if it adds to the flow primitives or pipeline stages.
