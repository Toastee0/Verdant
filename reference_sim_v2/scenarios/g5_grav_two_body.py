"""
Scenario: g5_grav_two_body — Lagrange-like neutral line.

Two equal-mass point sources at (-D, 0) and (+D, 0) on opposite sides of
the disc. By symmetry, cells along the y-axis (q=0) have gravity vectors
that cancel to ≈ zero. Cells off-axis are pulled toward the nearer source.

Validation:
  - Center cell (q=0, r=0): |g| < 1% of single-source magnitude.
  - Cells along the q=0 axis: gx ≈ 0, gy ≈ 0.
  - Off-axis cells (q != 0): non-zero gravity; symmetric around the axis.
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
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, GravitySource, Scenario, WorldConfig


SCENARIO_NAME = "g5_grav_two_body"
RINGS = 5
SOURCE_DISTANCE_M = 5.0
PER_SIDE_TARGET_G = 9.8 / 2.0   # so single-source is ~4.9 m/s²


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase_fraction[cell_id, PHASE_SOLID] = 1.0
        cells.phase_mass[cell_id, PHASE_SOLID]     = float(EQUILIBRIUM_CENTER[PHASE_SOLID])
        cells.energy_raw[cell_id]                  = encode_energy_J_scalar(300.0)
        cells.mohs_level[cell_id]                  = 6
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    G_CONST = 6.674e-11
    source_mass = PER_SIDE_TARGET_G * (SOURCE_DISTANCE_M ** 2) / G_CONST

    world = WorldConfig(
        dt=1.0 / 128.0,
        gravity_sources=(
            GravitySource(position=(-SOURCE_DISTANCE_M, 0.0), mass_kg=source_mass),
            GravitySource(position=(+SOURCE_DISTANCE_M, 0.0), mass_kg=source_mass),
        ),
        noise_floor_epsilon=1e-4,
        cell_size_m=0.01,
    )

    emission = EmissionConfig(
        mode=emission_mode,
        output_dir=Path(output_dir) if output_dir else None,
        include_petals=True,
        include_gravity_vec=True,
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
            "91-cell Si solid disc with two equal-mass point gravity sources "
            f"at (±{SOURCE_DISTANCE_M:g} m, 0). Expected: vector cancellation "
            "along the q=0 axis (Lagrange-like neutral line). Validates "
            "multi-source gravity field summation through Jacobi diffusion."
        ),
    )
