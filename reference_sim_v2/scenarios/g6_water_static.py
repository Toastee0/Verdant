"""
Scenario: g6_water_static — Tier 1 (H + O + water) smoke test.

91-cell hex disc, every cell carries water (compound 200 = H, 114, O,
141) at room temperature, 100% liquid phase. Validates:

  - Element table loads H and O alongside Si.
  - Compound expansion works: cells get (H, 114), (O, 141) composition.
  - composition_sum_255 invariant holds with multi-element comp.
  - Mass per element conserved across ticks for both H AND O.
  - Phase diagram lookup for water (loaded under both H and O element
    ids — the dominant element wins identity by saturation, and both
    paths point to H2O.csv so the lookup is consistent regardless of
    tie-break).
  - g5_static-like uniform-equilibrium behaviour: zero deltas every tick.

This is the M6'.0 baseline that lets M6'.1 (t1_ice_melt) build directly
on top: same element table, same phase diagrams, same compound macros,
just different initial energy.
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_LIQUID,
)
from ..compounds import set_compound
from ..grid import build_hex_disc
from ..phase_diagram import load_phase_diagram, load_phase_diagrams_for_table
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "g6_water_static"
RINGS = 5
INITIAL_T_K = 300.0   # room temp; well within liquid range (273–373)


def _build_water_phase_diagrams(table, repo_root: Path) -> dict:
    """Map both H and O element_ids to the H2O phase diagram so identity
    tie-breaks land on the same lookup. Tier 1 stub for compound-aware
    phase resolution; full composition-blended boundaries are M6'.x."""
    h2o = load_phase_diagram(repo_root / "data" / "phase_diagrams" / "H2O.csv")
    diagrams = {}
    h = table["H"]; o = table["O"]
    diagrams[h.element_id] = h2o
    diagrams[o.element_id] = h2o
    # Si keeps its own diagram if present
    si_path = repo_root / "data" / "phase_diagrams" / "Si.csv"
    if si_path.is_file():
        diagrams[table["Si"].element_id] = load_phase_diagram(si_path)
    return diagrams


def build(output_dir: Path | str | None = None, emission_mode: str = "tick") -> Scenario:
    grid = build_hex_disc(RINGS)
    assert grid.cell_count == 91

    repo_root = Path(__file__).resolve().parent.parent.parent
    table = load_element_table(repo_root / "data" / "element_table.tsv")
    phase_diagrams = _build_water_phase_diagrams(table, repo_root)

    cell_size_m = 0.01
    volume = cell_size_m ** 3

    # Per-cell physical mass and c_p for liquid water at composition
    # (H, 114), (O, 141) summed by fraction-weighted contributions:
    h = table["H"]; o = table["O"]
    f_h = 114 / 255.0
    f_o = 141 / 255.0
    density_l = f_h * h.density_liquid + f_o * o.density_liquid     # kg/m³
    cp_l      = f_h * h.specific_heat_liquid + f_o * o.specific_heat_liquid  # J/(kg·K)
    mass = density_l * volume
    energy_J = mass * cp_l * INITIAL_T_K
    # Use Si's energy_scale (1.0) so u16 capacity is consistent. Energy in
    # raw u16 units = energy_J directly. Verify it fits.
    initial_energy_raw = int(round(energy_J))
    assert 0 < initial_energy_raw <= 0xFFFF, (
        f"initial_energy_raw {initial_energy_raw} out of u16 — "
        "M6'.x will need per-element energy_scale calibration"
    )

    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        # Water composition (compound macro 200 expands to H + O)
        set_compound(cells, cell_id, compound_id=200, element_table=table)
        cells.phase_fraction[cell_id, PHASE_LIQUID] = 1.0
        cells.phase_mass[cell_id, PHASE_LIQUID]     = float(EQUILIBRIUM_CENTER[PHASE_LIQUID])
        cells.pressure_raw[cell_id]                 = 0
        cells.energy_raw[cell_id]                   = initial_energy_raw
        cells.mohs_level[cell_id]                   = 0   # liquid — no solid component
        cells.flags[cell_id]                        = 0
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
            f"91-cell water disc at T≈{INITIAL_T_K:g} K (liquid). "
            "Compound macro 200 expands to (H, 114) (O, 141) per cell. "
            "Validates Tier 1 multi-element + compound + water phase "
            "diagram + per-element mass conservation across H AND O."
        ),
    )
