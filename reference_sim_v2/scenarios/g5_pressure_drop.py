"""
Scenario: g5_pressure_drop — first real flux test.

91-cell hex disc, all Si liquid at the equilibrium center. Center cell
gets a non-zero pressure_raw value (deviation above equilibrium); the
neighbouring cells stay at zero deviation. Per the M5'.3 region kernel,
this drives mass to flow out of the elevated cell along all six bonds,
proportional to ΔP × cohesion × phase_fraction × K_liquid × dt.

Si liquid is used (not solid) because gen5 commits solid to non-
opportunistic mass transport — the M5'.3 stub region kernel does a simple
Fickian model that produces visible flow only with a non-trivial phase
conductance. K_liquid is moderate; M5'.5 will replace this with a
phase-correct kernel (solid: yield-event displacement; liquid: gravity-
biased; gas: opportunistic averaging).

Expected over a few ticks:
  - Center cell phase_mass[liquid] decreases monotonically
  - Each ring-1 cell's phase_mass[liquid] increases by an equal amount
  - Total Si liquid mass conserved exactly
  - Composition fraction stays 100% Si everywhere
  - All standard invariants hold

This scenario is the M5'.3 commitment: the region kernel + flux summation
+ integration pipeline produces conservative mass redistribution.
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_LIQUID,
    set_single_element,
)
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "g5_pressure_drop"
RINGS = 5
DEFAULT_MOHS = 0                       # liquid: mohs unused
INITIAL_T_K = 2500.0                   # liquid Si range
ELEVATED_PRESSURE_RAW = 5000           # arbitrary u16 deviation; M5'.3 stub decode is identity
DEFAULT_PRESSURE_RAW = 0


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cell_size_m = 0.01
    volume = cell_size_m ** 3
    mass = si.density_liquid * volume
    cp_mass = mass * si.specific_heat_liquid
    initial_energy_raw = int(round(cp_mass * INITIAL_T_K / si.energy_scale))
    assert 0 < initial_energy_raw <= 0xFFFF, f"initial_energy_raw {initial_energy_raw} out of u16"

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase_fraction[cell_id, PHASE_LIQUID] = 1.0
        cells.phase_mass[cell_id, PHASE_LIQUID]     = float(EQUILIBRIUM_CENTER[PHASE_LIQUID])
        cells.energy_raw[cell_id]                   = initial_energy_raw
        cells.mohs_level[cell_id]                   = DEFAULT_MOHS
        cells.flags[cell_id]                        = 0
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    # Elevated pressure on the center cell
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
        element_table=element_table,
        allowed_elements=("Si",),
        description=(
            f"91-cell Si liquid disc at T≈{INITIAL_T_K:g} K. Center cell at "
            f"pressure_raw={ELEVATED_PRESSURE_RAW} (deviation), all others "
            "at 0. M5'.3 region kernel drives mass flow from center to "
            "ring-1 neighbours. Total Si liquid mass conserved exactly."
        ),
    )
