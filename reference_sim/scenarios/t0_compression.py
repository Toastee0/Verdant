"""
Scenario: t0_compression

A single elevated-strain cell at the center of an otherwise rest-state Si
solid disc. Tests Stage 2 elastic-strain Jacobi propagation through the
cohesion network: the center's strain disperses outward over sub-iterations
within one tick, and across ticks as the Jacobi sweep converges.

Why strain, not pressure: Tier 0 is single-element Si; the mass-auction in
Stage 3 has no headroom on a uniform solid disc (every cell at fraction
255 with cohesion barriers blocking flow), so "compression equilibration"
on a Tier 0 substrate is mechanically a strain-equilibration test on the
cohesion graph (wiki/elastic-flow.md). Tier 1+ scenarios with mixed
composition will exercise the mass auction's pressure-equalization path.

Expected:
- Mass conserved exactly per element every tick (no Stage 3 flow on
  intact-cohesive single-element solid).
- Center cell's elastic_strain decreases monotonically over ticks.
- Ring-1 cells' strain rises proportionally (Jacobi spreads the load).
- pressure_raw / energy / phase / composition all unchanged.
- Composition_sum_255 invariant holds at every tick (all cells full Si).
- No fracture (initial +60 strain stays well below ±127 saturation, and no
  bond's |Δε| reaches the tensile-limit/elastic-limit threshold).
"""

from __future__ import annotations

from pathlib import Path

from ..cell import CellArrays, set_single_element, PHASE_SOLID
from ..element_table import load_element_table
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t0_compression"
RINGS = 5
DEFAULT_MOHS = 5
DEFAULT_ENERGY_J = 300
INITIAL_STRAIN_CENTER = 60   # i8 in [-127, +127]; well below saturation


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    """Construct t0_compression."""
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase[cell_id] = PHASE_SOLID
        cells.mohs_level[cell_id] = DEFAULT_MOHS
        cells.pressure_raw[cell_id] = 0
        cells.energy[cell_id] = DEFAULT_ENERGY_J
        cells.flags[cell_id] = 0
        cells.elastic_strain[cell_id] = 0
        cells.magnetization[cell_id] = 0

    # Center cell (cell_id 0 by hex_disc ordering: ring 0)
    cells.elastic_strain[0] = INITIAL_STRAIN_CENTER

    world = WorldConfig(
        dt=1.0 / 128.0,
        g_sim=0.0,
        t_space=2.7,
        solar_flux=0.0,
        magnetism_enabled=False,
    )

    emission = EmissionConfig(
        mode=emission_mode,
        output_dir=Path(output_dir) if output_dir else None,
        include_bids=False,
        include_gradients=False,
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
            "91-cell hex disc, all Mohs-5 Si solid. Center cell starts at "
            f"elastic_strain={INITIAL_STRAIN_CENTER} (compressed); rest at 0. "
            "Stage 2 propagates strain through the cohesion network. "
            "Mass is conserved exactly (Stage 3 silent on intact single-element "
            "solid); strain monotonically equilibrates across the disc."
        ),
    )
