"""
Scenario: g5_temp_gradient — derive-stage validation.

91-cell hex disc, uniform Si solid composition + phase distribution, but
energy_raw varies linearly across the disc to produce a temperature
gradient. No flow physics yet (M5'.3+) — this scenario exists purely to
validate that:

  - identity (majority phase + element) is uniform Si solid everywhere.
  - cohesion = 1.0 across all in-grid bonds; 0 at grid edges.
  - temperature_K matches manual T = energy_J / (mass_kg × c_p) per cell.
  - decoded pressure = 0 everywhere (raw=0, no deviation from equilibrium).

Mass conservation invariant trivially holds (no transitions, no flow).
Energy total stays constant cycle to cycle (no flow).
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PHASE_SOLID,
    PETAL_TOPO_IS_GRID_EDGE,
    set_single_element,
)
from ..grid import build_hex_disc, ring_of
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "g5_temp_gradient"
RINGS = 5
DEFAULT_MOHS = 6
ENERGY_COLD_RAW = 100      # outermost cells
ENERGY_HOT_RAW  = 2000     # center cell


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cells = CellArrays.empty(grid)
    for cell_id, coord in enumerate(grid.coords):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase_fraction[cell_id, PHASE_SOLID] = 1.0
        cells.phase_mass[cell_id, PHASE_SOLID]     = float(EQUILIBRIUM_CENTER[PHASE_SOLID])
        cells.pressure_raw[cell_id]                = 0
        # Linear gradient by ring: ring 0 (center) hot, ring 5 (edge) cold
        ring = ring_of(coord)
        ratio = ring / RINGS                # 0..1 from center to edge
        e_raw = int(round(ENERGY_HOT_RAW * (1.0 - ratio) + ENERGY_COLD_RAW * ratio))
        cells.energy_raw[cell_id]                  = e_raw
        cells.mohs_level[cell_id]                  = DEFAULT_MOHS
        cells.sustained_overpressure[cell_id]      = 0.0
        cells.flags[cell_id]                       = 0
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    world = WorldConfig(
        dt=1.0 / 128.0,
        gravity_sources=(),
        noise_floor_epsilon=1e-4,
        t_space=2.7,
        solar_flux=0.0,
        magnetism_enabled=False,
        cell_size_m=0.01,
    )

    emission = EmissionConfig(
        mode=emission_mode,
        output_dir=Path(output_dir) if output_dir else None,
        include_petals=True,
        include_gravity_vec=False,
        include_cohesion=True,    # M5'.1 wants cohesion in the JSON for verifier checks
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
            "91-cell hex disc, uniform Si solid composition + phase, "
            f"linear energy gradient by ring (center={ENERGY_HOT_RAW}, "
            f"edge={ENERGY_COLD_RAW} raw u16). Validates derive: identity, "
            "cohesion, temperature, pressure decode."
        ),
    )
