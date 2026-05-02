"""
Scenario v2 — gen5 scenario container.

Mirrors the Tier 0 scenario.py shape but extended for gen5:
  - WorldConfig adds gravity_sources (list of point-source descriptors)
    and noise_floor_epsilon (the Tail-at-Scale culling threshold).
  - Drops Tier 0's μ-auction-specific fields (g_sim scalar, rate
    multipliers, conv_cap_*).
  - Convergence budgets per-phase are gen5 universals (3/5/7/3) —
    not scenario-tunable.

Scenarios remain Python: each module exports `build()` returning a
ready-to-run Scenario instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .cell import CellArrays
from .grid import HexGrid


@dataclass
class GravitySource:
    """A point gravity source. Used at scenario setup to seed the gravity
    vector field's border values via Newton's law (gen5 §"Gravity as a
    first-class diffused vector field")."""
    position: tuple[float, float]   # axial-coordinate-space, can be outside the grid
    mass_kg: float                   # source mass


@dataclass
class WorldConfig:
    """Global sim parameters for gen5."""

    dt: float = 1.0 / 128.0          # cycle time (s); see wiki/dt-and-units.md (still applicable)

    # Gravity — gen5 supports multiple point sources diffused into a vector
    # field. Empty list = zero-gravity scenarios (g vector is zero everywhere).
    gravity_sources: tuple[GravitySource, ...] = ()

    # Tail-at-Scale culling threshold. Cells whose six-direction flux
    # contributions are all below ε cull themselves until a neighbor
    # promotes them. Tunable per scenario / hardware target.
    noise_floor_epsilon: float = 1e-4

    # Radiative environment for cells flagged RADIATES.
    t_space: float = 2.7
    solar_flux: float = 0.0

    # Magnetism gate (skip the B-field diffusion entirely when False)
    magnetism_enabled: bool = False

    # Grid cell size in metres (used for face_area in flux computation,
    # gravity Newton-law calculation, and physical-unit translation).
    cell_size_m: float = 0.01

    # Border properties table reference. None means default behavior:
    # all grid edges are NO_FLOW + INSULATED (sealed).
    border_table_path: Path | None = None


@dataclass
class EmissionConfig:
    """When and where JSON dumps happen."""
    mode: str = "tick"                  # "off" | "tick" | "sub_pass" | "violation"
    output_dir: Path | None = None
    include_petals: bool = True         # always include for now; turn off for hot loops
    include_gravity_vec: bool = True
    include_cohesion: bool = False      # transient working state; debug only
    include_flux: bool = False           # debug; large


@dataclass
class Scenario:
    """A full gen5 scenario."""
    name: str
    grid: HexGrid
    cells: CellArrays
    world: WorldConfig
    emission: EmissionConfig
    element_table: object               # ElementTable (loose typing avoids
                                        # an import cycle with element_table.py)
    allowed_elements: tuple[str, ...]
    description: str = ""
    # Phase diagrams keyed by element_id, populated at scenario.build()
    # for elements that need transition logic. Empty dict = no transitions.
    phase_diagrams: dict = field(default_factory=dict)

    @property
    def cell_count(self) -> int:
        return self.grid.cell_count
