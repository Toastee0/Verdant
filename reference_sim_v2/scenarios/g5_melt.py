"""
Scenario: g5_melt — phase transition validation.

91-cell hex disc, all Si solid initially, but heated to T well above
Si's melt point (1687 K). Phase resolve at the start of the first cycle
should detect (T > melt) and transition every cell solid → liquid.

Energy chosen so that BOTH solid and liquid temperatures land above the
melt threshold:
  - Solid c_p × ρ × V × T = 1.654 × T
  - Liquid c_p × ρ × V × T = 2.488 × T (so liquid T = solid T × 0.665)
  - To stay above melt 1687 in BOTH phases: solid T > 1687 / 0.665 = 2536 K

Use solid T_initial = 3000 K → energy_raw ≈ 4962 (raw u16). After phase
flip, cell is in liquid at T ≈ 3000 × 0.665 = 1994 K, still above melt.
No latent-heat oscillation in M5'.5 stub (latent heat deferred to M5'.5b
because the c_p discontinuity at small cell sizes makes naive instant
latent-heat absorption immediately re-freeze the cell — see commit msg).

Validation:
  - tick 0: every cell phase=solid (raw initial, derived recomputed)
  - tick 1+: every cell phase=liquid (transition fired, mass shifted to
    liquid channel, mohs_level=0)
  - Mass per element conserved (solid_mass moved to liquid_mass within
    each cell; total per-element invariant)
  - All standard invariants pass
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
from ..phase_diagram import load_phase_diagrams_for_table
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "g5_melt"
RINGS = 5
INITIAL_T_K = 3000.0  # well above Si melt 1687, below boil 3538 (in liquid post-flip)


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    repo_root = Path(__file__).resolve().parent.parent.parent
    table = load_element_table(repo_root / "data" / "element_table.tsv")
    si = table["Si"]
    phase_diagrams = load_phase_diagrams_for_table(table, repo_root / "data" / "phase_diagrams")

    cell_size_m = 0.01
    volume = cell_size_m ** 3
    # Compute energy for T=3000 K starting in SOLID phase
    mass_solid = si.density_solid * volume
    cp_solid = si.specific_heat_solid
    initial_energy_J = mass_solid * cp_solid * INITIAL_T_K
    initial_energy_raw = encode_energy_J_scalar(initial_energy_J)

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase_fraction[cell_id, PHASE_SOLID] = 1.0
        cells.phase_mass[cell_id, PHASE_SOLID]     = float(EQUILIBRIUM_CENTER[PHASE_SOLID])
        cells.energy_raw[cell_id]                  = initial_energy_raw
        cells.mohs_level[cell_id]                  = 6
        cells.flags[cell_id]                       = 0
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

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
        element_table=table,
        allowed_elements=("Si",),
        phase_diagrams=phase_diagrams,
        description=(
            f"91-cell Si disc starting in solid phase at T≈{INITIAL_T_K:g} K. "
            "Phase resolve transitions every cell to liquid at tick 1. "
            "Validates phase_diagram lookup + apply_phase_transitions."
        ),
    )
