"""
Scenario: t0_radiate

Uniformly hot Si-liquid disc with a radiative outer boundary. Tests
Stage 4 radiation (Stefan-Boltzmann to T_space) and conduction (interior →
boundary) on the energy field.

Why liquid Si: Tier 0 sticks to one element; for radiation deltas to
register at u16 + Si energy_scale=1.0 J/unit, T must be high enough that
ε σ T⁴ × A × dt ≳ 1 J per face per tick. Si liquid's range (1687–3538 K)
gives that headroom — at T=2500 K and ε=0.3 the per-face per-tick loss is
~0.83 J ⇒ floors to 1 raw unit. Si solid at lower T drops below the
floor and the radiation effect goes invisible.

Boundary cells: every ring-5 cell (outer ring of the disc) flagged
RADIATES. They are NOT FIXED_STATE — they really do cool down each tick.
Interior cells conduct toward the cooling boundary.

Expected over 5+ ticks:
- Mass conserved exactly (no Stage 3 flow on uniform liquid Si).
- Energy total decreases monotonically (radiative loss).
- Boundary cells lose more energy than interior cells (radiation is local
  to RADIATES; interior loses only via conduction toward boundary).
- No phase transitions if T stays above melt and below boil — the choice
  of T=2500 K with cell mass × c_p sized so energy_raw ≈ 6220 keeps cells
  in liquid phase across many ticks of cooling.
"""

from __future__ import annotations

from pathlib import Path

from ..cell import CellArrays, set_single_element, PHASE_LIQUID
from ..element_table import load_element_table
from ..flags import RADIATES
from ..grid import build_hex_disc, ring_of
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t0_radiate"
RINGS = 5
INITIAL_T_K = 2500.0   # Si liquid; well within (melt=1687, boil=3538)
DEFAULT_MOHS = 0       # liquid: mohs unused (verify.py allows 0 or 1 for non-solids)


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    table_path = Path(__file__).resolve().parent.parent.parent / "data" / "element_table.tsv"
    element_table = load_element_table(table_path)
    si = element_table["Si"]

    cell_size_m = 0.01
    volume = cell_size_m ** 3
    mass = si.density_liquid * volume                 # kg
    cp_mass = mass * si.specific_heat_liquid          # J/K
    initial_energy_j = cp_mass * INITIAL_T_K
    initial_energy_raw = int(round(initial_energy_j / si.energy_scale))
    assert 0 < initial_energy_raw <= 0xFFFF, f"initial_energy_raw {initial_energy_raw} out of u16"

    cells = CellArrays.empty(grid)
    for cell_id, coord in enumerate(grid.coords):
        set_single_element(cells, cell_id, element_id=si.element_id, fraction=255)
        cells.phase[cell_id] = PHASE_LIQUID
        cells.mohs_level[cell_id] = DEFAULT_MOHS
        cells.pressure_raw[cell_id] = 0
        cells.energy[cell_id] = initial_energy_raw
        cells.elastic_strain[cell_id] = 0
        cells.magnetization[cell_id] = 0
        # Boundary ring (ring index = RINGS): RADIATES
        if ring_of(coord) == RINGS:
            cells.flags[cell_id] = RADIATES
        else:
            cells.flags[cell_id] = 0

    world = WorldConfig(
        dt=1.0 / 128.0,
        g_sim=0.0,
        t_space=2.7,
        solar_flux=0.0,
        magnetism_enabled=False,
        cell_size_m=cell_size_m,
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
            f"91-cell hex disc, all Si liquid at T≈{INITIAL_T_K:g} K "
            f"(energy={initial_energy_raw} raw u16). Ring-{RINGS} outer cells "
            "flagged RADIATES; they emit Stefan-Boltzmann to T_space=2.7 K "
            "each tick. Interior conducts toward the cooling boundary. "
            "Mass conserved; total energy decreases monotonically."
        ),
    )
