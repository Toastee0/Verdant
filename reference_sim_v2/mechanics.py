"""
Mechanics — gen5 §"Petal data" persistent directional stress.

For now, two responsibilities:
  - update_petal_stress: integrate gravity-driven directional stress onto
    per-cell petals. Each cycle, the cell's weight (ρ × V × g_vec)
    projected onto each direction's unit vector × the bond's cohesion
    × dt accumulates into petal_stress.
  - decay_petal_stress: exponential springback toward zero each cycle so
    stress doesn't grow unbounded.

Sign convention: positive petal_stress = compression (cell pushed
toward neighbour); negative = tension. Across a bond between two cells
in a uniform gravity field with uniform cohesion, A's petal_stress[d]
and B's petal_stress[OPP[d]] are equal-and-opposite — net stress = 0.
Non-uniform density / cohesion / gravity produces non-zero net stress
representing real phenomena (heavy block compressing light support, etc).

Per-direction unit vectors come from axial_to_cartesian on each
NEIGHBOR_DELTA.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .cell import CellArrays, N_PETAL_DIRS
from .grid import NEIGHBOR_DELTAS

if TYPE_CHECKING:
    from .derive import DerivedFields
    from .scenario import WorldConfig


# Per-direction unit vectors in cartesian (matches gravity.axial_to_cartesian
# convention: x = (q + r/2) × cs, y = r × √3/2 × cs).
_SQRT3_OVER_2 = float(np.sqrt(3.0) / 2.0)
DIRECTION_UNIT_VECTORS = np.array([
    # axial (dq, dr) → cartesian unit-vec
    [+1.0,             0.0],          # 0: E   ( +1,  0)
    [+0.5, -_SQRT3_OVER_2],            # 1: SE  ( +1, -1)
    [-0.5, -_SQRT3_OVER_2],            # 2: SW  (  0, -1)
    [-1.0,             0.0],           # 3: W   ( -1,  0)
    [-0.5, +_SQRT3_OVER_2],            # 4: NW  ( -1, +1)
    [+0.5, +_SQRT3_OVER_2],            # 5: NE  (  0, +1)
], dtype=np.float32)


# Springback decay: stress decays toward 0 over time when no force sustains it.
PETAL_STRESS_DECAY_PER_SEC = 0.5
# Cap on absolute petal_stress magnitude (Pa-equivalent in arbitrary units;
# prevents f32 overflow under pathological scenarios).
PETAL_STRESS_CAP = 1e9


FLAG_FIXED_STATE = 1 << 3


def update_petal_stress(
    cells: CellArrays,
    derived: "DerivedFields",
    world: "WorldConfig",
) -> None:
    """Accumulate gravity-on-mass projected stress onto each cell's six
    petals. Then apply exponential springback decay.

    For zero-gravity scenarios (no gravity sources), gravity_vec is zero
    everywhere; the projection step contributes nothing and only decay
    runs. Behaviour is identical to "no petal stress" for those scenarios.
    """
    n = cells.n
    if n == 0:
        return

    fixed = (cells.flags & FLAG_FIXED_STATE) != 0

    # Force per cell in Newtons: F = ρ × V × g_vec (per-component)
    volume = float(world.cell_size_m) ** 3
    force = derived.density[:, None] * volume * derived.gravity_vec   # (N, 2)

    # Project onto each direction's unit vector → per-cell-per-direction scalar
    proj = force @ DIRECTION_UNIT_VECTORS.T                           # (N, 6)

    # Cohesion damping (blind, per-cell-per-direction)
    delta = proj * derived.cohesion * float(world.dt)                 # (N, 6)

    # Don't accumulate onto FIXED_STATE cells (held state)
    if fixed.any():
        delta[fixed, :] = 0.0

    new_stress = cells.petal_stress.astype(np.float32) + delta

    # Springback decay (exponential toward 0)
    decay = max(0.0, 1.0 - PETAL_STRESS_DECAY_PER_SEC * float(world.dt))
    new_stress *= decay

    # Cap to prevent f32 overflow
    new_stress = np.clip(new_stress, -PETAL_STRESS_CAP, PETAL_STRESS_CAP)

    cells.petal_stress[:] = new_stress.astype(np.float32)
