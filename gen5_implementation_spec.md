# VerdantSim Gen5 — Implementation Spec (Architectural Commitment Catalog)

**Source document:** `verdant_sim_design.md` (gen5)
**Audience:** Python reference implementation team (this is the source-of-truth list we implement against)
**Scope note:** GPU/CUDA-only patterns are catalogued for completeness but flagged as out-of-scope for the Python reference. The Python reference must reproduce the *physics* commitments verbatim; the *hardware* commitments are deferred to M9 (CUDA port).

This catalog captures every architectural commitment in gen5 — rules, structures, and invariants that an implementation must satisfy to be compliant. Each item carries: the precise rule (**What**), the citation in gen5 (**Where**), the implementation implication (**Implication**), and any flagged ambiguity (**Open question**).

A separate VS-WIKI/SESSION-LOG delta list at the end ranks contradictions between gen5 and the earlier framework (wiki/, SESSION_LOG_2026-04-16.md).

---

## 1. Per-cell state

### 1.1 — Composition vector is 16-slot, not 4-slot
- **What:** Each cell holds a composition vector of 16 `(element_id: u8, fraction: u8)` slots; fractions normalized to sum to 255. Targets the full periodic table (118 elements). When more than 16 species are present, smallest fractions merge into nearest existing slot by element similarity, or into a final "trace" slot.
- **Where in gen5:** §State representation → Per-cell state, lines 243–244.
- **Implication for impl:** A fixed-size `(16, 2)` byte array per cell, or two parallel `uint8` arrays of length 16. NOT the 4-slot `(u8, u8)` layout from SESSION_LOG. Composition merge is a per-cell operation invoked when a 17th distinct species would arrive via flux; needs an "element similarity" metric (deferred — see Open question).
- **Open question:** "Element similarity" for slot merging is not defined. Atomic-number distance? Same column of periodic table? Flag for human decision; for M3 we can use `abs(Z_a − Z_b)` as a placeholder.

### 1.2 — Phase distribution is fractional and includes vacuum as complement
- **What:** Phases are `(solid, liquid, gas, plasma)`, each a fraction in `[0,1]`. They sum to ≤ 1.0. Vacuum fraction is implicit: `vacuum = 1 - (solid + liquid + gas + plasma)`. Vacuum is **not stored**.
- **Where:** §State representation → Per-cell state, line 244; §Vacuum, lines 277–283.
- **Implication for impl:** Four `f32` fields per cell. Vacuum is computed on demand. Single-phase cells are NOT a separate code path — they are simply cells where one phase fraction is 1.0 and others are 0.0. Mixed-phase cells (wet sand, foam, magma) are first-class and use the same data layout.

### 1.3 — Per-phase mass field, separate from phase fraction
- **What:** "Phase-fraction masses: per-phase mass content within the cell. This is the quantity each phase fraction seeks to hold near its phase density equilibrium center."
- **Where:** §State representation → Per-cell state, line 245.
- **Implication for impl:** Four additional `f32` fields per cell — the actual mass-of-gas, mass-of-liquid, mass-of-solid, mass-of-plasma. Distinct from "phase fraction" which is volumetric/notional. Pressure deviates above/below center as a function of mass, not fraction.
- **Open question:** Relationship between phase fraction (`f`) and phase mass (`m`) is not pinned down. Is `f` derived from `m` via a per-element density table, or are both stored independently and consistency-checked? Flag for decision; reference impl will likely store both with `f` derived per-cycle.

### 1.4 — Pressure is log-scale u16 canonical, f32 working, encoded as deviation from phase-density center
- **What:** Pressure stored as log-scale `u16` for grid-resident state. Decoded to `f32` for arithmetic and re-encoded at integration boundaries. Pressure is **deviation from the phase-density equilibrium center** — positive means above center (mass wants to flow out), negative means below (mass wants to flow in).
- **Where:** §State representation → Per-cell state, line 246; equilibrium-center discussion lines 263–274.
- **Implication for impl:** Pressure has a sign. Working state in region kernels is `f32`; encoding/decoding is at canonical-grid boundary. The deviation-from-center semantic means a single scalar pressure does not suffice for a mixed-phase cell: each phase has its own pressure deviation from its own center. This points to **per-phase pressure**, not a single scalar.
- **Open question:** Does pressure decompose per-phase (4 `f32`s) or is there one cell-level pressure that mixes phases? Gen5 says "expressed as deviation from the phase density equilibrium center" (singular). For mixed cells with two centers (e.g., 1,764 for liquid and 42 for gas) we need per-phase pressure. Flag for decision.

### 1.5 — Temperature, energy, and the energy→temperature derivation
- **What:** Temperature stored as absolute K (`f32` working / `u16` encoded). Energy is internal-energy scalar. **Temperature is derived from energy + heat capacity + composition**, not stored independently.
- **Where:** §State representation → Per-cell state, lines 247–248.
- **Implication for impl:** Energy is the canonical state field. Temperature is a computed view. Heat capacity comes from the element table weighted by composition. This means the element table must carry per-element specific heat capacity as a first-class field.

### 1.6 — Mohs is per-cell, per-solid-component
- **What:** "Mohs level: per-cell, per-solid-component." Starts from phase-diagram initial assignment; ratchets up under sustained compression.
- **Where:** Line 249.
- **Implication for impl:** A solid-component-keyed mohs map per cell, NOT a single `u4`. If a cell has solid quartz (SiO₂) and solid feldspar (KAlSi₃O₈) coexisting, each carries its own mohs level. In practice this means a parallel array indexed by composition slot: `mohs[16]`, valid only where the slot is a solid contributor.
- **Open question:** Does the per-component mohs index use the same 16 composition slots, or a separate solid-only slot system? Flag for decision; suggest reusing the composition slot index for simplicity.

### 1.7 — Sustained-overpressure as f32 magnitude integrator (no integer counters)
- **What:** A single `f32` per cell that accumulates `(pressure_excess × cycle_time)` while the cell sits above its equilibrium threshold, and decays toward zero when below. Ratcheting fires when this magnitude crosses a trigger value. Replaces the `u8 cycles_above_threshold` counter from SESSION_LOG.
- **Where:** Line 250; reinforced "No integer counters are used anywhere in the cell state" line 255.
- **Implication for impl:** One `f32` field per cell. Update rule per cycle: `if pressure > threshold: sustained += (pressure - threshold) * dt; else: sustained *= decay_factor`. Ratcheting trigger: `if sustained > trigger: ratchet()`. This encodes both peak excess and duration in one continuous field.

### 1.8 — Petals: 6 slots per cell, persistent directional state
- **What:** Each cell has 6 petals (one per neighbor direction N, NE, SE, S, SW, NW) carrying:
  - directional stress (the tax field, accumulates and relieves)
  - accumulated velocity/momentum along that direction
  - topology metadata (`is_border`, `border_type_index`, `is_inert`, `is_grid_edge`)
  - any other directional history
  - **Cohesion is explicitly NOT petal state.**
- **Where:** Line 251; §Petal data lines 338–348; §Topology caching lines 593–602.
- **Implication for impl:** A `petals[6]` struct per cell holding stress (`f32`), momentum (`f32`), topology flags (small bitfield). Topology is filled on first contact and immutable thereafter. Cohesion is a transient inside the region kernel, never persisted.

### 1.9 — Working precision is f32 throughout; canonical state is packed/encoded
- **What:** All region-kernel arithmetic uses `f32`. Canonical grid storage uses packed/log-encoded representations (log-scale `u16` pressure, encoded temperature). f64 is forbidden ("FP64 is 1/64th of FP32 on all three cards — unusable").
- **Where:** Lines 74, 253–255.
- **Implication for impl:** Python reference uses `numpy.float32`. Encoding/decoding routines run at integration boundaries; region-kernel arithmetic stays in `f32`. Avoid accidental `float64` from Python literal arithmetic — broadcast with `np.float32(...)`.

