"""
Scenario: t0_fracture

Tensile bond failure on a single bond at the disc center. Two adjacent
cells start with opposing extreme strain: one in deep tension, one in deep
compression but JUST below the +127 ratchet sentinel so Stage 1 doesn't
consume it. The resulting Δε across their shared bond produces a stress
greater than tensile_limit, and Stage 2's per-bond fracture detection
flags both endpoints as FRACTURED.

For Si (element_table values):
    elastic_limit = 1.20e8 Pa
    tensile_limit = 1.30e8 Pa
    bond_stress = elastic_limit × |Δstrain_i8| / 127

To exceed tensile_limit:
    |Δstrain_i8| > 127 × 1.30e8 / 1.20e8 ≈ 137.6

So we use cell A = -127 and cell B = +120; |Δ| = 247 ⇒ bond_stress ≈ 2.33e8 Pa,
well above tensile_limit. Cell B is below the +127 sentinel, so Stage 1
does NOT ratchet it, and Stage 2 sees the full 247-unit gradient.

Expected (tick 1 emission):
- cells 0 and 1: flags.fractured=true
- cells 0 and 1: elastic_strain=0 (snapped spring released)
- All other cells unchanged.
- Mass conserved exactly.
"""

from __future__ import annotations

from pathlib import Path

from ..cell import CellArrays, set_single_element, PHASE_SOLID
from ..element_table import load_element_table
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t0_fracture"
RINGS = 5
DEFAULT_MOHS = 5
DEFAULT_ENERGY_J = 300
TENSION_STRAIN = -127        # cell A: full tension
COMPRESSION_STRAIN = 120     # cell B: just below ratchet sentinel +127


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
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

    # Cell 0: center, deep tension. Cell 1: ring-1 east, near-saturation
    # compression but below the ratchet sentinel.
    cells.elastic_strain[0] = TENSION_STRAIN
    cells.elastic_strain[1] = COMPRESSION_STRAIN

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
            "91-cell hex disc, Mohs-5 Si solid. Cells 0 and 1 start at "
            f"elastic_strain {TENSION_STRAIN} and {COMPRESSION_STRAIN} respectively. "
            "Stage 2's per-bond stress check exceeds tensile_limit at the (0,1) "
            "bond; both cells get flags.fractured set and their strain released."
        ),
    )
