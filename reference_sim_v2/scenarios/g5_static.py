"""
Scenario: g5_static — gen5 hello world.

91-cell hex disc, every cell uniform Si solid at the equilibrium center
(74088 mass units), zero pressure deviation, low energy, zero motion.
No gravity, no walls, no transitions.

Expected behavior: zero deltas every tick. Mass per (element, phase)
conserved exactly. All invariants pass at every emission.

This is the gen5 analog of t0_static — the regression baseline that
every later milestone must keep green.
"""

from __future__ import annotations

from pathlib import Path

# Reuse the Tier 0 element_table loader (framework-agnostic per the
# reusability audit). M5'.5 will adapt the column set; for M5'.0 we
# read the existing TSV as-is and reference Si's properties.
from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PHASE_SOLID,
    PETAL_TOPO_IS_GRID_EDGE,
    set_single_element,
)
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "g5_static"
RINGS = 5
DEFAULT_MOHS = 6                          # picked from a Si phase diagram lookup; M5'.5 will derive this
DEFAULT_ENERGY_J = 300.0                  # joules; produces a low T well below Si melt


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91, f"expected 91-cell disc, got {grid.cell_count}"

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        # Composition: 100% Si in slot 0
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)

        # Phase distribution: pure solid, no liquid/gas/plasma, no vacuum
        cells.phase_fraction[cell_id, PHASE_SOLID]  = 1.0
        # Phase mass at the gen5 hex-arithmetic equilibrium center for solid
        cells.phase_mass[cell_id, PHASE_SOLID]      = float(EQUILIBRIUM_CENTER[PHASE_SOLID])

        cells.pressure_raw[cell_id]            = 0       # zero deviation from center
        cells.energy_raw[cell_id]              = encode_energy_J_scalar(DEFAULT_ENERGY_J)
        cells.mohs_level[cell_id]              = DEFAULT_MOHS
        cells.sustained_overpressure[cell_id]  = 0.0
        cells.flags[cell_id]                   = 0

        # Mark grid-edge directions in petal topology (cached on first contact;
        # gen5 §"Topology caching in petal metadata"). For M5'.0 we precompute
        # this at scenario init since region kernels aren't running yet.
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
        include_gravity_vec=False,        # no gravity sources, vec is zero
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
            "91-cell hex disc, uniform Mohs-6 Si solid at phase-density "
            f"equilibrium center ({EQUILIBRIUM_CENTER[PHASE_SOLID]:.0f} mass units), "
            "zero pressure deviation, energy_raw=300, no gravity, no walls. "
            "Expected: zero deltas every tick; mass per (element, phase) conserved exactly."
        ),
    )