### 1.10 — No integer counters anywhere in cell state
- **What:** All persistent accumulation is expressed as `f32` magnitudes with explicit accumulation and decay rules.
- **Where:** Line 255.
- **Implication for impl:** Ratchet duration, settled-cycles, demote-eligible-counters etc. are all `f32` magnitudes that integrate and decay. The `u8 cycles_above_threshold` from SESSION_LOG §4 is replaced.

---

## 2. Phase semantics

### 2.1 — Four phases with fixed equilibrium centers
- **What:** Solid, liquid, gas, plasma. Equilibrium centers in mass units:
  - Plasma: 42 (3 sub-passes, opportunistic)
  - Gas: 42 (3 sub-passes, opportunistic)
  - Liquid: 42 × 42 = 1,764 (5 sub-passes, opportunistic)
  - Solid: 42 × 42 × 42 = 74,088 (7 sub-passes, non-opportunistic)
- **Where:** Lines 259–269 (table at lines 263–268).
- **Implication for impl:** These are NOT tunable per scenario; they are universal constants. Per-element scaling factors (§2.5) multiply these. Hex-arithmetic-friendly (42 = 6×7) means averages of 7 cells of mass-42 give exact integer results — register layout and conservation tests should preserve this.

### 2.2 — Equilibrium centers are "centers", not caps
- **What:** A gas cell can exceed 42 under compression; a liquid can fall below 1,764 under tension. Deviation IS the pressure. Encoded ceiling/floor are clamping artifacts of the canonical encoding, not physics.
- **Where:** Lines 272–274.
- **Implication for impl:** Pressure dynamics are smooth and can carry mass either way. The encoding's range is wider than physically meaningful values; scenario bounds (§17) keep normal sim away from the encoded boundaries.

### 2.3 — Vacuum is the low-pressure limit, not a separate phase
- **What:** Vacuum = pressure at the encoding floor. Defining property is the pressure floor, NOT absence of phase content. Near-vacuum and vacuum are continuous; transition is smooth.
- **Where:** §Vacuum, lines 276–283.
- **Implication for impl:** No "vacuum flag" or "vacuum phase". Cells with very low total mass naturally hit the pressure floor via phase-mass dynamics. Vacuum-fill behavior (gas rushing into low-pressure region) emerges from normal pressure-driven flux, not from a vacuum special case.

### 2.4 — Mixed-phase cells are first-class
- **What:** A cell can hold any combination of solid+liquid+gas+plasma simultaneously (wet sand, foam, magma). Each phase fraction updates on its phase's sub-pass schedule.
- **Where:** Lines 244, 421–423.
- **Implication for impl:** Region kernel must support running, e.g., the solid-phase flux rule on a cell whose solid-fraction is 0.3 alongside the liquid-phase flux rule on the same cell with liquid-fraction 0.7. This is the granularity at which "phase-homogeneous warp dispatch" applies — not at the cell level, but at the per-fraction level.

### 2.5 — Per-element density scaling
- **What:** 42 / 1,764 / 74,088 are phase-class defaults. Per-element factors scale them multiplicatively. Iron > water; water > oil; helium < nitrogen. Gas density scales by molar mass; liquid is approximately compositionally uniform; solid diverges strongly by composition AND mohs level.
- **Where:** §Per-element density scaling, lines 284–290; §Material identity, lines 458–463.
- **Implication for impl:** Element table carries `gas_scale`, `liquid_scale`, `solid_scale_base` per element. Effective center for phase `p` of element `e` at mohs `m` is `phase_center[p] × element_scale[e][p] × mohs_factor(m)`. Mohs factor is geometric (~1.6× per level, see §11).

---

## 3. Identity computation

### 3.1 — Identity is computed per cycle, never stored
- **What:** Cell identity (phase + composition description used for cohesion, rendering, identity-dependent rules) is **computed each cycle** from phase-fraction masses and composition vector. Majority phase by mass wins; majority element within that phase wins.
- **Where:** §Cell identity is computed, not stored, lines 316–321.
- **Implication for impl:** No `cell.type` flag. A `compute_identity(cell) -> (phase, dominant_element)` function runs at the start of each cycle (or on demand inside the region kernel). Smooth transitions: a rock cell accumulating water flips from solid-majority to liquid-majority within a single cycle when the 50% threshold is crossed, but the underlying composition is continuous.

### 3.2 — No state-change events, no flag invalidation
- **What:** "No state-change event to manage, no flag to invalidate, no 'this cell is now water' transition code. The cell just answers 'what's my majority' from current state each cycle."
- **Where:** Line 320.
- **Implication for impl:** No event bus. No transition callbacks. Identity is a query.

### 3.3 — Two open questions about identity (gen5 explicitly shelves these)
- **Open question (gen5 §Shelved questions, line 757):** Majority-by-mass vs majority-by-fraction-of-equilibrium. A cell with 42 units of gas (at center) and 100 units of liquid (severely under-dense relative to 1,764 center) has mass-majority = liquid but the liquid is barely physically present. Do we use raw mass or normalized-to-center?
- **Open question (line 758):** Unified vs per-purpose identity. Cohesion, rendering, sorting, phase-transition decisions might want different "identity" answers. One unified function or several?
- **Implication for impl:** Build identity as a single function for now (M3-M5), parameterize it later (M6+) if subsystems diverge. Use majority-by-mass as M3 default; revisit when borderline-cell tests show pathology.

---

## 4. Region kernel (7-cell flower)

### 4.1 — A region is a 7-cell hex flower
- **What:** Center cell + 6 neighbors. Pointy-top hex grid, axial coordinates `(q, r)`, neighbor directions N, NE, SE, S, SW, NW.
- **Where:** §Grid line 237; §What a region is, lines 380–386.
- **Implication for impl:** Need axial-coordinate neighbor offsets for pointy-top hex (parity-dependent for odd/even rows). Region kernel takes a center index and reads 7 cells (1 center + 6 neighbors).

### 4.2 — Regions overlap; every cell is the center of its own region
- **What:** Every cell in the grid is the center of its own region AND a peripheral member of 6 others. Each cell participates in up to 7 different regions per cycle.
- **Where:** Lines 382–384.
- **Implication for impl:** Iterate region kernels over every (non-border-sentinel) cell. No domain decomposition with non-overlapping tiles. Cells get read 7× per sub-pass; this is intentional for L2 amortization on GPU and irrelevant to the Python reference (which is `numpy`-vectorized).

### 4.3 — Regions do not share state during compute
- **What:** Each region kernel reads canonical state at start of cycle, computes contribution in private working memory, emits flux records. No coordination between regions during compute.
- **Where:** Line 385.
- **Implication for impl:** Region computation is pure-functional given the snapshot. Python reference can vectorize across regions trivially. Race-free by construction (read from buffer N, write to flux scratch which is zero-initialized).

### 4.4 — Blind summation of flux contributions
- **What:** Multiple overlapping regions contribute to the same edge. Their contributions sum. No conflict resolution, no voting, no winner selection. Forces are additive (Newton).
- **Where:** Lines 396–397; §Sum, don't arbitrate, lines 580–583.
- **Implication for impl:** Per-edge flux scratch buffer is zero-initialized then accumulated into. In Python reference, this is `np.add.at(flux, edge_indices, contributions)` or equivalent scatter-add. On GPU it's atomicAdd — but the design says "blind summation eliminates atomics; each contribution adds independently" (line 21), meaning the scratch is sized per-region not per-edge, then reduced. **Open question:** does the Python reference replicate the per-region scratch then reduce, or scatter-add directly? For M3 use scatter-add for simplicity.

