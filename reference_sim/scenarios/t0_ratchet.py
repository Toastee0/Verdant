"""
Scenario: t0_ratchet

Aggressive compression on the center cell triggers a Mohs ratchet event at
Stage 1 of tick 1. Demonstrates the cross-tick deferred-overflow handshake:
the scenario seeds elastic_strain at +127 (the saturation sentinel chosen
in Step 1's design), and Stage 1's ratchet check consumes it — increments
mohs_level, sets the RATCHETED flag, dumps compression work into the
energy field, and resets strain to 0.

Expected (tick 1 emission):
- cell 0: phase=solid, mohs_level=6 (was 5), flags.ratcheted_this_tick=true
- cell 0: energy slightly elevated (compression work)
- cell 0: elastic_strain=0
- All other cells unchanged.
- Mass conserved exactly per element.

Tier 0 caveat: at Si energy_scale=1 J/unit and cell_size=0.01 m, the
elastic strain energy ½σy²/E·V ≈ 0.04 J — below u16 resolution. The
ratchet code applies a unit floor (raw ≥ 1) to ensure observability.
This is a Tier 0 contrivance; M5+ scenarios will use realistic scaling.
"""

from __future__ import annotations

from pathlib import Path

from ..cell import CellArrays, set_single_element, PHASE_SOLID
from ..element_table import load_element_table
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t0_ratchet"
RINGS = 5
DEFAULT_MOHS = 5
DEFAULT_ENERGY_J = 300
SATURATED_STRAIN = 127  # +i8 max — the Stage 2→Stage 1 ratchet sentinel


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

    # Center cell: pre-saturated compression. Stage 1 of tick 1 consumes it.
    cells.elastic_strain[0] = SATURATED_STRAIN

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
            "91-cell hex disc, Mohs-5 Si solid. Center cell starts at "
            f"elastic_strain={SATURATED_STRAIN} (compression saturation sentinel). "
            "Stage 1 of tick 1 consumes the sentinel: mohs_level 5→6, "
            "RATCHETED flag set, compression work dumped to energy field, "
            "strain reset to 0."
        ),
    )
