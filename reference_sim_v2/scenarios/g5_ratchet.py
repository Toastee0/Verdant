"""
Scenario: g5_ratchet — sustained-overpressure ratchet validation.

91-cell hex disc, all Si solid at low T (well below melt). Center cell
has a sustained high pressure deviation (raw=15000 vs threshold=1000).
Each cycle, apply_ratchet integrates the over-threshold excess into the
sustained_overpressure f32 register; once it crosses RATCHET_TRIGGER, the
cell ratchets:
  - mohs_level → mohs_level + 1 (capped at MOHS_MAX=10)
  - RATCHETED flag set for that cycle
  - energy_raw += compression-work raw delta (small but observable)
  - sustained_overpressure reset to 0

After the ratchet fires, the cell starts re-integrating from 0; with the
same overpressure it will fire again in roughly the same number of cycles.

Excess accumulation per cycle =
    (P_dev - threshold) × dt × LONGEST_BUDGET[7]  ≈ (15000 - 1000) × 0.0078 × 7 ≈ 765
Trigger: 10000. So roughly every ~13 cycles a ratchet fires.

Validation:
  - tick 0: cell0.mohs_level=6, sustained_overpressure=0
  - tick 1..N: sustained_overpressure climbs until trigger, then mohs++ and
    flag set on the trigger tick
  - Total Si mass conserved (no transitions, no flow on uniform pressure
    elsewhere; the elevated cell's pressure_raw drives sustained_overpressure
    only — no neighbour mass flow because solid K is near-zero)
  - mohs_level monotonic (never decreases)
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
from ..phase_diagram import load_phase_diagrams_for_table
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "g5_ratchet"
RINGS = 5
RATCHET_TARGET_CELL = 0
SUSTAINED_PRESSURE_RAW = 50000   # excess 49000; trigger 10000 fires ~tick 27


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    repo_root = Path(__file__).resolve().parent.parent.parent
    table = load_element_table(repo_root / "data" / "element_table.tsv")
    si = table["Si"]
    phase_diagrams = load_phase_diagrams_for_table(table, repo_root / "data" / "phase_diagrams")

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase_fraction[cell_id, PHASE_SOLID] = 1.0
        cells.phase_mass[cell_id, PHASE_SOLID]     = float(EQUILIBRIUM_CENTER[PHASE_SOLID])
        cells.energy_raw[cell_id]                  = 300   # low T → solid stays solid
        cells.mohs_level[cell_id]                  = 6
        cells.flags[cell_id]                       = 0
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    cells.pressure_raw[RATCHET_TARGET_CELL] = SUSTAINED_PRESSURE_RAW

    world = WorldConfig(
        dt=1.0 / 128.0,
        gravity_sources=(),
        noise_floor_epsilon=1e-4,
        cell_size_m=0.01,
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
        allowed_elements=("Si",),
        phase_diagrams=phase_diagrams,
        description=(
            f"91-cell Si solid disc, cold (T well below melt). Center "
            f"cell at sustained pressure_raw={SUSTAINED_PRESSURE_RAW} "
            "(deviation). Validates apply_ratchet: integrator climbs each "
            "cycle, fires mohs_level++ when threshold crossed."
        ),
    )