### 4.5 — Veto stage between region compute and summation
- **What:** Some proposed fluxes are physically impossible (across grid border, into inert region, into hard constraint). The veto stage runs between region compute and flux summation, filtering proposed fluxes against hard constraints. After topology caching settles in, "the veto rarely fires after the first cycle."
- **Where:** §Veto stage, lines 584–590; §Topology caching lines 592–602.
- **Implication for impl:** Veto is a per-edge predicate using cached petal topology metadata. In Python reference, a boolean mask applied to the flux buffer before summation: `flux *= (~vetoed_mask)`. Veto applies to the proposed flux, not after summation.

### 4.6 — Edge consistency by physics symmetry
- **What:** Flux from A→B equals −flux from B→A up to numerical precision; they sum to one transport. With cell-centric storage, each cell owns its 6 outgoing records; with edge-centric storage, double-counting is impossible by construction. Choice is implementation; physics doesn't constrain.
- **Where:** §Edge-consistency convention, lines 604–608.
- **Implication for impl:** Pick edge-centric for the Python reference (cleaner; no convention to maintain). Each edge has one flux record. Region kernel writes contributions to edges it touches; signs handled by edge-direction convention.

---

## 5. Per-phase sub-pass scheduling

### 5.1 — Concurrent phase sub-passes within a cycle
- **What:** Gas, liquid, solid run **concurrently**, each at its own pass budget within the same cycle window. Cycle window = max budget = 7 sub-passes (solid). Phases do not run sequentially.
- **Where:** §Concurrent phase sub-passes, lines 398–419.
- **Implication for impl:** Within one cycle, the loop is `for sub_pass in 0..7:` and at each sub-pass, regions belonging to currently-active phases compute. A phase is "active" at sub_pass `i` iff `i < budget[phase]`. Each sub-pass is a complete Jacobi step (read buffer N, write buffer N+1, swap).

### 5.2 — Pass budgets: gas 3, liquid 5, solid 7, plasma 3
- **What:** Hardcoded per-phase pass budgets. Sub-passes 1–3 all phases active; 4–5 gas frozen, liquid+solid active; 6–7 liquid frozen, only solid active.
- **Where:** Lines 402–414.
- **Implication for impl:** The freeze-as-budgets-exhaust pattern means later sub-passes see frozen phases as static reads. In the Python reference, "frozen" is implemented by skipping the flux contribution for that phase in later sub-passes, while reads continue normally.

### 5.3 — Cross-phase boundaries update live
- **What:** Where gas meets liquid or liquid meets solid, flux between them can exchange while both sides are still computing (within their respective budgets). Cross-phase interaction is NOT gated on phase completion.
- **Where:** Lines 418–419.
- **Implication for impl:** A gas-liquid boundary edge's flux is computed during BOTH gas's and liquid's active sub-passes. Both contributions sum. There is no "wait for all gas passes before starting liquid" barrier.

### 5.4 — Mixed-phase cells run multiple phase schedules
- **What:** Wet sand (solid+liquid) gets the solid fraction's 7 solid sub-passes AND the liquid fraction's 5 liquid sub-passes. They couple via intra-cell phase transitions and shared boundary flux records.
- **Where:** §Mixed-phase cells, lines 421–423.
- **Implication for impl:** A mixed cell appears in multiple phase-dispatch lists. Its solid-fraction is updated on solid sub-passes; its liquid-fraction on liquid sub-passes. Intra-cell coupling (phase transitions, temperature equalization within the cell) happens at integration time.

### 5.5 — Each sub-pass is a full Jacobi step with buffer swap
- **What:** Read canonical from buffer N → all hot regions compute flux → blind sum → integration writes new state to buffer N+1 → swap. Each sub-pass advances the field.
- **Where:** §Cycle structure step 2, lines 666–675; §Why each sub-pass gets its own buffer swap, lines 676–680.
- **Implication for impl:** Pressure waves travel up to N cells per cycle for a phase with N sub-passes (gas pressure waves cross 3 cells/cycle; solid stress waves 7 cells/cycle). This is what makes the pass counts physically meaningful — they are wave-propagation budgets, not just relaxation iterations.

### 5.6 — Tail-at-Scale region culling
- **What:** A region whose 6 directional flux contributions are all below `ε` (noise floor) culls itself from subsequent sub-passes within the cycle. If gas equalizes by pass 2, the region skips pass 3.
- **Where:** §Tail at Scale, lines 437–445.
- **Implication for impl:** Per-region active-flag set during sub-passes. After computing region's fluxes, if max(|flux|) < ε, mark inactive for remaining sub-passes of this cycle. Reactivates next cycle by default. The connection to memory tiers (§14) is that consecutive-cycles-inactive demotes to warm tier.

### 5.7 — ε is a single tunable global parameter
- **What:** "The noise floor ε is a tunable parameter… one number, tuned per target hardware." Smaller ε = higher fidelity, more active regions. Larger ε = aggressive culling, more performance.
- **Where:** Line 445.
- **Implication for impl:** Single scalar in sim config. Used for: region culling (§5.6), gravity application (§9.4), demote eligibility (§14).

---

## 6. Phase-specific flux rules

### 6.1 — Plasma: averaging like gas, but with amplified thermal coupling
- **What:** Plasma averages mass toward same-phase neighbors like gas. But its energy flux to non-plasma neighbors is large, driving rapid heating, ablating solids, evaporating liquids. Opportunistic.
- **Where:** Line 428.
- **Implication for impl:** Plasma uses gas-style mass diffusion + an amplified energy flux term. Energy flux to non-plasma neighbors uses a high coupling coefficient (compared to gas-gas conduction). This causes plasma boundaries to ablate adjacent solids by raising their T past melt/vaporize.

### 6.2 — Gas: averaging toward same-phase, mass-conserving, opportunistic
- **What:** Gas averages toward same-phase neighbors. Pressure equilibrates fast. Composition mixes. Opportunistic — fills low-pressure regions aggressively.
- **Where:** Line 429.
- **Implication for impl:** Standard diffusion: flux ∝ (pressure_self − pressure_neighbor), per species. Composition mixes via per-species-per-edge mass flux (§12).

### 6.3 — Liquid: same-phase averaging, gravity-biased, opportunistic; surface tension via cohesion
- **What:** Same-phase averaging at reduced rate, gravity-biased via the sorting ruleset. Opportunistic. **Surface tension is NOT a separate rule** — it emerges from cohesion.
- **Where:** Line 430.
- **Implication for impl:** Liquid flux is gas-like flux modulated by (a) per-direction gravity bias from the sorting ruleset (§8), and (b) cohesion damping (§7). No surface-tension term.

### 6.4 — Solid: non-opportunistic; transmits stress; moves discretely; move-before-compress
- **What:** Solid does NOT spontaneously fill low-pressure regions (non-opportunistic mass transport). Transmits stress via petal stress updates. Moves only in discrete displacement events when yield threshold is exceeded. **Move-before-compress priority:** when yield exceeded, first check for fluid/gas neighbor to displace into (brittle/spalling); if none, compress and harden (ductile).
- **Where:** Line 431.
- **Implication for impl:** Solid sub-pass updates petal stress fields, NOT mass fluxes (under normal conditions). Yield check: if `petal_stress > yield_threshold`, look for displacement target (gas or liquid neighbor); if found, emit a discrete mass-displacement flux; if none, increment sustained-overpressure (toward ratchet).

### 6.5 — Vacuum needs no special-case rule
- **What:** Vacuum is just the low-pressure limit. Incoming fluxes from neighbors are accepted normally.
- **Where:** Lines 432–433.
- **Implication for impl:** Do not write a vacuum branch. Cells with pressure at the floor accept incoming flux and rise off the floor naturally.

### 6.6 — Phase transitions are per-cell per-cycle decisions via 2D phase diagram
- **What:** "Phase transitions (freeze/melt/evaporate/condense/ionize/recombine) are decisions the region kernel makes per cell per cycle based on the 2D phase diagram lookup `(pressure, temperature) → (phase, initial_mohs)`."
- **Where:** Lines 434–435.
- **Implication for impl:** Phase transitions are NOT a separate Stage 1 special-case (departure from SESSION_LOG framework). They run in the region kernel each cycle. The 2D phase-diagram table is per-element; lookup uses cell's current `(P, T)` — see §10 for details.

