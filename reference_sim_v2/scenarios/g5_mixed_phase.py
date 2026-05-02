"""
Scenario: g5_mixed_phase — concurrent per-phase scheduler validation.

91-cell hex disc, every cell carries BOTH solid and liquid Si phase
fractions (50/50 mix — gen5's "magma" / "wet sand" archetype). Center
cell elevated pressure_raw; expected: liquid phase fraction redistributes
faster (5 sub-passes/cycle), solid phase fraction redistributes slower
(7 sub-passes/cycle).

After enough ticks the asymmetric flux rates produce visibly different
liquid vs solid mass distributions:
  - Liquid concentrates faster around the gradient.
  - Solid lags — 7/5 ratio of sub-passes per cycle.

Invariants tested:
  - Per-phase mass conservation (each phase's total stays constant).
  - Phase-freeze: once liquid hits its 5-sub-pass budget, its phase_mass
    stops changing for the rest of the cycle. Solid keeps moving.
  - Composition stays 100% Si everywhere (single-element).
  - All standard checks pass.
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
    set_single_element,
)
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "g5_mixed_phase"
RINGS = 5
ELEVATED_PRESSURE_RAW = 5000


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cell_size_m = 0.01
    # Pick energy that doesn't trigger phase-resolve flips at M5'.3
    # (M5'.5 will manage transitions properly). For mixed solid/liquid
    # we use a temperature within the liquid coexistence range; T derive
    # is OK with mixed fractions.
    initial_energy_raw = 4000

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        # 50/50 mix of solid and liquid phase fractions. Each phase gets
        # half its equilibrium mass.
        cells.phase_fraction[cell_id, PHASE_SOLID]  = 0.5
        cells.phase_fraction[cell_id, PHASE_LIQUID] = 0.5
        cells.phase_mass[cell_id, PHASE_SOLID]      = 0.5 * float(EQUILIBRIUM_CENTER[PHASE_SOLID])
        cells.phase_mass[cell_id, PHASE_LIQUID]     = 0.5 * float(EQUILIBRIUM_CENTER[PHASE_LIQUID])
        cells.energy_raw[cell_id]                   = initial_energy_raw
        cells.mohs_level[cell_id]                   = 0
        cells.flags[cell_id]                        = 0
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
        element_table=element_table,
        allowed_elements=("Si",),
        description=(
            "91-cell mixed-phase Si disc (50% solid + 50% liquid by phase "
            "fraction in every cell). Center cell at elevated pressure. "
            "Validates concurrent per-phase scheduler: liquid budget = 5 "
            "sub-passes, solid budget = 7. Liquid redistributes faster; "
            "solid keeps flowing for two extra sub-passes per cycle."
        ),
    )
