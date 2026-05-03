"""
Scenario: t1_ice_melt — Tier 1 phase transition demo.

91-cell water disc starting in MIXED phase: 50% solid (ice) + 50% liquid
water in every cell. Energy chosen so that the composition-weighted
temperature lands in the liquid range (T > 273 K) — phase diagram says
target = liquid; rate-limited partial transitions push solid mass into
the liquid channel each cycle (1/16 per cycle).

Why mixed-state init rather than "ice cube heated until melt fires":
the (H, 114) + (O, 141) compound recipe gives blended properties that
don't quite match real H₂O — c_p_l blend ≈ 5410 vs real 4186, density_l
≈ 663 vs real 1000, L_fusion ≈ 800 kJ/kg vs real 333.6 kJ/kg. With
those numbers, no single energy_raw value places T_solid below melt
AND T_liquid above melt simultaneously (the cp-discontinuity gap is
too wide). So a scenario that "heats ice until it melts" oscillates.
M6'.x calibration will revisit; for M6'.1 demonstration the mixed-state
init shows the transition mechanism cleanly for the first several
cycles before the c_p shift drags T below melt and the direction
reverses.

Validation:
  - tick 0: every cell has phase_mass split 50/50 solid/liquid
  - tick 1+: solid_mass decreases, liquid_mass increases monotonically
    until T crosses below melt threshold
  - Mass per element conserved exactly (H AND O totals invariant
    across the channel shifts)
  - composition_sum_255 holds throughout
  - mohs follows solid component (mohs > 0 while solid_mass > 0;
    drops to 0 once fully liquid)
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_LIQUID,
    PHASE_SOLID,
)
from ..compounds import set_compound
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc
from ..phase_diagram import load_phase_diagram
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t1_ice_melt"
RINGS = 5
INITIAL_T_K = 290.0     # liquid range; phase resolve target = liquid


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    repo_root = Path(__file__).resolve().parent.parent.parent
    table = load_element_table(repo_root / "data" / "element_table.tsv")
    h2o = load_phase_diagram(repo_root / "data" / "phase_diagrams" / "H2O.csv")
    si = load_phase_diagram(repo_root / "data" / "phase_diagrams" / "Si.csv")
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

    # 50/50 phase blend properties
    density_blend = 0.5 * (f_h * h.density_solid  + f_o * o.density_solid) \
                  + 0.5 * (f_h * h.density_liquid + f_o * o.density_liquid)
    cp_blend = 0.5 * (f_h * h.specific_heat_solid  + f_o * o.specific_heat_solid) \
             + 0.5 * (f_h * h.specific_heat_liquid + f_o * o.specific_heat_liquid)
    mass_kg = density_blend * volume
    energy_J = mass_kg * cp_blend * INITIAL_T_K
    initial_energy_raw = encode_energy_J_scalar(energy_J)

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_compound(cells, cell_id, compound_id=200, element_table=table)
        cells.phase_fraction[cell_id, PHASE_SOLID]  = 0.5
        cells.phase_fraction[cell_id, PHASE_LIQUID] = 0.5
        cells.phase_mass[cell_id, PHASE_SOLID]      = 0.5 * float(EQUILIBRIUM_CENTER[PHASE_SOLID])
        cells.phase_mass[cell_id, PHASE_LIQUID]     = 0.5 * float(EQUILIBRIUM_CENTER[PHASE_LIQUID])
        cells.energy_raw[cell_id]                   = initial_energy_raw
        cells.mohs_level[cell_id]                   = 2   # ice mohs from H2O.csv
        cells.flags[cell_id]                        = 0
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
            "91-cell water disc starting 50/50 solid+liquid mix at T≈290 K. "
            "Phase resolve (T > melt 273) targets liquid; rate-limited "
            "transitions move 1/16 of solid mass to liquid each cycle. "
            "Validates Tier 1 phase transitions on multi-element compound "
            "compositions; calibration of L_blend / c_p discontinuity is "
            "M6'.x work."
        ),
    )