---

## 7. Cohesion (per-cell, per-direction damping)

### 7.1 — Cohesion is a 6-element scalar per cell, per cycle
- **What:** Each cell computes 6 cohesion values (one per neighbor direction) consumed as damping coefficients on its outgoing flux. High cohesion → suppressed outgoing flux.
- **Where:** §Cohesion, lines 350–373.
- **Implication for impl:** During the region kernel, compute `cohesion[6]` for the center cell from current composition + purity. Apply as a multiplier to that cell's outgoing flux per direction.

### 7.2 — Cohesion formula: composition similarity × purity
- **What:** `cohesion(self, dir) = f(shared_majority_match(self.comp, neighbor.comp)) × g(self.purity)`
- **Where:** Lines 354–360.
- **Implication for impl:** Need a `shared_majority_match` function (composition similarity metric — likely fraction of self's mass in elements shared with neighbor's majority). Need `purity` (likely 1.0 for pure, falls toward 0 as composition diversifies — Shannon entropy or simpler "fraction of largest slot"). Both `f` and `g` are monotone non-negative; exact shapes are tunable.
- **Open question:** Concrete formulas for `f`, `g`, `shared_majority_match`, and `purity` are not specified. M3 placeholder: `purity = max(fraction)`, `shared_majority_match = sum_over_shared_elements(self.frac × neighbor.frac)`, `f` and `g` identity. Flag for tuning.

### 7.3 — Cohesion is blind and asymmetric
- **What:** Each cell computes cohesion from its own composition, its own purity, and the neighbor's canonical composition. It does NOT know the neighbor's cohesion value. No reciprocity constraint, no shared cohesion variable.
- **Where:** Lines 360–363.
- **Implication for impl:** No cohesion read/write coupling. A cell reads neighbor composition to compute similarity, but the cohesion values themselves never cross cells. Asymmetry (pure water cell has higher cohesion toward impure-water neighbor than vice versa) emerges from the blind flux dampings summing.

### 7.4 — Cohesion is transient working state, never persisted
- **What:** Recomputed each cycle from current composition. Exists only inside the region kernel during flux computation.
- **Where:** Lines 372–373.
- **Implication for impl:** No cohesion field in cell state. Region-kernel local; lifetime = single cycle.

### 7.5 — Cohesion uses sorted exposure at mixed-cell boundaries
- **What:** A mixed oil-water cell's cohesion toward its top neighbor is computed against oil composition (the phase exposed on that edge per the sorting ruleset, §8), not against the cell's overall composition. Cohesion at the bottom edge is computed against water.
- **Where:** Line 490.
- **Implication for impl:** Cohesion calculation must use the per-direction sorted-exposure composition, NOT the unsorted cell composition. This couples §7 to §8 — sort first, then compute cohesion against the sorted composition for that edge.

---

## 8. Sorting ruleset at edges

### 8.1 — Cells are indivisible; sorting is a pure function at flux time
- **What:** A cell has no internal spatial substructure. A 30%-liquid-70%-gas cell does not store "liquid is at the bottom." When flux is computed across an edge, the kernel acts **as if the cell's contents were sorted** at that edge by a ruleset. The sorting is a pure function applied at flux-compute time, not stored state.
- **Where:** §Cells are indivisible, lines 467–493.
- **Implication for impl:** No per-cell internal-geometry state. Sorting is a function `sort(composition, phase_fractions, edge_dir, gravity_vector_at_cell) → (per_phase_exposure_weight[6], dominant_phase_at_edge)`.

### 8.2 — Sorting inputs and outputs
- **What:**
  - Inputs: composition vector, phase-fraction masses, edge direction, local gravity vector at cell.
  - Outputs: per-phase exposure weight for the edge (modulating flux), which phase is most exposed across the edge.
- **Where:** Lines 472–481.
- **Implication for impl:** Per-edge call returns `f32 weights[4]` (one per phase) summing to 1 plus a dominant-phase index. Implementation uses gravity vector to pick which phase floats/sinks: heavier phases sort toward `+gravity`, lighter toward `−gravity`. With zero gravity, weights = phase fractions (no sorting).

### 8.3 — Sorting applies to BOTH outbound and inbound flux
- **What:** Outbound: this cell sends flux drawn preferentially from whichever phase is exposed on the outgoing edge. Inbound: incoming mass joins composition but is associated with the receiving edge's sorting position, affecting subsequent flux decisions.
- **Where:** Line 488.
- **Implication for impl:** Outbound mass is drawn according to exposure weights for the outgoing edge. Inbound mass distributes into the cell's composition but the sort-on-next-cycle reflects "this came in on the bottom edge."

### 8.4 — Sorting is gravity-aware (works at any direction)
- **What:** Sorting reads a per-cell gravity vector. Zero-gravity → uniform exposure. Centripetal (spinning habitat) → radially-outward. Magnetic sort (ferromagnetic) is a future extension.
- **Where:** Lines 486–492.
- **Implication for impl:** Gravity vector at cell drives sorting direction. Magnitude controls strength of sorting (weak gravity → weak sort).

### 8.5 — Emergent behaviors that test sorting
- **What:** Oil floats on water; bubbles rise; sediment falls. All from sorting + flux + cohesion + cycles. No explicit "buoyancy" rule.
- **Where:** Lines 483–486.
- **Implication for impl:** These are validation scenarios, not impl tasks. M5+ test: drop a mixed oil-water cell into a column, expect oil to migrate up over many cycles.

---

## 9. Gravity vector field

### 9.1 — One vector per cell, Jacobi-diffused
- **What:** Gravity is a vector field with one vector per cell (magnitude+direction combined as `(gx, gy)` or `(gx, gy, gz)` for 3D). Diffused via Jacobi sweeps like other physics fields.
- **Where:** §Gravity as a first-class diffused vector field, lines 496–545.
- **Implication for impl:** Two `f32` fields per cell for 2D (`gravity_x`, `gravity_y`) or three for 3D. Diffusion is Jacobi-style averaging across neighbors.

### 9.2 — Setup phase: layered Jacobi initialization from border seeds
- **What:**
  - Scenario defines point sources (planet center, moon, asteroid) — position + mass each.
  - For each border tile, the gravity vector contribution from each point source via Newton's law (`g = GM/d² × direction_to_source`). Contributions sum into one vector per border tile.
  - Layered Jacobi passes propagate boundary values inward to settle the initial field.
- **Where:** §Setup phase, lines 504–509.
- **Implication for impl:** Border-vector seeding is a one-time setup pass (or rerun when point sources move). Then the field is diffused inward. The "layered Jacobi" pattern is the same used elsewhere for setup seeding.

