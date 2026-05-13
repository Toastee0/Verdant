"""
Scenario: t1_buoyancy — Phase 2 hydrostatic pressure SCAFFOLD.

Liquid water in the upper half of the disc (r ≤ 0), empty cells in the
lower half (r > 0). Gravity points "down" (+y in our hex Cartesian
convention) so water should fall into the empty cells via hydrostatic
pressure gradient.

⚠️ M7'.2 STATUS: HYDROSTATIC CORRECTION IS WIRED BUT FLOW IS TOO SLOW
TO BE VISIBLY OBSERVABLE IN 30 TICKS at this scenario's parameters.

The region kernel's per-face dP gets a `ρ̄ × cell_size × (g · d̂_d)`
term (see region.py), so water cells correctly see a positive pressure
differential toward the empty cells below. Flow fires — but slowly.
The legacy `PHASE_CONDUCTANCE × FLOW_SCALE_KG ≈ 3 × 10⁻¹¹` mobility,
applied to a 350-Pa hydrostatic gradient, gives a per-cycle drainage
of ~10⁻⁷ kg of a 1 kg cell — i.e. millions of ticks to see the column
collapse.

This is the calibration M7'.4 fixes: replace `PHASE_CONDUCTANCE` with
physically-grounded Darcy permeability (m²/(Pa·s)) per phase, with
viscosity-based mobility. At that point the same scenario will show
the water column falling at sub-second physical timescales.

For now t1_buoyancy serves as:
  1. A regression test that hydrostatic correction doesn't break
     mass conservation (16/16 regression PASS).
  2. A scaffold scenario the viewer can load to confirm the pressure
     field is computed (inspect derived.pressure → should be roughly
     constant in the water column, ≈ 0 in the void).
  3. A target for M7'.4 — when the K constants get rebalanced, this
     scenario will start showing visible drainage.
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_LIQUID,
)
from ..compounds import compound_cell_energy_J, compound_eq_phase_mass, set_compound
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc, ring_of
from ..phase_diagram import load_phase_diagram
from ..scenario import EmissionConfig, GravitySource, Scenario, WorldConfig


SCENARIO_NAME = "t1_buoyancy"
RINGS = 5
WATER_T_K = 298.15      # 25 °C — well within liquid water range

# Gravity source: place mass FAR BELOW the disc (positive y in our hex
# Cartesian convention = "south") so the gravity vector at disc cells
# points downward (toward +y).
SOURCE_DISTANCE_M = 10.0
TARGET_G = 9.8


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    repo_root = Path(__file__).resolve().parent.parent.parent
    table = load_element_table(repo_root / "data" / "element_table.tsv")
    h2o = load_phase_diagram(repo_root / "data" / "phase_diagrams" / "H2O.csv")
    si  = load_phase_diagram(repo_root / "data" / "phase_diagrams" / "Si.csv")
    phase_diagrams = {
        table["H"].element_id:  h2o,
        table["O"].element_id:  h2o,
        table["Si"].element_id: si,
    }

    cell_size_m = 0.1   # 1 liter cells → 1 kg per fully-saturated water cell
    world_proxy = type("W", (), {"cell_size_m": cell_size_m})()

    EQ_LIQUID_water = compound_eq_phase_mass(200, PHASE_LIQUID, world_proxy)
    water_energy_raw = encode_energy_J_scalar(
        compound_cell_energy_J(
            phase_mass_solid=0.0,
            phase_mass_liquid=EQ_LIQUID_water,
            phase_mass_gas=0.0,
            T_K=WATER_T_K,
            compound_id=200,
        )
    )

    cells = CellArrays.empty(grid)
    for cell_id, coord in enumerate(grid.coords):
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

        q, r = coord
        if r <= 0:
            # Top half + centre row: full liquid water column
            set_compound(cells, cell_id, compound_id=200, element_table=table)
            cells.phase_fraction[cell_id, PHASE_LIQUID] = 1.0
            cells.phase_mass[cell_id, PHASE_LIQUID]     = float(EQ_LIQUID_water)
            cells.energy_raw[cell_id]                   = water_energy_raw
            cells.mohs_level[cell_id]                   = 0
        else:
            # Bottom half: empty cells (no composition, no mass, no energy)
            # Water above should fall into these under gravity.
            cells.phase_fraction[cell_id, :] = 0.0
            cells.phase_mass[cell_id, :]     = 0.0
            cells.energy_raw[cell_id]        = 0
            cells.mohs_level[cell_id]        = 0
            # composition stays all-zero (void)

    # Gravity source at (0, +SOURCE_DISTANCE_M) — south of the disc in
    # the hex Cartesian layout. Gravity at disc cells points toward +y.
    G_CONST = 6.674e-11
    source_mass = TARGET_G * (SOURCE_DISTANCE_M ** 2) / G_CONST
    source = GravitySource(position=(0.0, SOURCE_DISTANCE_M), mass_kg=source_mass)

    world = WorldConfig(
        dt=0.5,
        gravity_sources=(source,),
        noise_floor_epsilon=1e-4,
        cell_size_m=cell_size_m,
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
        element_table=table,
        allowed_elements=("H", "O"),
        phase_diagrams=phase_diagrams,
        description=(
            f"91-cell water column at T={WATER_T_K - 273.15:g} °C with "
            "gravity pointing downward (+y in hex Cartesian). Top half + "
            "centre row = full liquid water. Bottom half = empty cells "
            "(void). Hydrostatic pressure should drive water DOWN into "
            "the void; over many ticks the column should redistribute "
            "with mass-bearing cells at the bottom. Validates the "
            "M7'.2 hydrostatic correction in the region kernel: the "
            "per-face ΔP gets a `ρ̄ × cell_size × (g · d̂_d)` term that "
            "favours downward flow under gravity."
        ),
    )
