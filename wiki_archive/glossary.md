# Glossary

Quick definitions for terms used throughout the wiki and code. Cross-links into the detail pages where relevant.

---

**Auction.** The mass-flow mechanism — each cell bids mass to neighbors based on its local stencil read. See [`auction.md`](auction.md).

**Bidder.** A cell acting as a source of a flow. Every cell is a bidder every sub-iteration; whether it has excess to distribute depends on its state vs. dead-band.

**Bid-ignorant capacity check.** A bidder decides whether to place a bid based only on the target's *pre-iteration* state, ignoring other concurrent bidders. Enables parallel execution; cavitation is the resulting intentional side effect.

**Bottle.** The sim's outer boundary, implemented as a loop of `NO_FLOW + RADIATES + FIXED_STATE` cells (or whatever flag combo the scenario chooses). See [`walls.md`](walls.md).

**Buoyancy.** Dense material sinks, light material rises. In this sim, emerges from the `ρ_element × Φ` term in μ — different elements have different density contributions to μ, so iron flows down through water in the same pass water flows up. See [`mass-flow.md`](mass-flow.md).

---

**Cavitation.** Temporary over-accumulation of mass/pressure at a cell receiving converging bids. Physically correct (real fluid phenomenon); resolves over subsequent sub-iterations via the cell becoming a bidder outward. See [`overflow.md`](overflow.md).

**Cell struct.** Per-cell stored state, ~16 bytes. See [`cell-struct.md`](cell-struct.md).

**Chemical potential (μ).** The cost function that drives mass flow. Combines pressure, gravitational potential, concentration, cohesion barrier, and magnetic contribution into a single scalar per (cell, element). Mass flows down μ gradient. See [`mass-flow.md`](mass-flow.md).

**CFL.** Courant-Friedrichs-Lewy stability criterion: for explicit time integration, `v · dt / Δx < 1`. Relevant because our dt is chosen for visual/hardware reasons, not for CFL-correct acoustic propagation. See [`dt-and-units.md`](dt-and-units.md).

**Cohesion.** The topological property of same-material solid cells being bonded. Gives rocks their structural integrity. Implicit — recomputed each tick. See [`cohesion.md`](cohesion.md).

**Compound alias.** A shorthand element ID (200+) that expands to a multi-element composition at cell init. Water = `(H, 114) + (O, 141)` for example. See [`element-table.md`](element-table.md).

**Convection.** Heat transport coupled to mass transport. When Stage 3 moves mass, that mass carries its thermal energy. Handled automatically in Stage 4. See [`energy-flow.md`](energy-flow.md).

**Conduction.** Heat transport via a T gradient between adjacent cells. Stage 4's primary mechanism. See [`energy-flow.md`](energy-flow.md).

**Convergence budget.** Per-phase max sub-iterations per tick: gas ≤3, liquid ≤5, solid ≤7. See [`convergence.md`](convergence.md).

**CULLED.** Flag bit set on a cell that didn't converge within its budget this sub-iteration. Carries over. See [`flags.md`](flags.md), [`convergence.md`](convergence.md).

**Curie temperature.** Temperature above which a ferromagnetic material loses its magnetization. See [`magnetism.md`](magnetism.md).

---

**Dead-band.** The range of pressure values in which a cell is considered "at rest" — doesn't bid excess. Centered on the phase/material's equilibrium pressure. See [`auction.md`](auction.md).

**Delta buffer.** Scratch memory that holds per-direction, per-element flow deltas during a sub-iteration. Reconciled at end-of-sub-iteration. See [`auction.md`](auction.md), [`mass-flow.md`](mass-flow.md).

**Derive stage.** Stage 0 — compute all derived fields (Φ, cohesion, T, B, μ) for this tick. No state change. See [`pipeline.md`](pipeline.md).

**Derived field.** A per-cell scalar/vector that's recomputed each tick from stored state. Never persistent. Includes Φ, T, B, μ, cohesion map. See [`derived-fields.md`](derived-fields.md).

**Dissolution.** Reverse of precipitation: a liquid undersaturated in some element draws that element from an adjacent solid surface. Same solubility table, reversed direction. See [`precipitation.md`](precipitation.md).

**dt.** Sim time per tick. Currently `1/128 s = 7.8125 ms`. See [`dt-and-units.md`](dt-and-units.md).

---

**Elastic strain.** Reversible deformation of a solid cell. Stored as signed i8. Decays back to zero when load is removed (springback). If it exceeds elastic_limit, plastic ratchet fires; if it exceeds tensile_limit, fracture fires. See [`elastic-flow.md`](elastic-flow.md).

**Element table.** The material database, SI units, NIST-sourced. Every material constant. See [`element-table.md`](element-table.md).

**EXCLUDED.** Flag bit set when a cell hits Tier 3 overflow (numeric saturation). Frozen until neighborhood resolves enough to rejoin. See [`flags.md`](flags.md), [`overflow.md`](overflow.md).