### 9.3 — Runtime phase: concurrent Jacobi diffusion, low sub-pass count
- **What:**
  - Border values stay frozen (external context static from slice's perspective).
  - Active cells contribute their own mass to gravity diffusion (mass concentrations perturb the field locally).
  - Gravity diffusion is a first-class concurrent sub-pass alongside pressure/energy/etc. Because gravity changes slowly, sub-pass count can be low (1 per cycle, or 1 per N cycles, tunable).
- **Where:** §Runtime phase, lines 510–514.
- **Implication for impl:** Add `gravity` to the sub-pass dispatch list with budget 1 (or configurable). Cell-mass contributions perturb the local diffusion source.

### 9.4 — Gravity applies as acceleration to motion-only cells
- **What:** "Gravity is applied as an acceleration contribution to each cell's petal data **only when the cell has non-zero motion.** Cells at rest do not have gravity applied."
- **Where:** §Applying gravity to cell motion, lines 521–535.
- **Implication for impl:** Per cycle: `if max(|petal_momentum|) > ε: petal.acceleration += gravity_at_cell`. Settled cells (rocks at rest, hydrostatic columns) skip the update; gravity is inactive for them. Eliminates the "perpetual force opposed by reaction force" problem and lets static stratification stabilize.

### 9.5 — Multi-source gravity comes free from border seeding
- **What:** A scenario with planet+moon supplies both point sources to the border calculation. Vector-summed at border, diffused inward. Lagrange points, tidal forces, and gradient-field features emerge without additional mechanism.
- **Where:** §Multi-source gravity, lines 536–540.
- **Implication for impl:** Setup seed function takes a list of point sources, sums Newton contributions per border tile. No special multi-source code path inside the runtime.

### 9.6 — Convexity is required
- **What:** "Border tile shape may be non-uniform but **the overall sim region must be convex.** Non-convex borders produce gradient pathologies at the concave regions (gravity diffusion flows around obstacles incorrectly). Convex is the architectural requirement."
- **Where:** Line 508; reinforced §Scenario bounds line 561.
- **Implication for impl:** Setup-time convexity check on the region. Hex disc, hex rectangle, and other convex shapes are fine; L-shapes and donuts are rejected.

### 9.7 — Refreshable point sources for moving bodies
- **What:** Point source positions/masses can be stored as formulas. If sources move (orbiting moon, mobile spacecraft), border can be recomputed periodically and re-seeded. Refresh frequency tunable: static planets never refresh; dynamic systems refresh every N cycles.
- **Where:** Lines 516–518.
- **Implication for impl:** Point source = `(position_func(t), mass)`. Re-seed at refresh interval.

### 9.8 — Vector magnitudes stay in f32 precision envelope
- **What:** "The 'stupendous values' concern dissolves because the sim never stores or sums planetary mass directly — it stores the *vector contribution* at the border, which is always a modest number regardless of the mass generating it."
- **Where:** §Precision and bounds, lines 542–545.
- **Implication for impl:** Border vectors are normal f32 scalars. No multi-precision arithmetic needed.

---

## 10. Phase transitions

### 10.1 — Phase transitions are an in-place fraction shift, not a Stage-1 special case
- **What:** Phase transitions run inside the region kernel each cycle as part of normal flux computation. They use a 2D phase-diagram lookup `(pressure, temperature) → (phase, initial_mohs)`. Replaces the "Stage 1: phase resolve" special-case from SESSION_LOG.
- **Where:** Lines 434–435.
- **Implication for impl:** Per cell, per cycle: query phase diagram with current (P, T) per element. If diagram says element should be in phase B but cell holds element in phase A, shift mass-fraction from A's phase-mass to B's phase-mass. Emit appropriate energy flux (latent heat).

### 10.2 — No separate latent-heat shed step
- **What:** Phase transitions write flux records for any mass/energy redistribution the transition requires. Not a separate Stage-1 phase-resolve operation.
- **Where:** Line 435.
- **Implication for impl:** Latent heat for melting/vaporizing/condensing is accounted in the energy flux written by the phase-transition logic in the region kernel. Element table needs latent-heat-of-fusion and latent-heat-of-vaporization per element.

### 10.3 — Phase diagram lookup is per-element
- **What:** "the 2D phase diagram lookup `(pressure, temperature) → (phase, initial_mohs)`" — each element has its own phase diagram (water freezes at different P,T than iron).
- **Where:** Line 435; SESSION_LOG §4 line 87 (carries forward).
- **Implication for impl:** Element table includes a 2D phase-diagram (sampled grid or piecewise-linear). Lookup returns `(phase_index, initial_mohs)`. Initial mohs is set when an element first transitions into the solid phase.

### 10.4 — Cross-phase emergent behaviors
- **What:** Condensation, evaporation, precipitation, ionization, ablation, trapped water, springs/seepage — all emerge from phase transitions + cohesion + sorting + diffusion + cycles. Listed in §Cross-phase dynamics with concrete walk-throughs.
- **Where:** Lines 292–314.
- **Implication for impl:** These are validation scenarios for M5+. The implementation does not need separate code paths for any of them.

---

## 11. Mohs ratcheting

### 11.1 — F32 sustained-overpressure integrator (no integer counters)
- **What:** Per-cell `f32` accumulator. Each cycle above threshold: `sustained += (pressure_excess) × cycle_time`. Below threshold: `sustained *= decay_factor`. Ratchet fires when `sustained > trigger`.
- **Where:** Line 250 (state); §Mohs ratcheting lines 447–456.
- **Implication for impl:** Single `f32` per cell. Update rule one line in the integration kernel. Replaces SESSION_LOG's `u8 cycles_above_threshold`.

### 11.2 — Ratcheting triggers on peak-excess OR duration-gate
- **What:** Single sustained-overpressure field captures both. A large single-cycle spike floods the integrator past trigger; a small sustained excess accumulates over many cycles to trigger.
- **Where:** Lines 449–452.
- **Implication for impl:** Tuning of the integrator (decay rate, trigger value) governs sensitivity. Suggest: trigger = 1.0 (in normalized units), spike-with-`pressure_excess > 1` triggers in one cycle; sustained excess of 0.1 triggers in ~10 cycles.

### 11.3 — Ratchet step: mohs_level++, excess absorbed, work → energy
- **What:** On ratchet: `mohs_level += 1`; excess pressure absorbed into the new level's dead-band; compression work dumped into energy channel as heat. Ratcheting is exothermic — metamorphic rock is hot, sim gets this for free.
- **Where:** Lines 449–453.
- **Implication for impl:** Ratchet event:
  ```
  mohs[component] += 1
  energy += compression_work  (= excess × volume_change, scaled appropriately)
  pressure -= excess  (absorbed into new dead band)
  sustained = 0      (reset integrator)
  ```

### 11.4 — Geometric yield threshold per level
- **What:** "Each ratchet step raises the cell's yield threshold geometrically (Mohs maps exponentially to wallet equivalent, ~1.6× per level). The ceiling is diamond at Mohs 10; nothing in ambient conditions can push past."
- **Where:** Line 453.
- **Implication for impl:** `yield_threshold(m) = base_threshold × 1.6^m`. Mohs is capped at 10.

### 11.5 — Overburden field
- **What:** Scalar field maintained alongside cell state, storing cumulative mass of all cells above each cell. Updated incrementally by the integration kernel (O(changes), not O(grid)). Regions read overburden as input to compression and ratcheting logic.
- **Where:** §Overburden field, lines 454–456.
- **Implication for impl:** A second `f32` field at grid resolution. Update rule:
  ```
  overburden[q,r] = mass[q,r] + overburden[q, r_above]
  ```
  Maintained incrementally — when a cell's mass changes by Δm, overburden[q,r] and all cells below it update by Δm. Effective sustained pressure for ratcheting includes overburden contribution.
- **Open question:** "incrementally O(changes)" needs concrete update rule. For Python reference, full recomputation each cycle is cheaper to write; do that for M3 and optimize later.

### 11.6 — Mohs maximum and initial assignment from phase diagram
- **What:** Initial Mohs assigned by phase-diagram lookup when element first transitions to solid. Max is 10 (diamond).
- **Where:** Line 435 (initial mohs from diagram); line 453 (cap at 10).
- **Implication for impl:** When phase-transition writes mass into solid phase, also write initial mohs from phase diagram. Cap all subsequent ratchet operations at 10.

---

## 12. Flux records

### 12.1 — Per-edge per-cycle scratch; zero-init then blind-sum
- **What:** Flux records describe boundary-integrated transport across one hex edge during one cycle. 6 edges per cell. Zero-initialized at start of sub-pass, accumulated by blind summation, consumed by integration kernel. **Do not persist across cycles.**
- **Where:** §Flux records, lines 322–337.
- **Implication for impl:** Flux scratch buffer reset to zero each sub-pass. Integration consumes and discards.

### 12.2 — Flux record carries five quantities minimum
- **What:** A flux record carries:
  - **Mass flux**: per species, per phase. A single edge can carry H₂O liquid AND CO₂ gas simultaneously.
  - **Momentum flux**: velocity/momentum being transported, plus pressure-work contribution (P × A × dt).
  - **Energy flux**: kinetic + thermal + pressure work.
  - **Stress flux**: directional stress transmitted across this edge. Updates petal stress on both sides during integration.
  - **Phase-identity metadata**: enough information about what's flowing for the receiving cell to integrate correctly (a gas flux to a solid-dominated cell behaves differently than to a vacuum-dominated cell).
- **Where:** Lines 326–333.
- **Implication for impl:** Flux record is wide. Per edge per cycle:
  ```
  mass_per_species_per_phase: f32[4 phases × 16 elements]   # 64 floats
  momentum: f32[2D vector]                                   # 2 floats
  energy: f32                                                # 1 float
  stress: f32                                                # 1 float
  phase_identity: u8 (or fraction vector)                    # ≤8 floats
  ```
  This is heavy. Use sparsity in practice (most species are zero per edge).

### 12.3 — "Carry as much information as we need to do the job"
- **What:** Schema is not fixed ceremonially; it expands to whatever conserved quantities physics requires. Fields not needed for a configuration are zero and cost only their packed storage.
- **Where:** Line 336.
- **Implication for impl:** The flux record schema is data-driven. Adding a new conserved quantity (e.g., charge for electromagnetic extension) is a schema-extension, not a kernel rewrite.

### 12.4 — Storage layout (edge-centric vs cell-centric) is implementation choice
- **What:** "actual storage is whatever is most efficient (edge-centric, cell-centric SoA, whatever the kernel needs — the layout is an implementation choice, not a required abstraction)."
- **Where:** Line 324.
- **Implication for impl:** Python reference picks edge-centric (simpler, no double-counting risk). Each unique edge has one flux record; orientation handled by sign convention.

### 12.5 — Flux records update petals during integration
- **What:** A flux carrying stress increments both sides' petal stress. A flux transporting momentum updates both sides' petal velocity components. Petals are the persistent home; flux is the transport mechanism.
- **Where:** Line 348.
- **Implication for impl:** Integration kernel reads flux, writes new cell state AND updates petal stress/momentum on both incident cells.

---

## 13. Borders / boundary conditions

### 13.1 — Per-channel configurable behavior
- **What:** Each transport channel (mass, momentum, energy, stress) has its own border behavior flag independently. Examples:
  - Thermally insulating but mass-permeable (pressure relief valve)
  - Mass-sealed but thermally conductive (vacuum flask wall)
  - Fully sealed (isolated experiment chamber)
  - Radiatively coupled to fixed ambient T (space-facing exterior)
  - Held at fixed T regardless of incoming flux (heated plate, cold sink)
  - Held at fixed flux (solar input, geothermal input)
  - Reflective (momentum inverts; wall bounce)
  - Absorbing (momentum dissipates; soft wall)
- **Where:** §Borders and boundary conditions, lines 612–637.
- **Implication for impl:** Border configuration per edge per channel: 4 channels × small enum each. Stored compactly; looked up by petal topology cache.

### 13.2 — Border properties table, indexed by border-type tag
- **What:** Border behaviors stored in a lookup table at sim start, indexed by border-type tag. Per-channel parameters live in the table (target temperatures, flux magnitudes, absorption/reflection coefficients). Analogous to element table; one lookup per border-contact at topology-cache time, no per-cycle cost.
- **Where:** §Border properties table, lines 629–633.
- **Implication for impl:** A `border_table.tsv` (or equivalent) with one row per named border type. Petal metadata stores `border_type_index` (small int). Lookup at flux-compute time uses the index to retrieve channel-specific behavior.

### 13.3 — Topology cached in petal metadata; immutable post-discovery
- **What:** Petal metadata caches `is_border`, `border_type_index`, `is_inert`, `is_grid_edge` and any cached parameters needed by border properties table. Discovered on first contact; invariant thereafter for static topology. Dynamic neighbor state is NOT cached.
- **Where:** §Topology caching, lines 593–602.
- **Implication for impl:** Petal struct includes these flags as small bitfield + index. First-cycle code path populates them; subsequent cycles read directly without re-evaluation.

### 13.4 — Setup-time validation of border consistency
- **What:** Contradictory per-channel border settings (e.g., "fully sealed" AND "fixed pressure 10 atm") rejected at setup.
- **Where:** Lines 562, 564.
- **Implication for impl:** Setup-time validation routine cross-checks border-table entries for internal consistency.

---

## 14. Cycle structure

### 14.1 — Cycle = promotion + sub-pass loop + re-encoding + render-sync
- **What:** One cycle:
  1. **Promotion pass** (tier management): warm/cold regions with hot neighbors get promoted; equilibrium hot regions demote.
  2. **Sub-pass loop** (3/5/7 per phase, concurrent): each sub-pass = read N → compute fluxes → blind-sum → integrate → write N+1 → swap.
  3. **Re-encoding**: working `f32` compressed to canonical packed representation after final sub-pass.
  4. **Render sync** (optional, at display rate).
- **Where:** §Cycle structure, lines 661–684.
- **Implication for impl:** Outer-loop structure for the Python reference. Render sync produces JSON snapshot for the debug harness viewer.

### 14.2 — Double-buffering (A/B ping-pong) is sufficient
- **What:** "Double-buffering (A/B ping-pong) is sufficient. You do not need one buffer per sub-pass unless you want to preserve intermediate states for debugging."
- **Where:** Lines 678–680.
- **Implication for impl:** Two physics buffers, swapped each sub-pass. For Python reference debug harness, also write each sub-pass to disk as a JSON frame for validation.

### 14.3 — Re-encoding only at end of cycle, not each sub-pass
- **What:** Working `f32` state is compressed to canonical packed representation **after the final sub-pass of the cycle**, not each sub-pass.
- **Where:** Line 673.
- **Implication for impl:** Sub-passes operate on `f32` working buffers; encode/decode happens at cycle boundary. Within a cycle, all sub-passes share `f32` state.

### 14.4 — Render sync is decoupled
- **What:** Renderer runs at display framerate (likely lower than sim framerate). It samples canonical state.
- **Where:** Lines 728–730.
- **Implication for impl:** Render sync is an optional output step, not a sim dependency. JSON dump for debug harness is the Python reference's analog.

---

## 15. Memory tiers (GPU-specific, DEFER for Python reference)

### 15.1 — Three tiers: hot VRAM / warm VRAM / cold sysRAM
- **What:**
  - Hot tier: full `f32` working state, full flux records, double-buffered.
  - Warm tier: canonical packed-encoded state only. Re-promotable in one cycle.
  - Cold tier: system RAM, compressed, streamed over PCIe when disturbance approaches.
- **Where:** §Memory tiers, lines 688–710.
- **Implication for impl:** **Out of scope for Python reference.** The Python reference holds everything in working `f32` (analogous to all-hot). Tier discipline is a CUDA-port concern.
- **Flag:** What does the Python reference need for tier compatibility? Answer: nothing yet. The cell-state schema must be encodeable to the canonical packed form (so an eventual tier-demote operation is possible), but the Python reference itself does not implement tiering.

### 15.2 — Promote/demote discipline
- **What:** Promote on incoming flux above `ε`. Demote when all fluxes and incoming-toward-cell fluxes have been below `ε` for some hysteresis-window of consecutive cycles.
- **Where:** Lines 705–710.
- **Implication for impl:** **Defer to M9.**

---

## 16. Hardware patterns (GPU-specific, M9 CUDA-only)

These are listed for completeness; **none are implemented in the Python reference.** Catalogued so the schema/data-layout decisions made now do not paint the eventual CUDA port into a corner.

### 16.1 — `cp.async` multistage pipeline (sm_80+)
- **Where:** §Multistage asynchronous-copy pipeline, lines 166–172.
- **Python reference relevance:** Cell state aligned to 16 bytes where possible. Reference impl should keep packed cell-state schema compatible with 16-byte alignment (no fields straddling 16-byte boundaries in the canonical packed layout).

### 16.2 — Warp shuffles for neighbor access
- **Where:** Lines 174–183.
- **Python reference relevance:** N/A directly; the hex-axial neighbor offsets and parity handling do carry over.

### 16.3 — Shared memory carveout 100 KB / SM
- **Where:** Lines 185–193.
- **Python reference relevance:** N/A.

### 16.4 — Structure-of-Arrays cell layout
- **Where:** §Structure-of-Arrays cell layout, lines 195–201.
- **Python reference relevance:** **Highly relevant.** Use SoA in the Python reference too. Each field is a separate `numpy` array indexed by cell coordinate, not an array of cell-objects. This matches how `numpy` vectorizes anyway.

### 16.5 — Phase-homogeneous warp dispatch
- **Where:** §Phase-homogeneous warp dispatch, lines 203–209.
- **Python reference relevance:** Marginal. The Python reference can dispatch all phases over the full grid with vectorized masks; it does not need warp-level homogeneity.

### 16.6 — Dual-architecture compilation (sm_86, sm_89, PTX fallback)
- **Where:** Lines 211–221.
- **Python reference relevance:** N/A.

### 16.7 — Deferred optimizations (L2 persistence, tensor core offload, thread block clusters)
- **Where:** Lines 223–229.
- **Python reference relevance:** N/A.

---

## 17. Scenario bounds and validation

### 17.1 — Hard operating bounds enforced at setup
- **What:** Scenarios outside bounds are rejected with a clear error message before any cycle runs. Bounds enforced:
  - Gravity vector magnitude
  - Slice extent vs. point source distance
  - Mass per cell (capped by phase-density ceilings, ratchet caps)
  - Flux magnitudes per cycle (mass can't move more than a fraction of a cell's contents per cycle)
  - Temperature and pressure ranges (within element table phase-diagram domain and log-scale encoding range)
  - Simulation region convexity
  - Border configuration consistency (per-channel, no contradictions)
- **Where:** §Scenario bounds and validation, lines 550–572.
- **Implication for impl:** Setup-time validator runs before any cycle. Returns a list of violations; sim refuses to start if non-empty. Each bound is a parameter in the sim configuration (tunable per use case).

### 17.2 — Convexity is structural
- **What:** Required for gravity diffusion to behave correctly. Non-convex regions rejected.
- **Where:** Line 561.
- **Implication for impl:** Convex-hull check or region-shape check at setup. Hex disc and hex rectangle pass; L-shapes and donuts fail.

### 17.3 — Flux-magnitude-per-cycle bound as numerical-stability guard
- **What:** "Mass can't move more than a fraction of a cell's contents per cycle; prevents numerical explosions from pathological gradients."
- **Where:** Line 559.
- **Implication for impl:** Per-cycle flux-magnitude clamp (CFL-like). Suggested: outgoing mass per cycle ≤ 0.5 × cell mass. Apply during region kernel as a soft cap, log if hit.

### 17.4 — Tunable safety margins
- **What:** Bounds are sim-configuration parameters, not hardcoded. Different use cases (game, research, benchmark) tune differently. As hardware improves, bounds widen without kernel changes.
- **Where:** §Tunable safety margins, lines 570–572.
- **Implication for impl:** All bounds live in a `sim_config.toml` (or equivalent). Defaults reasonable for game scenarios; can be widened for research.

---

## VS WIKI / SESSION_LOG delta list

The earlier framework (wiki/, SESSION_LOG_2026-04-16.md) and the gen5 design diverge on numerous points. Below: explicit contradictions where gen5 changes the framework, **ranked by impact on a Python reference implementation.**

### Δ1 (HIGH) — Composition vector size: 4 → 16 slots
- **Earlier:** SESSION_LOG §6, line 127: `[(element_id: u8, fraction: u8) × 4]`. 4 slots covers ~95% of real materials.
- **Gen5:** §State representation line 243: 16 slots, full periodic table coverage with degradation.
- **Impl impact:** Cell struct grows by 24 bytes per cell. Composition merge (slot collision) needs an "element similarity" metric not yet defined. Touches every cell-state read/write.

### Δ2 (HIGH) — Stage 1 phase-resolve special case is REMOVED
- **Earlier:** SESSION_LOG §1, lines 30–37: "Stage 1: Phase resolve (type check) — render gate, 1 pass." Phase resolution was its own pipeline stage.
- **Gen5:** Lines 434–435: Phase transitions run inside the region kernel each cycle as part of normal flux computation. No Stage 1.
- **Impl impact:** The pipeline is now **one** sub-pass loop per phase (not stages 1/2/3). Phase transitions are folded into the region kernel. Latent heat is shed via energy flux records, not a special phase-resolve operation. Major restructure of the cycle.

### Δ3 (HIGH) — Cells are mixed-phase by default; single-phase is the special case
- **Earlier:** Cell struct (SESSION_LOG §6 line 134): `phase: u2` — one phase per cell.
- **Gen5:** §Per-cell state line 244: phase distribution is a fraction vector. Wet sand, foam, magma are first-class.
- **Impl impact:** Phase is no longer a categorical field. Every cell carries 4 phase fractions. Region kernel handles per-fraction sub-pass updates.

### Δ4 (HIGH) — Sustained-overpressure is f32 magnitude, not u8 counter
- **Earlier:** SESSION_LOG §4 line 92, "Open thread #6" line 197: `cycles_above_threshold: u8`.
- **Gen5:** Line 250, line 255: `f32` magnitude integrator, decay-and-accumulate. Explicit "no integer counters anywhere."
- **Impl impact:** Different update rule (continuous integration with decay vs. discrete counter). Different trigger condition (magnitude > trigger vs. count ≥ N).

### Δ5 (HIGH) — Auction / bid / arbitrate framework is GONE
- **Earlier:** SESSION_LOG §1–§3, wiki/auction.md: μ-gradient mass auction with bidders, recipients, accumulators, conflict resolution (or proportional distribution after the dead-band-seeking refactor).
- **Gen5:** Lines 580–583: "Sum, don't arbitrate. … There is no conflict resolution, no voting, no winner selection among region contributions. The physics is vector summation." The auction metaphor is replaced by region-kernel blind summation. Only residual is the **veto stage** for hard-constraint rejection (lines 584–590).
- **Impl impact:** Significant. No bidder/recipient terminology. Region kernels compute partial fluxes; flux-sum aggregates; veto rejects impossible. No proportional-distribution or eligibility-mask logic.

### Δ6 (MEDIUM-HIGH) — Single-cell pressure → per-phase pressure (deviation-from-center)
- **Earlier:** SESSION_LOG §6 line 134: `pressure: u16` (one per cell, within current phase's log scale).
- **Gen5:** Line 246: pressure is "deviation from the phase density equilibrium center" — per phase, since each phase has its own center. Mixed cells need per-phase pressure.
- **Impl impact:** Pressure may need to be 4× wider (one per phase). Or there is one cell-level pressure per phase that's only meaningful per-phase. Open question flagged in §1.4.

### Δ7 (MEDIUM-HIGH) — Cohesion is per-direction transient, not per-pair persistent
- **Earlier:** wiki/cohesion.md (per project conventions): cohesion treated as a between-cell property.
- **Gen5:** Lines 350–373: cohesion is a per-cell per-direction scalar, blind to neighbor's cohesion, recomputed each cycle, never persisted.
- **Impl impact:** No persistent cohesion field in cell state. Computed inside region kernel and used only there. Asymmetric behavior emerges from blind summation.

### Δ8 (MEDIUM-HIGH) — Petals (6 per cell) are persistent directional state, separate from flux records
- **Earlier:** Implicit in earlier docs; not formalized as "petal" in SESSION_LOG.
- **Gen5:** §Petal data lines 338–348: 6 petals per cell, holding directional stress + momentum + topology metadata. Distinct from flux records (which are per-cycle scratch).
- **Impl impact:** New per-cell directional-state field. 6 × (stress + momentum + topology) per cell. Flux records update petals during integration.

### Δ9 (MEDIUM) — Pressure encoding is "log-scale u16" with phase-density-deviation semantic, not the phase-encoded packed u16 from SESSION_LOG
- **Earlier:** SESSION_LOG §5 lines 113–121: `bits 0-11: mantissa, bits 12-15: phase_offset/mohs_level`. Packed encoding includes phase context.
- **Gen5:** Line 246: log-scale `u16`, deviation from phase-density-equilibrium center. Phase context comes from phase-fraction masses, not packed into the pressure encoding.
- **Impl impact:** Different encode/decode routines. Phase information is no longer co-encoded with pressure.

### Δ10 (MEDIUM) — Sorting ruleset at edges is a NEW concept; cells are explicitly indivisible
- **Earlier:** Not present in earlier docs.
- **Gen5:** §Cells are indivisible, lines 467–493: a 30%-liquid-70%-gas cell does NOT store "liquid is at the bottom"; sorting is a pure function at flux-compute time using cell composition + edge direction + gravity vector.
- **Impl impact:** New function `sort(...)` invoked per edge per region-kernel. Couples to gravity (§9) and cohesion (§7.5). Without this, mixed-phase fluxes don't get oil-floats-on-water behavior.

### Δ11 (MEDIUM) — Gravity is a Jacobi-diffused vector field, not a scenario constant
- **Earlier:** wiki/gravity.md: simpler treatment (likely a global-direction or per-axis constant).
- **Gen5:** §Gravity as a first-class diffused vector field, lines 496–545: vector field with Jacobi diffusion, border-seeded by Newton-law contributions from point sources, applied only to cells with motion above ε.
- **Impl impact:** Significant new subsystem. Two `f32` fields per cell (2D), point-source border seeding, Jacobi sweep each cycle, motion-gated application. Multi-source Lagrange points emerge for free.

### Δ12 (MEDIUM) — Identity is computed-not-stored, and per-cycle
- **Earlier:** SESSION_LOG §6 line 134: `phase: u2` is stored. Material ID is stored.
- **Gen5:** Lines 316–321: identity is computed each cycle from current state. No "type" flag.
- **Impl impact:** No phase enum field. No state-change events. Identity computed by a function call.

### Δ13 (MEDIUM) — Pass count of plasma is 3 (gen5 introduces plasma cleanly)
- **Earlier:** SESSION_LOG §1 line 38: gas 3, liquid 5, solid 7. Plasma not enumerated.
- **Gen5:** Lines 263–268: plasma 3 sub-passes, opportunistic, density center 42 (same as gas).
- **Impl impact:** Plasma is a first-class fourth phase. Pass scheduler needs the fourth entry.

### Δ14 (LOW-MEDIUM) — "Render gate" semantics moved
- **Earlier:** SESSION_LOG §1 line 38: Stage 1 is the render gate.
- **Gen5:** Lines 670–675: Render sync is at end of cycle (post-re-encoding), and decoupled from sim cadence (renderer runs at display framerate).
- **Impl impact:** Different point in the pipeline; no Stage 1 to gate against. Render sync hooks into the post-cycle render-sync step instead.

### Δ15 (LOW) — Cell struct size grew significantly (~14B → ~120B packed)
- **Earlier:** SESSION_LOG §6 line 143: ~14 bytes/cell, 480×270 = 1.8 MB.
- **Gen5:** Lines 99–107: ~120 bytes packed/double-buffered. 9500×9500 grid on 24 GB card.
- **Impl impact:** Memory footprint is 8.5× the earlier design's per-cell. Reflects 16-slot composition + petals + phase-distribution + per-phase masses + sustained-overpressure + overburden field.

### Δ16 (LOW) — "Dead-band seeking" framing is gone
- **Earlier:** SESSION_LOG §2: "Dead-band seeking replaces winner-takes-all."
- **Gen5:** No mention of dead-band seeking. Pressure-driven flux is just "flux is proportional to gradient × phase-rule × cohesion damping × sorting weight."
- **Impl impact:** Vocabulary shift; same first-principle (Fick's-law diffusion) is preserved. No code-level impact beyond renaming.

### Δ17 (LOW) — Element table strategy unchanged
- **Earlier:** SESSION_LOG §5, §7: 118 elements + compound aliases, NIST-sourced.
- **Gen5:** Lines 458–463: same.
- **Impl impact:** No change.

### Δ18 (LOW) — Mohs ratchet exothermicity confirmed and restated
- **Earlier:** SESSION_LOG §4 line 84: "compression work dumped into energy field (ratcheting is exothermic — metamorphic rock is hot)."
- **Gen5:** Line 449: same statement, identical semantics.
- **Impl impact:** No change.

---

## Summary table — what to build first for the Python reference

The implementation order suggested by gen5's architecture:

| Order | Subsystem | Spec sections | M-milestone fit |
|-------|-----------|---------------|-----------------|
| 1 | Per-cell SoA arrays (f32 working) | §1, §16.4 | M3-a (done in tree) |
| 2 | Region kernel skeleton (7-cell flower, edge-centric flux scratch) | §4 | M3-c |
| 3 | Phase-fraction masses + density centers | §1.3, §2.1 | M3 |
| 4 | Sub-pass loop with budgets and double-buffering | §5, §14 | M3-d |
| 5 | Identity computation function | §3 | M3-e |
| 6 | Phase-specific flux rules (gas first, then liquid, then solid) | §6 | M4 |
| 7 | Phase transitions via 2D phase-diagram lookup | §10 | M4 |
| 8 | Cohesion + sorting | §7, §8 | M5 |
| 9 | Mohs ratcheting + sustained-overpressure integrator + overburden | §11 | M5 |
| 10 | Gravity vector field with border seeding | §9 | M6 |
| 11 | Borders / boundary conditions table | §13 | M6 |
| 12 | Scenario bounds validator | §17 | M6 |
| 13 | Memory tiers, GPU patterns | §15, §16 | M9 (CUDA) |

---

## Open questions consolidated (to flag for human decision before M3 close)

1. **§1.1**: Concrete element-similarity metric for 17th-element merge. Default placeholder: atomic-number distance.
2. **§1.3**: Relationship between phase fraction `f` and phase mass `m`. Default placeholder: `f` derived from `m` via per-element density per cycle.
3. **§1.4**: Per-phase pressure (4 floats per cell) vs single cell pressure. Default placeholder: per-phase, since deviation-from-center is per-phase.
4. **§1.6**: Per-component mohs uses composition slot index, or separate solid-only index? Default placeholder: reuse composition slot.
5. **§3**: Identity computation — majority-by-mass vs majority-by-fraction-of-equilibrium (gen5 §Shelved). Default placeholder: majority-by-mass.
6. **§3**: Unified identity vs per-purpose (gen5 §Shelved). Default placeholder: unified single function for M3-M5; revisit M6+.
7. **§4.4**: Blind summation — per-region scratch then reduce, vs scatter-add directly. Default placeholder: scatter-add for Python reference.
8. **§7.2**: Concrete formulas for `f`, `g`, `shared_majority_match`, `purity`. Default placeholder: `purity = max_fraction`, `match = sum(self.frac × neighbor.frac over shared elements)`, `f` and `g` identity.
9. **§11.5**: Overburden field incremental update rule. Default placeholder: full recomputation each cycle for Python reference; optimize later.

These defaults let M3-M5 proceed without blocking on human decisions; each is flagged for revisit when the relevant subsystem matures.
