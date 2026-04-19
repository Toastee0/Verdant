"""
Scenario — the object that defines "what is this sim run?"

A Scenario bundles:
- grid shape + size
- initial cell state (composition, phase, energy, pressure, flags)
- world config: dt, G_sim, T_space, solar flux, rate multipliers
- emission granularity
- which element table is authoritative

Scenarios are Python code. A scenario module exports a `build()` function
that returns a Scenario instance with a ready-to-run initial state. See
reference_sim/scenarios/t0_static.py for the simplest example.

This keeps scenario DSL complexity zero — scenarios are just Python. If we
accrete enough of them that a declarative format pays off, we can swap later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .cell import CellArrays
from .element_table import ElementTable
from .grid import HexGrid


@dataclass
class WorldConfig:
    """Global sim parameters."""
    dt: float = 1.0 / 128.0            # sim-seconds per tick (see wiki/dt-and-units.md)
    g_sim: float = 1.0                 # sim-scaled gravitational constant (tune per scenario)
    t_space: float = 2.7               # K, background radiation temperature for RADIATES cells
    solar_flux: float = 0.0            # W/m² incoming; 0 = sunless
    # Rate multipliers scale slow processes so they're observable at sim timescales.
    precipitation_rate_multiplier: float = 1.0
    dissolution_rate_multiplier: float = 1.0
    # Convergence tuning — per-phase sub-iteration caps (see wiki/convergence.md)
    conv_cap_gas: int = 3
    conv_cap_liquid: int = 5
    conv_cap_solid: int = 7
    convergence_threshold: float = 1e-3
    # Magnetism gate: skip Stage 0d entirely when False
    magnetism_enabled: bool = False
    # Grid cell size in metres (for dimensional accuracy of Jacobi constants)
    cell_size_m: float = 0.01          # 1 cm default


@dataclass
class EmissionConfig:
    """When and where JSON dumps happen."""
    mode: str = "tick"                 # "off" | "tick" | "stage" | "cycle" | "violation"
    output_dir: Path | None = None     # where files land; None = dry-run (no writes)
    # What to include in each emission (debug controls that bloat the JSON).
    include_bids: bool = False         # bids_sent / bids_received arrays
    include_gradients: bool = False    # per-cell gradient 6-vector


@dataclass
class Scenario:
    """A full scenario: grid + initial cells + world + emission + metadata."""
    name: str
    grid: HexGrid
    cells: CellArrays
    world: WorldConfig
    emission: EmissionConfig
    element_table: ElementTable
    allowed_elements: tuple[str, ...]   # scenario manifest
    description: str = ""

    @property
    def cell_count(self) -> int:
        return self.grid.cell_count