**Eulerian.** Grid-based where cell positions are fixed. The VerdantSim invariant: cells don't move, properties do. Contrast with Lagrangian (particle-based, particles move). See [`framework.md`](framework.md).

---

**Fickian diffusion.** Mass flow down a concentration gradient: `J = -D ∇c`. The μ framework includes a Fickian-like term in the concentration/solubility contribution. See [`mass-flow.md`](mass-flow.md).

**FIXED_STATE.** Flag bit: this cell's state never changes. Used for walls, held-T sources, drains. See [`flags.md`](flags.md), [`walls.md`](walls.md).

**Flow primitive.** One of the three fundamental flow types: mass, energy, elastic. Each has its own cost term and convergence budget but the same Jacobi-sweep shape. See [`framework.md`](framework.md).

**FRACTURED.** Flag bit: this solid cell has been broken by tensile failure. Loses cohesion bonds. Acts as a downward bidder (avalanche). See [`flags.md`](flags.md), [`elastic-flow.md`](elastic-flow.md).

---

**G_sim.** Sim-scaled gravitational constant. Not real G (too weak for sim-scale behavior). Scenario-tunable. See [`gravity.md`](gravity.md).

**Gauss-Seidel.** An iteration method like Jacobi but where each cell reads already-updated neighbor values. Can converge faster but harder to parallelize. We use Jacobi (all cells read from pre-sub-iter snapshot). See [`auction.md`](auction.md).

**Gravity.** Derived field `Φ` computed via Poisson Jacobi. General — works for arbitrary mass distributions. See [`gravity.md`](gravity.md).

---

**Hex grid.** Six-neighbor hexagonal cell grid. Axial coordinates (q, r). Bring-up substrate is a 91-cell hex disc (5 rings from center).

---

**INSULATED.** Flag bit: no heat conduction across this cell's boundary. Used for thermal isolation. See [`flags.md`](flags.md).

**Invariant.** A mathematical property that must hold every tick (mass conservation, energy conservation, composition_sum_255, etc.). The verifier independently checks invariants vs. the sim's self-report. See [`debug-harness.md`](debug-harness.md).

---

**Jacobi iteration.** Parallel iteration where all cells read from the previous-iteration snapshot and write to a new buffer. Converges slower than Gauss-Seidel but is naturally parallel. All propagate stages here use Jacobi. See [`auction.md`](auction.md).

---

**Latent heat.** Energy absorbed or released during a phase transition without changing T. Handled via the **shedding rule**: a cell transitioning doesn't partial-phase itself; it sheds the converted material to a fluid neighbor. See [`phase-transitions.md`](phase-transitions.md).

**Log-scale pressure.** u16 pressure encoding where `pressure_raw = mantissa × phase_scale × mohs_multiplier^(level-1)`. Concentrates resolution within each phase. See [`cell-struct.md`](cell-struct.md).

---

**μ (mu).** Chemical potential. See *Chemical potential*.

**Magnetization.** Stored i8 per cell. Signed scalar magnetic moment. Zero above Curie. See [`magnetism.md`](magnetism.md).

**Microstencil.** The 7-point local read pattern (self + 6 neighbors). Every bidder reads only its own microstencil; no global reads. See [`auction.md`](auction.md).

**Mohs level.** Hardness grade 1–10 for solids. Ratcheting increments monotonically within a tick. Determines per-cell dead-band pressure center exponentially. See [`cell-struct.md`](cell-struct.md), [`phase-transitions.md`](phase-transitions.md).

---

**NIST.** National Institute of Standards and Technology. Source of reference material constants. See [`element-table.md`](element-table.md).

**NO_FLOW.** Flag bit: mass cannot cross this cell's boundary. Used for walls. See [`flags.md`](flags.md).

---

**Overflow cascade.** Three-tier response to pressure/energy exceeding limits: Tier 1 cavitation, Tier 2 P↔U coupling, Tier 3 refund + EXCLUDED. See [`overflow.md`](overflow.md).

---

**P↔U coupling.** Rule that converts excess pressure to heat (and vice versa for rarefaction → cooling) when limits are approached. Unifies adiabatic compression, ratchet heating, and numeric-ceiling protection. See [`overflow.md`](overflow.md).

**Phase.** solid / liquid / gas / plasma. 2-bit field in cell struct. Determined by Stage 1 from `(P, U, composition)`. See [`cell-struct.md`](cell-struct.md), [`phase-transitions.md`](phase-transitions.md).

**Phase diagram.** Per-element (and per-composition) map of `(P, U) → phase`. Used in Stage 1. See [`phase-transitions.md`](phase-transitions.md).

**Plastic (deformation).** Irreversible ratcheting of a solid when compressive strain exceeds elastic_limit. See [`elastic-flow.md`](elastic-flow.md), [`phase-transitions.md`](phase-transitions.md).

**Poisson equation.** `∇²Φ = source_term`. Used for gravity (source = ρ) and magnetic field. Solved by Jacobi iteration. See [`gravity.md`](gravity.md).

