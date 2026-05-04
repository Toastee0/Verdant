"""
Scenario: t1_water_pressure_drop — Tier 1 multi-element mass flow.

Tier 1 analog of g5_pressure_drop: 91-cell liquid water disc with the
center cell at elevated pressure_raw. Mass flux drives water from the
high-pressure center to the lower-pressure ring-1 neighbours, with
H AND O components transported proportionally to the cell composition
(both 114/255 and 141/255 fractions of the moving mass go to each
neighbour).

Validates:
  - Tier 1 multi-element transport correctness (M5'.3 region kernel
    handles 16-slot composition × 4-phase channels; this is the first
    scenario that uses two slots simultaneously).
  - Per-element mass conservation (H AND O totals invariant across
    multi-cell flux).
  - Recipient cells gain identical fractional H / O ratios → composition
    stays "water" everywhere (no separation).
  - Phase resolve doesn't oscillate (uniform liquid; T below boil but
    above melt → target = liquid for all cells; no transitions fire).
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_LIQUID,
    Q_KG,
)
from ..compounds import set_compound
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc
from ..phase_diagram import load_phase_diagram
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t1_water_pressure_drop"
RINGS = 5
INITIAL_T_K = 320.0     # safely within liquid range
ELEVATED_PRESSURE_RAW = 5000


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
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
    density_l = f_h * h.density_liquid + f_o * o.density_liquid
    cp_l      = f_h * h.specific_heat_liquid + f_o * o.specific_heat_liquid
    initial_energy_raw = encode_energy_J_scalar(density_l * volume * cp_l * INITIAL_T_K)
    EQ_LIQUID_water = density_l * volume / Q_KG

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_compound(cells, cell_id, compound_id=200, element_table=table)
        cells.phase_fraction[cell_id, PHASE_LIQUID] = 1.0
        cells.phase_mass[cell_id, PHASE_LIQUID]     = float(EQ_LIQUID_water)
        cells.energy_raw[cell_id]                   = initial_energy_raw
        cells.mohs_level[cell_id]                   = 0
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    cells.pressure_raw[0] = ELEVATED_PRESSURE_RAW

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
            "91-cell liquid water disc. Center cell at elevated "
            f"pressure_raw={ELEVATED_PRESSURE_RAW}, all others at 0. "
            "Validates Tier 1 multi-element mass flux: H AND O move "
            "together proportional to composition; conservation per "
            "element across all phases."
        ),
    )
