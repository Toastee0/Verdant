"""
Scenario: t0_static

The simplest possible Tier 0 scenario. 91-cell hex disc, every cell is a
Mohs-5 solid Silicon cell at its dead-band center pressure. Nothing is
displaced, nothing is hotter than anything else, no walls, no gravity
effect worth noting.

Expected behavior: zero deltas every tick. Mass conserved exactly. Energy
conserved exactly (no radiation, no ratcheting, no flows). Every invariant
should PASS across any number of ticks.

If this scenario produces any non-trivial dynamics, something is wrong in
the baseline of the sim — Stage 0 or Stage 5 is mis-behaving. Use this as
the hello-world correctness test.
"""

from __future__ import annotations

from pathlib import Path

from ..cell import CellArrays, set_single_element, PHASE_SOLID
from ..element_table import load_element_table
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t0_static"
RINGS = 5
DEFAULT_MOHS = 5
DEFAULT_ENERGY_J = 300  # placeholder uniform thermal content (arbitrary small value)


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    """Construct the t0_static scenario.

    Args:
        output_dir:    where JSON emissions go. None = don't write to disk.
        emission_mode: "off" | "tick" | "stage" | "cycle" | "violation"
    """
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91, f"expected 91-cell disc, got {grid.cell_count}"

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase[cell_id] = PHASE_SOLID
        cells.mohs_level[cell_id] = DEFAULT_MOHS
        cells.pressure_raw[cell_id] = 0     # at or below dead-band center
        cells.energy[cell_id] = DEFAULT_ENERGY_J
        cells.flags[cell_id] = 0            # no walls, no transients
        cells.elastic_strain[cell_id] = 0
        cells.magnetization[cell_id] = 0

    world = WorldConfig(
        dt=1.0 / 128.0,
        g_sim=0.0,                    # no gravity; hello-world
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
            "91-cell hex disc, all cells uniformly Mohs-5 Si solid at "
            "pressure_raw=0, energy=300 J. No walls, no gravity. "
            "Expected behavior: zero deltas every tick; perfect conservation."
        ),
    )
