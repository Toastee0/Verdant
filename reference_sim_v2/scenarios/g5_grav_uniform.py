"""
Scenario: g5_grav_uniform — gravity Jacobi sanity test.

A single point source far below the disc, mass tuned to produce ~9.8 m/s²
at the disc surface. Border cells get Newton contributions; Jacobi
diffusion fills the interior. The expected gravity field is approximately
uniform downward (-y).

Validation:
  - At all cells: g ≈ (0, -9.8 m/s²) within ~1% tolerance.
  - Field is finite (no NaN, no Inf).
  - Border vectors are stable across ticks (gravity recomputed each cycle
    is deterministic from the same source list).
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_SOLID,
    set_single_element,
)
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, GravitySource, Scenario, WorldConfig


SCENARIO_NAME = "g5_grav_uniform"
RINGS = 5
DEFAULT_MOHS = 6
DEFAULT_ENERGY_RAW = 300

# Source ~10 m below the disc; mass tuned for ~9.8 m/s² at the disc.
# g = G·M/d² ⇒ M = g·d² / G
SOURCE_DISTANCE_M = 10.0
TARGET_G = 9.8


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase_fraction[cell_id, PHASE_SOLID] = 1.0
        cells.phase_mass[cell_id, PHASE_SOLID]     = float(EQUILIBRIUM_CENTER[PHASE_SOLID])
        cells.pressure_raw[cell_id]                = 0
        cells.energy_raw[cell_id]                  = DEFAULT_ENERGY_RAW
        cells.mohs_level[cell_id]                  = DEFAULT_MOHS
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    # Source far below the disc (negative y in axial-cartesian)
    G_CONST = 6.674e-11
    source_mass = TARGET_G * (SOURCE_DISTANCE_M ** 2) / G_CONST
    source = GravitySource(position=(0.0, -SOURCE_DISTANCE_M), mass_kg=source_mass)

    world = WorldConfig(
        dt=1.0 / 128.0,
        gravity_sources=(source,),
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
        include_gravity_vec=True,    # this scenario's whole point
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
            f"91-cell Si solid disc with a point gravity source at (0, "
            f"-{SOURCE_DISTANCE_M:g} m), mass tuned for ~{TARGET_G:g} m/s² "
            "at the disc surface. Validates gravity vector field Jacobi "
            "diffusion: expected uniform downward gravity, ~1% variation."
        ),
    )
