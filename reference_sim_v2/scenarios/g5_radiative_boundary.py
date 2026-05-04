"""
Scenario: g5_radiative_boundary — Stefan-Boltzmann boundary cooling.

91-cell Si liquid disc at T≈2500 K. Outer-ring (ring 5) cells are flagged
RADIATES; they emit blackbody radiation to T_space=2.7 K each cycle. Per
gen5 §"Radiation" this is a once-per-cycle slow boundary loss; cycle by
cycle the boundary cells lose energy_raw, which (eventually) draws heat
from the interior via energy flux (M5'.6 stub: energy flux from
conduction is not yet wired — that's the energy-channel of the region
kernel which is M5'.6b/M6'+ work). For M5'.6 we observe boundary cells
cooling directly; the interior conducts via convection-with-mass once
the energy channel is populated.

For Si liquid at T=2500 K with ε_liquid=0.3:
  P_face_per_tick = 0.3 × σ × T⁴ × area × dt
                  = 0.3 × 5.67e-8 × 3.91e13 × 1e-4 × 1/128
                  ≈ 0.83 J / face / tick
At energy_scale=1.0 J/raw, this rounds to 1 raw unit per RADIATES cell
per tick. 30 boundary cells × 1 raw = 30 raw/tick total energy loss.

Validation:
  - tick 0: total_energy = 91 × 6220 = ~565 K raw (varies by exact rounding)
  - tick N: total_energy decreases monotonically by ~30 raw / tick
  - Mass per element conserved exactly (no mass flow on uniform liquid)
  - Boundary cells (ring 5) lose more energy than interior cells
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_LIQUID,
    Q_KG,
    set_single_element,
)
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc, ring_of
from ..phase_diagram import load_phase_diagrams_for_table
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "g5_radiative_boundary"
RINGS = 5
INITIAL_T_K = 2500.0
FLAG_RADIATES = 1 << 1


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    repo_root = Path(__file__).resolve().parent.parent.parent
    table = load_element_table(repo_root / "data" / "element_table.tsv")
    si = table["Si"]
    phase_diagrams = load_phase_diagrams_for_table(table, repo_root / "data" / "phase_diagrams")

    cell_size_m = 0.01
    volume = cell_size_m ** 3
    mass_l = si.density_liquid * volume
    cp_l = si.specific_heat_liquid
    initial_energy_raw = encode_energy_J_scalar(mass_l * cp_l * INITIAL_T_K)
    EQ_LIQUID_Si = si.density_liquid * volume / Q_KG

    cells = CellArrays.empty(grid)
    for cell_id, coord in enumerate(grid.coords):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase_fraction[cell_id, PHASE_LIQUID] = 1.0
        cells.phase_mass[cell_id, PHASE_LIQUID]     = float(EQ_LIQUID_Si)
        cells.energy_raw[cell_id]                   = initial_energy_raw
        cells.mohs_level[cell_id]                   = 0
        # Outer ring → RADIATES
        if ring_of(coord) == RINGS:
            cells.flags[cell_id] = FLAG_RADIATES
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    world = WorldConfig(
        dt=1.0 / 128.0,
        gravity_sources=(),
        noise_floor_epsilon=1e-4,
        t_space=2.7,
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
            f"91-cell Si liquid disc at T≈{INITIAL_T_K:g} K. Outer ring "
            "flagged RADIATES; emits Stefan-Boltzmann to T_space. Total "
            "energy decreases ~30 raw/tick. Validates apply_radiation +"
            "per-channel border behavior."
        ),
    )
