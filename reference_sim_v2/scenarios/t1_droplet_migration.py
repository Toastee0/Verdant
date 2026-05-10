"""
Scenario: t1_droplet_migration — cohesion-as-attractor demonstration.

The matching bookend to t1_condensation: validates that, after gas → liquid
condensation produces dispersed liquid droplets in humid air, those droplets
migrate via cohesion attraction toward existing liquid-water cells.

Per gen5 §"Cross-phase dynamics → Condensation":
    "Liquid cohesion pulls the dispersed droplets toward cells with existing
     liquid water (high cohesion attractor), leaving the original cell with
     humid gas and the receiving cell with growing liquid mass."

Setup (91-cell water disc):
  - Center cell (id=0): humid gas with a small newly-condensed liquid
    fraction. phase_fraction = (gas=0.95, liquid=0.05). phase_mass[GAS] at
    equilibrium-fill (full gas saturation), phase_mass[LIQUID] at 5 % of
    EQ_LIQUID. T = 350 K (below boil) — the cell mimics the post-condensation
    state from t1_condensation but is held there for a clean migration demo.
  - Ring-1 neighbours (cells 1..6): fully saturated liquid water at the same
    T. phase_fraction[LIQUID] = 1.0, phase_mass[LIQUID] = EQ_LIQUID_water.
    These are the "high cohesion attractors."
  - Ring-2+ cells: more humid gas (same as the centre, no liquid). Provides
    a uniform cohesion-1.0 environment so the only saturation gradient is
    centre↔ring-1.

Expected:
  - Each cycle, cohesion attractor pulls liquid mass from cell 0 toward
    cells 1..6 along all six in-grid edges. flux.mass[0, d, slot, LIQUID]
    is positive on every direction d that points to a high-saturation
    liquid neighbour.
  - cell 0 phase_mass[LIQUID] decreases monotonically.
  - Sum of cells 1..6 phase_mass[LIQUID] increases by an equal amount
    (per-element conservation across the migration).
  - Gas phase stays put at cell 0 (gas attractor is weak; gas isn't the
    cohesion target).
  - Identity does not flip: cell 0 stays gas-dominant by saturation.

Calibration caveat: the humid-air ring-2 cells also have a small
saturation gradient toward the ring-1 liquid cells, so liquid mass also
migrates ring-2 → ring-1 (a secondary effect). Watch the totals for the
overall direction of mass flow rather than per-cell drama.
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_GAS,
    PHASE_LIQUID,
    Q_KG,
)
from ..compounds import compound_cell_energy_J, compound_eq_phase_mass, set_compound
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc, ring_of
from ..phase_diagram import load_phase_diagram
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t1_droplet_migration"
RINGS = 5
T_K = 350.0                   # below boil → phase_diagram = LIQUID
HUMID_GAS_FRAC = 0.95         # 95 % volumetric gas
HUMID_LIQUID_FRAC = 0.05      # 5 % volumetric liquid (newly condensed)
# Sums to 1.0 (vacuum complement = 0); both phases sit at their own equilibrium
# density within their volumetric share, so phase_mass[p] = HUMID_x_FRAC × EQ_p.


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

    cell_size_m = 0.01
    world_proxy = type("W", (), {"cell_size_m": cell_size_m})()

    EQ_GAS_water    = compound_eq_phase_mass(200, PHASE_GAS,    world_proxy)
    EQ_LIQUID_water = compound_eq_phase_mass(200, PHASE_LIQUID, world_proxy)

    # Humid cells: HUMID_GAS_FRAC of EQ_GAS + HUMID_LIQUID_FRAC of EQ_LIQUID
    pm_humid_gas    = HUMID_GAS_FRAC    * EQ_GAS_water
    pm_humid_liquid = HUMID_LIQUID_FRAC * EQ_LIQUID_water
    humid_energy_raw = encode_energy_J_scalar(
        compound_cell_energy_J(
            phase_mass_solid=0.0,
            phase_mass_liquid=pm_humid_liquid,
            phase_mass_gas=pm_humid_gas,
            T_K=T_K,
            compound_id=200,
        )
    )

    # Liquid attractor cells: full liquid mass
    liquid_energy_raw = encode_energy_J_scalar(
        compound_cell_energy_J(
            phase_mass_solid=0.0,
            phase_mass_liquid=EQ_LIQUID_water,
            phase_mass_gas=0.0,
            T_K=T_K,
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
        if ring == 1:
            # Attractor cells: full liquid water
            cells.phase_fraction[cell_id, PHASE_LIQUID] = 1.0
            cells.phase_mass[cell_id, PHASE_LIQUID]     = EQ_LIQUID_water
            cells.energy_raw[cell_id]                   = liquid_energy_raw
        else:
            # Centre + ring-2+: humid gas with small newly-condensed liquid
            cells.phase_fraction[cell_id, PHASE_GAS]    = HUMID_GAS_FRAC
            cells.phase_fraction[cell_id, PHASE_LIQUID] = HUMID_LIQUID_FRAC
            cells.phase_mass[cell_id, PHASE_GAS]        = pm_humid_gas
            cells.phase_mass[cell_id, PHASE_LIQUID]     = pm_humid_liquid
            cells.energy_raw[cell_id]                   = humid_energy_raw
        cells.pressure_raw[cell_id] = 0
        cells.flags[cell_id]        = 0

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
        allowed_elements=("H", "O"),
        phase_diagrams=phase_diagrams,
        description=(
            f"91-cell water disc at T={T_K:g} K. Centre + ring-2+ cells: "
            f"humid air with {HUMID_LIQUID_FRAC*100:g}% newly-condensed "
            "liquid (low liquid saturation). Ring-1 neighbours: fully "
            "saturated liquid water. Validates the cohesion-as-attractor "
            "flux term: liquid mass migrates from low-saturation humid "
            "cells toward the high-saturation liquid ring along the "
            "saturation gradient × cohesion × dt × phase_mass attractor."
        ),
    )
