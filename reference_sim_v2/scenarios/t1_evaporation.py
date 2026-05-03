"""
Scenario: t1_evaporation — gen5 sorting-ruleset extension demonstration.

91-cell water disc. Center cell (cell 0) holds liquid water at T≈320 K
with elevated pressure_raw. The 90 surrounding cells hold hot dry gas
water at T≈400 K (above the H2O boil threshold of 373.15 K) with
phase_mass[GAS] sitting well below the gas equilibrium centre — hot,
under-saturated.

Phase-diagram lookup for water (H2O.csv):
    T  ∈ (273.15, 373.15] K → liquid
    T  >  373.15 K          → gas

So at flux compute time:
    For each (cell0, direction d, slot s) where slot s holds H or O:
        neighbour B's T ≈ 400 K  →  phase_diagram[H₂O].lookup(400) = GAS
        flux.dst_phase_per_slot[0, d, s] = PHASE_GAS

The region kernel sees src_phase = LIQUID (cell 0 is 100% liquid) and
dst_phase = GAS (per neighbour-side lookup). A cross-phase event fires
on every outbound edge: liquid mass leaves cell 0, scatter-credits to
neighbour's GAS channel (not LIQUID), and the source cell pays latent
heat L_v × Δm_kg debited via flux.energy_self.

Validates:
  - flux.dst_phase_per_slot is populated correctly by the sorting-ruleset
    extension (PHASE_GAS at all six outbound edges of cell 0).
  - Mass arrives in the destination cell's GAS phase channel directly
    (no transit through the LIQUID channel).
  - Per-element conservation: total H AND total O mass invariant across
    all phases.
  - Source cell loses energy by the latent-heat amount (negative
    flux.energy_self[0] each sub-pass).
  - Identity at cell 0 stays "liquid water"; identity at ring-1
    neighbours stays "gas water" (their gas mass grows; their gas phase
    fraction was already 1.0).

Note on calibration: our (H, 114) + (O, 141) compound blend gives
L_v_blend ≈ 316,800 J/kg vs real water 2,257,000 J/kg. The qualitative
behaviour (liquid → gas via sorting ruleset, source pays) is correct;
quantitative L_v calibration is M6'.x compound-aware physics work.
"""

from __future__ import annotations

from pathlib import Path

from reference_sim.element_table import load_element_table

from ..cell import (
    CellArrays,
    EQUILIBRIUM_CENTER,
    PETAL_TOPO_IS_GRID_EDGE,
    PHASE_GAS,
    PHASE_LIQUID,
)
from ..compounds import set_compound
from ..encoding import encode_energy_J_scalar
from ..grid import build_hex_disc
from ..phase_diagram import load_phase_diagram
from ..scenario import EmissionConfig, Scenario, WorldConfig


SCENARIO_NAME = "t1_evaporation"
RINGS = 5

CENTER_T_K        = 320.0     # liquid range, well below boil
NEIGHBOR_T_K      = 400.0     # gas range, above 373.15 K boil
ELEVATED_PRESSURE = 5000      # source push toward neighbours
NEIGHBOR_GAS_MASS_FRAC = 0.10  # gas cells start at 10% of EQ_GAS — under-saturated


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

    # ---- Energy levels per cell type --------------------------------------
    # Center: 100% liquid, T = CENTER_T_K
    density_l = f_h * h.density_liquid + f_o * o.density_liquid
    cp_l      = f_h * h.specific_heat_liquid + f_o * o.specific_heat_liquid
    mass_l_kg = density_l * volume
    energy_l_J = mass_l_kg * cp_l * CENTER_T_K
    center_energy_raw = encode_energy_J_scalar(energy_l_J)

    # Neighbours: 100% gas (phase_fraction-wise) at low gas mass.
    # Under log-encoded energy_raw the per-cell resolution is sub-K even
    # for these tiny gas-cell masses, so we can target NEIGHBOR_T_K
    # directly without the hand-tuned-quantum workaround the linear
    # encoding required.
    density_g = f_h * h.density_gas_stp + f_o * o.density_gas_stp
    cp_g      = f_h * h.specific_heat_gas + f_o * o.specific_heat_gas
    mass_g_kg = density_g * volume
    energy_g_J = mass_g_kg * cp_g * NEIGHBOR_T_K
    neighbor_energy_raw = encode_energy_J_scalar(energy_g_J)

    # ---- Build the cells -------------------------------------------------
    cells = CellArrays.empty(grid)
    for cell_id in range(grid.cell_count):
        set_compound(cells, cell_id, compound_id=200, element_table=table)
        for d in range(6):
            if grid.neighbors[cell_id][d] == -1:
                cells.petal_topology[cell_id, d] |= PETAL_TOPO_IS_GRID_EDGE

    # Center cell — liquid water, elevated pressure
    cells.phase_fraction[0, PHASE_LIQUID] = 1.0
    cells.phase_mass[0, PHASE_LIQUID]     = float(EQUILIBRIUM_CENTER[PHASE_LIQUID])
    cells.pressure_raw[0]                 = ELEVATED_PRESSURE
    cells.energy_raw[0]                   = center_energy_raw

    # Neighbour cells — hot under-saturated gas water
    for cell_id in range(1, grid.cell_count):
        cells.phase_fraction[cell_id, PHASE_GAS] = 1.0
        cells.phase_mass[cell_id, PHASE_GAS]     = (
            NEIGHBOR_GAS_MASS_FRAC * float(EQUILIBRIUM_CENTER[PHASE_GAS])
        )
        cells.pressure_raw[cell_id]              = 0
        cells.energy_raw[cell_id]                = neighbor_energy_raw

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
            f"91-cell water disc. Center cell: liquid water at "
            f"T={CENTER_T_K:g} K, pressure_raw={ELEVATED_PRESSURE}. "
            f"Surrounding 90 cells: hot under-saturated gas water at "
            f"T={NEIGHBOR_T_K:g} K (above 373.15 K boil), "
            f"phase_mass[gas] = {NEIGHBOR_GAS_MASS_FRAC*100:g}% of "
            "equilibrium centre. Validates the gen5 sorting-ruleset "
            "extension (cross-phase mass transmutation): liquid water "
            "leaving the centre arrives at neighbours directly in their "
            "GAS channel (not LIQUID), and the source pays latent heat "
            "L_v×Δm_kg via flux.energy_self."
        ),
    )
