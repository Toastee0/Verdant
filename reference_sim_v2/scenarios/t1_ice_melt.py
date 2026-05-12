"""
Scenario: t1_ice_melt — ice cube melting in a warm-water bath.

91-cell water disc with a 7-cell ice core (cell 0 + the six ring-1
neighbours) at −30 °C surrounded by liquid water at +20 °C. Heat
conducts from the warm bath into the ice via the region-kernel energy
flux; the ice cells warm toward 0 °C, cross the melt boundary, and
convert solid → liquid. Volume contracts as ice (917 kg/m³) becomes
water (1000 kg/m³), opening a small vacuum fraction in the melted cells.

Why a 7-cell core (ring-0 + ring-1) rather than a single cell:
  - A single hot/cold spot equilibrates almost instantly via 6 neighbours.
  - A 7-cell core has 12 outer-boundary edges (between ring-1 ice and
    ring-2 water), enough thermal resistance to take many cycles to fully
    melt — useful for the viewer's tick-by-tick playback.
  - Mass per element conserved exactly across the whole disc.

Validates:
  - Conduction (region kernel energy flux) under a large ΔT
  - Per-cell EQ for compound water (ρ_solid=917, ρ_liquid=1000)
  - Volumetric phase_fraction tracking — fully-melted cells should show
    phase_fraction[LIQUID] ≈ 0.917 with vacuum fraction ≈ 0.083
  - Energy-balanced phase transitions in cells that cross 0 °C
  - Cohesion-as-attractor: as ice cells start melting, their newly-
    formed liquid drains toward the ring-2 saturated-liquid cells
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_LIQUID,
    PHASE_SOLID,
)
from ..compounds import compound_cell_energy_J, compound_eq_phase_mass, set_compound
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc, ring_of
from ..phase_diagram import load_phase_diagram
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t1_ice_melt"
RINGS = 5
ICE_T_K   = 243.15    # −30 °C — well below freeze; phase_diagram → SOLID
WATER_T_K = 293.15    # +20 °C — comfortably above freeze; phase_diagram → LIQUID


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

    # 0.1-m cells → 1 liter volume → ~1 kg of water per fully-saturated cell.
    # Matches gen5 kg-native physics (Q_KG = 1 kg/hex_unit). Energy = mass×cp×T
    # in joules directly; e.g. a 1 kg water cell at +20 °C carries 1.23 MJ.
    cell_size_m = 0.1
    world_proxy = type("W", (), {"cell_size_m": cell_size_m})()

    EQ_SOLID_water  = compound_eq_phase_mass(200, PHASE_SOLID,  world_proxy)
    EQ_LIQUID_water = compound_eq_phase_mass(200, PHASE_LIQUID, world_proxy)

    # Ice cells: fully saturated solid water at -30 °C
    ice_energy_raw = encode_energy_J_scalar(
        compound_cell_energy_J(
            phase_mass_solid=EQ_SOLID_water,
            phase_mass_liquid=0.0,
            phase_mass_gas=0.0,
            T_K=ICE_T_K,
            compound_id=200,
        )
    )
    # Water cells: fully saturated liquid water at +20 °C
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
        set_compound(cells, cell_id, compound_id=200, element_table=table)
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

        ring = ring_of(coord)
        if ring <= 1:
            # Centre + ring-1: solid ice core
            cells.phase_fraction[cell_id, PHASE_SOLID] = 1.0
            cells.phase_mass[cell_id, PHASE_SOLID]     = float(EQ_SOLID_water)
            cells.energy_raw[cell_id]                  = ice_energy_raw
            cells.mohs_level[cell_id]                  = 2     # ice mohs
        else:
            # Ring-2..5: liquid water bath
            cells.phase_fraction[cell_id, PHASE_LIQUID] = 1.0
            cells.phase_mass[cell_id, PHASE_LIQUID]     = float(EQ_LIQUID_water)
            cells.energy_raw[cell_id]                   = water_energy_raw
            cells.mohs_level[cell_id]                   = 0

    # dt = 60 s/tick. Real water κ=0.598 W/(m·K) means melting a 1 cm³ ice
    # cube via conduction alone takes ~30 hours of simulated time; at the
    # default dt=1/128 s the viewer would need >10⁶ ticks. Bumping to one
    # minute per tick gives a ~30-tick visible melt. No other physics in
    # this scenario relies on CFL-tight timestepping (no mass flux, no
    # gravity), so longer dt is safe here.
    world = WorldConfig(
        dt=60.0,
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
        allowed_elements=("H", "O"),
        phase_diagrams=phase_diagrams,
        description=(
            f"91-cell water disc with a 7-cell ice core (ring 0+1) at "
            f"T={ICE_T_K - 273.15:+g} °C surrounded by liquid water at "
            f"T={WATER_T_K - 273.15:+g} °C. Heat conducts inward via the "
            "region-kernel energy flux; ice warms, crosses 0 °C, melts; "
            "ice→water volume contraction opens ~8 % vacuum in melted "
            "cells. Validates conduction + energy-balanced transition + "
            "volumetric phase_fraction tracking against a real gradient."
        ),
    )
