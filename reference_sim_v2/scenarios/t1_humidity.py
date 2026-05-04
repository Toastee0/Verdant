"""
Scenario: t1_humidity — humid-air state representation sanity test.

91-cell water disc. Every cell is "humid air": composition is water
(H, 114) + (O, 141), phase fraction is 100% gas, phase_mass[GAS] is
10% of the gas equilibrium center (4.2 of 42 — under-saturated). T is
set to 400 K, well above the H2O boil threshold of 373.15 K, so the
phase-diagram lookup returns GAS for every cell every cycle and no
transitions fire.

Why this scenario exists:
  - Validates the schema can carry humid-gas-of-a-compound state without
    drama — composition + phase_fraction + phase_mass are independent
    enough to represent under-saturated gas of a multi-element compound.
  - Locks in the "stable humid air" baseline that t1_condensation
    perturbs by lowering T below dewpoint.
  - Confirms gen5's cycle pipeline does nothing visible when nothing
    should happen: no flux, no transitions, no T drift, no identity
    flicker. The static-uniform regression invariant.

Expected:
  - Every tick identical to tick 0 (uniform pressure_raw=0 everywhere ⇒
    no mass flux; T uniform and above boil ⇒ no transitions; no gravity
    ⇒ no petal stress integration).
  - mass_per_element_total: H and O totals invariant.
  - identity at every cell: phase=gas, element=O (O wins the majority
    tie-break by fraction 141/255 vs H's 114/255).
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_GAS,
    Q_KG,
)
from ..compounds import set_compound
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc
from ..phase_diagram import load_phase_diagram
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t1_humidity"
RINGS = 5
HUMID_T_K = 400.0                # above 373.15 K boil → phase_diagram = GAS
GAS_MASS_FRAC = 0.10             # 10% of EQ_GAS — under-saturated humid air


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

    h = table["H"]; o = table["O"]
    f_h = 114 / 255.0
    f_o = 141 / 255.0
    cell_size_m = 0.01
    volume = cell_size_m ** 3

    # Per-cell physical mass + cp for humid (gas-phase) water blend.
    # Under gen5 phase_mass↔kg semantics, EQ_GAS_water = density_blend ×
    # volume / Q_KG, and the cell's actual mass at GAS_MASS_FRAC saturation
    # is GAS_MASS_FRAC × density × volume.
    density_g = f_h * h.density_gas_stp + f_o * o.density_gas_stp
    cp_g      = f_h * h.specific_heat_gas + f_o * o.specific_heat_gas
    EQ_GAS_water = density_g * volume / Q_KG
    mass_g_kg = GAS_MASS_FRAC * density_g * volume
    energy_J = mass_g_kg * cp_g * HUMID_T_K
    initial_energy_raw = encode_energy_J_scalar(energy_J)

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_compound(cells, cell_id, compound_id=200, element_table=table)
        cells.phase_fraction[cell_id, PHASE_GAS] = 1.0
        cells.phase_mass[cell_id, PHASE_GAS]     = (
            GAS_MASS_FRAC * float(EQ_GAS_water)
        )
        cells.pressure_raw[cell_id]              = 0
        cells.energy_raw[cell_id]                = initial_energy_raw
        cells.mohs_level[cell_id]                = 0      # no solid component
        cells.flags[cell_id]                     = 0
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
        allowed_elements=("H", "O"),
        phase_diagrams=phase_diagrams,
        description=(
            f"91-cell humid-air disc: water composition (H,114)+(O,141), "
            f"100% gas phase, phase_mass[gas]={GAS_MASS_FRAC*100:g}% of "
            f"equilibrium centre, T={HUMID_T_K:g} K (above 373 K boil → "
            "phase_diagram = GAS). Static-uniform state; no flux, no "
            "transitions, no T drift across ticks. Locks in the humid-"
            "gas baseline that t1_condensation perturbs."
        ),
    )
