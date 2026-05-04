"""
Scenario: t1_condensation — in-place gas→liquid phase transition.

Per the gen5 Q3 verdict on cross-phase asymmetry: condensation does NOT
flow through the sorting-ruleset extension. It happens in place, in the
source cell, via apply_phase_transitions when the phase-diagram lookup
at the cell's (T, P) returns a phase lower-energy than the phase that
currently holds mass. Cohesion-driven liquid migration toward existing
liquid cells is a downstream behaviour that requires either signed-
pressure decoding (so an under-dense liquid cell can register negative
pressure deviation and pull mass in) or an explicit cohesion-attraction
term in the region kernel — both M6'.x calibration work, deferred.

This scenario validates the in-place path:

  91-cell humid-air disc identical to t1_humidity, except T is set to
  350 K — between water's freeze (273.15 K) and boil (373.15 K), so the
  phase-diagram lookup returns LIQUID. Every cell holds water mass in
  its GAS channel; that mass is in the wrong phase for the cell's (T, P).
  apply_phase_transitions converts a fraction of GAS → LIQUID per cycle
  (capped at 1/16 by the energy-balance cap until M6'.x compound
  calibration catches up), releases latent heat L_v × Δm_kg into
  energy_raw, and updates phase_fraction proportionally.

The cross-phase sorting ruleset stays sentinel for these cells: dst_phase
(LIQUID at neighbour) < src_phase (GAS at source) so the asymmetry rule
defers — flux.dst_phase_per_slot[A, d, slot] = 255 across the board.
The fact that no edges fire cross-phase routing IS the thing being
validated; condensation is an in-cell event by construction.

Validates:
  - apply_phase_transitions converts GAS → LIQUID per cycle in every
    cell (uniform initial state ⇒ uniform per-cycle conversion).
  - mass_per_element_total: H and O totals invariant across all phases
    (1:1 hex-unit transfer between gas and liquid channels).
  - phase_fraction shifts: phase_fraction[GAS] decreases, [LIQUID]
    increases each cycle.
  - Cross-phase asymmetry: integration treats src=GAS, dst=LIQUID edges
    as same-phase routing (dst < src ⇒ asymmetry rule defers). The
    sorting-ruleset extension does NOT route mass across cells on
    condensation edges; condensation is in-cell by construction.

Open calibration caveat (M6'.x) — T crashes after the transition fires.
The `derive.compute_thermal_blends` formula uses phase_fraction-weighted
density to derive cell mass for T = E/(m × cp). Liquid water density is
~800× gas water density, so when phase_fraction shifts even slightly
toward LIQUID the computed mass jumps and T plummets. The hex-unit
phase_mass representation is meant to be fungible across phase channels
(1:1 transfer preserves "amount of substance"), but the kg-derivation
from phase_fraction × density doesn't honour that. Resolving this is a
foundational calibration question: either (a) phase_mass-based mass
formula in derive (still has a smaller jump because liquid hex-per-kg
factor differs from gas's), or (b) renormalising phase_mass so 1 hex
unit is universal kg regardless of phase channel, with EQ_PHASE
recomputed from per-element densities. Both touch every scenario's init
energy. Until that lands, this scenario simply demonstrates that the
asymmetry rule fires (no cross-phase routing on condensation) and leaves
the post-transition T drift as a documented artefact.
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_GAS,
    Q_KG,
)
from ..compounds import set_compound
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc
from ..phase_diagram import load_phase_diagram
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t1_condensation"
RINGS = 5
COND_T_K = 350.0                 # below 373.15 K boil, above 273.15 K freeze → phase_diagram = LIQUID
GAS_MASS_FRAC = 0.10             # 10% of EQ_GAS — under-saturated humid air


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    repo_root = Path(__file__).resolve().parent.parent.parent
    table = load_element_table(repo_root / "data" / "element_table.tsv")
    h2o = load_phase_diagram(repo_root / "data" / "phase_diagrams" / "H2O.csv")
    si  = load_phase_diagram(repo_root / "data" / "phase_diagrams" / "Si.csv")
    phase_diagrams = {
        table["H"].element_id:  h2o,
        table["O"].element_id:  h2o,
        table["Si"].element_id: si,
    }

    h = table["H"]; o = table["O"]
    f_h = 114 / 255.0
    f_o = 141 / 255.0
    cell_size_m = 0.01
    volume = cell_size_m ** 3

    # Per-cell physical mass + cp for humid (gas-phase) water blend at T_cond.
    # Per gen5 phase_mass↔kg semantics: EQ_GAS_water = density × volume / Q_KG
    # and the cell's actual mass at GAS_MASS_FRAC saturation is
    # GAS_MASS_FRAC × density × volume.
    density_g = f_h * h.density_gas_stp + f_o * o.density_gas_stp
    cp_g      = f_h * h.specific_heat_gas + f_o * o.specific_heat_gas
    EQ_GAS_water = density_g * volume / Q_KG
    mass_g_kg = GAS_MASS_FRAC * density_g * volume
    energy_J = mass_g_kg * cp_g * COND_T_K
    initial_energy_raw = encode_energy_J_scalar(energy_J)

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_compound(cells, cell_id, compound_id=200, element_table=table)
        cells.phase_fraction[cell_id, PHASE_GAS] = 1.0
        cells.phase_mass[cell_id, PHASE_GAS]     = (
            GAS_MASS_FRAC * float(EQ_GAS_water)
        )
        cells.pressure_raw[cell_id]              = 0
        cells.energy_raw[cell_id]                = initial_energy_raw
        cells.mohs_level[cell_id]                = 0
        cells.flags[cell_id]                     = 0
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    world = WorldConfig(
        dt=1.0 / 128.0,
        gravity_sources=(),
        noise_floor_epsilon=1e-4,
        cell_size_m=cell_size_m,
    )

    emission = EmissionConfig(
        mode=emission_mode,
        output_dir=Path(output_dir) if output_dir else None,
        include_petals=True,
        include_gravity_vec=False,
        include_cohesion=False,
    )

    return Scenario(
        name=SCENARIO_NAME,
        grid=grid,
        cells=cells,
        world=world,
        emission=emission,
        element_table=table,
        allowed_elements=("H", "O"),
        phase_diagrams=phase_diagrams,
        description=(
            f"91-cell humid-air disc at T={COND_T_K:g} K (below 373.15 K "
            "boil → phase_diagram = LIQUID). Water mass starts in the "
            f"GAS channel ({GAS_MASS_FRAC*100:g}% of EQ_GAS); "
            "apply_phase_transitions converts GAS → LIQUID per cycle "
            "(in-place, source-pays latent heat). Validates the gen5 Q3 "
            "asymmetry: cross-phase sorting ruleset stays sentinel "
            "(dst < src ⇒ defer to in-place transitions), gas → liquid "
            "happens entirely within each cell."
        ),
    )