**Precipitation.** Composition-driven phase change: a supersaturated liquid deposits excess dissolved element onto an adjacent solid surface (or crystallizes in-place). See [`precipitation.md`](precipitation.md).

**Propagate stage.** The three flow passes (Stage 2 elastic, Stage 3 mass, Stage 4 energy). See [`pipeline.md`](pipeline.md).

---

**RADIATES.** Flag bit: cell emits blackbody radiation to scenario `T_space` at Stage 4. See [`flags.md`](flags.md), [`energy-flow.md`](energy-flow.md).

**Ratcheting.** Mohs-level increment triggered by compression beyond elastic_limit. Monotonic within a tick. Dumps compression work to energy field. See [`phase-transitions.md`](phase-transitions.md).

**RATCHETED.** Flag bit: set for the tick on which ratcheting fires. Cleared at tick end. Telemetry/debug only. See [`flags.md`](flags.md).

**Reconcile stage.** Stage 5 — apply delta buffer to stored state, run overflow cascade, apply refunds, clear scratch. See [`pipeline.md`](pipeline.md).

**Refund.** Tier 3 overflow response: route mass/energy that couldn't be placed back to the bidders that sent it. Rare path. See [`overflow.md`](overflow.md).

**Remanence.** Residual magnetization left in a ferromagnet after the applied field is removed. See [`magnetism.md`](magnetism.md).

**Resolve stage.** Stage 1 — phase transitions, ratcheting, Curie demag, precipitation events. Emits deltas for downstream passes. See [`pipeline.md`](pipeline.md), [`phase-transitions.md`](phase-transitions.md).

---

**Scenario.** A specific physics setup: grid shape, initial cell states, walls, `G_sim`, rate multipliers, emission config. Each scenario is a reproducible physics fixture. See [`../PLAN.md`](../PLAN.md).

**Scratch buffer.** Per-tick (or per-sub-iteration) VRAM allocated for derived fields and flow deltas. Cleared in Stage 5c. See [`derived-fields.md`](derived-fields.md).

**Schema v1.** The JSON format all three harness components agree on. See `../ARCHITECTURE.md` and [`debug-harness.md`](debug-harness.md).

**Shell theorem.** In a radially symmetric mass distribution, gravity at radius r only depends on mass enclosed within r. Basis for the radial-approximation gravity option (not used; we use Poisson instead).

**Solubility.** Per-phase, per-host-element, per-dissolved-element limit. Excess precipitates out, deficit dissolves in. See [`precipitation.md`](precipitation.md).

**Springback.** Reversible recovery of elastic strain when load is removed. The behavior of a rock briefly compressed below yield and then released. See [`elastic-flow.md`](elastic-flow.md).

**Stencil.** Local read pattern — self + immediate neighbors. In a hex grid, a 7-point stencil (self + 6 neighbors). See *Microstencil*.

**Sub-iteration.** One pass within a propagate stage. A tick has up to 7 sub-iterations (for solid cells). See [`convergence.md`](convergence.md).

---

**T_space.** Scenario-configured temperature representing the external environment that RADIATES cells emit to. Typically 2.7 K (deep space) but can be higher for near-star scenarios. See [`walls.md`](walls.md), [`energy-flow.md`](energy-flow.md).

**Tensile limit.** Strain magnitude at which a solid fractures. Per-material constant. See [`elastic-flow.md`](elastic-flow.md), [`element-table.md`](element-table.md).

**Tier ladder.** Staged element rollout: Tier 0 = Si only, Tier 1 = + H₂O, Tier 2 = + C/Fe, etc. See [`element-table.md`](element-table.md), [`../PLAN.md`](../PLAN.md).

**Thermodynamic coupling.** Per-phase coefficient that determines how much pressure excess converts to energy (and vice versa). Gas has high coupling (adiabatic compression heating); solid has low (stores as strain). See [`overflow.md`](overflow.md).

**Tick.** The fundamental time unit. Currently 1/128 s of sim time. One tick runs the full pipeline once. See [`dt-and-units.md`](dt-and-units.md).

---

**u16 / u8 / i8.** Standard fixed-width unsigned/signed integer types. Pressure and energy are u16 (stored) → decoded to SI via per-material scales. Composition fractions are u8 (out of 255). Strain and magnetization are i8 (signed).

**Unified flow.** The design choice that all mass movement (diffusion, buoyancy, precipitation, cohesion-locked rigidity, magnetic attraction) emerges from one auction pass with different terms in μ. See [`framework.md`](framework.md).

---

**verify.py.** The invariant checker. See [`debug-harness.md`](debug-harness.md).

**Void cell.** A cell with phase = gas and ~zero mass/pressure/energy. Represents vacuum. A RADIATES cell adjacent to void emits to `T_space`.

---

**Wall.** A cell flagged with combinations of `NO_FLOW`, `INSULATED`, `RADIATES`, `FIXED_STATE`. Real cell, not metadata. See [`walls.md`](walls.md).
