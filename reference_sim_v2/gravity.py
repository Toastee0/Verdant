"""
Gravity vector field — gen5 §"Gravity as a first-class diffused vector field".

Per gen5 the field is a per-cell f32 (gx, gy) vector, populated by:

  1. Setup phase (once per scenario, or refreshed when sources move):
     - Scenarios specify point sources (position, mass).
     - For each border cell, vector contribution from each source is
       computed by Newton's law (g = GM/d² × direction_to_source).
       Contributions sum.
     - Border values then "seed" a Jacobi diffusion that fills the
       interior cells over a few iterations. Border values stay frozen
       throughout runtime.

  2. Runtime phase (each cycle, optionally less often):
     - Active cells can contribute their own mass to perturb the field
       locally — a dense local body tilts neighbouring vectors slightly.
     - Border vectors stay frozen (external context is invariant to the
       sim's internal slice).

Convex region requirement: the simulation region must be convex for the
Jacobi diffusion to behave correctly. Concave regions produce gradient
pathologies. M5'.2 enforces this at scenario setup; for now the only
supported topology is the hex_disc, which is convex by construction.

Application to motion is M5'.3+ (region kernel applies acceleration only
to cells whose motion exceeds noise floor ε).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .grid import HexGrid
    from .scenario import GravitySource, WorldConfig


# Gravitational constant in SI: m³ / (kg · s²)
G_CONST = 6.674e-11


def is_border_cell(grid: "HexGrid", cell_id: int) -> bool:
    """A cell is a border cell iff any of its six neighbour slots is -1
    (off-grid). Used to identify the cells that get Newton-seeded values."""
    return any(nid == -1 for nid in grid.neighbors[cell_id])


def axial_to_cartesian(q: int, r: int, cell_size_m: float) -> tuple[float, float]:
    """Convert axial-coord (q, r) to cartesian (x, y) for distance arithmetic.

    Pointy-top hex convention. cell_size_m is the spacing between adjacent
    cell centres (edge-to-edge through a face).
    """
    x = (q + r * 0.5) * cell_size_m
    y = r * (np.sqrt(3.0) / 2.0) * cell_size_m
    return x, y


def assert_convex(grid: "HexGrid") -> None:
    """Reject non-convex grids per gen5 §"Setup phase". M5'.2 hard-codes
    hex_disc as the only accepted shape; future arbitrary topologies will
    need a real convexity check (boundary walk + cross-product sign test)."""
    if grid.shape != "hex_disc":
        raise ValueError(
            f"Gravity Jacobi diffusion requires a convex region; only "
            f"hex_disc is supported at M5'.2 (got shape={grid.shape!r}). "
            "Non-convex regions produce gradient pathologies at concavities "
            "(gen5 §Setup phase)."
        )


def seed_border_gravity(
    grid: "HexGrid",
    sources: tuple["GravitySource", ...],
    cell_size_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute the Newton-law gravity vector at each border cell, summed
    across all sources.

    Returns:
        g_seed:    float32[N, 2] — populated for border cells, zero elsewhere
        is_border: bool[N]       — True for cells that got a seed value
    """
    n = grid.cell_count
    g_seed = np.zeros((n, 2), dtype=np.float32)
    is_border = np.zeros(n, dtype=bool)

    if not sources:
        return g_seed, is_border

    for cid in range(n):
        if not is_border_cell(grid, cid):
            continue
        is_border[cid] = True
        q, r = grid.coords[cid]
        cx, cy = axial_to_cartesian(q, r, cell_size_m)

        gx, gy = 0.0, 0.0
        for src in sources:
            sx, sy = src.position
            dx, dy = sx - cx, sy - cy
            d = (dx * dx + dy * dy) ** 0.5
            if d < 1e-9:
                # Source at cell location — undefined direction. Skip.
                continue
            mag = G_CONST * src.mass_kg / (d * d)
            ux, uy = dx / d, dy / d
            gx += mag * ux
            gy += mag * uy
        g_seed[cid, 0] = gx
        g_seed[cid, 1] = gy
    return g_seed, is_border


def jacobi_diffuse_gravity(
    grid: "HexGrid",
    g_seed: np.ndarray,
    is_border: np.ndarray,
    n_iters: int = 30,
) -> np.ndarray:
    """Relax the gravity vector field with border cells frozen at their
    Newton-seeded values. Each interior cell becomes the mean of its
    valid neighbours per iteration. Vector components (gx, gy) diffuse
    independently — same Jacobi pattern, applied component-wise.

    Returns: float32[N, 2] gravity field.
    """
    n = grid.cell_count
    neighbors = np.array(grid.neighbors, dtype=np.int32)   # (N, 6)
    valid = neighbors >= 0                                  # (N, 6)
    nbr_count = valid.sum(axis=1).astype(np.float32)        # (N,)
    safe_count = np.maximum(nbr_count, 1.0)                 # avoid /0

    g = g_seed.copy().astype(np.float32)
    # Pad with zero row at index N for safe -1 indexing
    for _ in range(n_iters):
        g_padded = np.concatenate([g, np.zeros((1, 2), dtype=np.float32)])
        nbr_g = g_padded[neighbors]                          # (N, 6, 2)
        # Mask out invalid neighbours (shouldn't matter since g_padded[-1]=0,
        # but explicit zero is clearer and works regardless of np-pad layout)
        nbr_g = nbr_g * valid[:, :, None]
        avg = nbr_g.sum(axis=1) / safe_count[:, None]        # (N, 2)
        # Border cells stay frozen
        g_new = np.where(is_border[:, None], g, avg).astype(np.float32)
        g = g_new
    return g


def compute_gravity_field(
    grid: "HexGrid",
    sources: tuple["GravitySource", ...],
    world: "WorldConfig",
    n_iters: int = 30,
) -> np.ndarray:
    """Top-level: convex check, seed borders, Jacobi diffuse, return the
    full per-cell gravity vector field.

    Returns float32[N, 2]. When `sources` is empty, returns the zero field.
    """
    assert_convex(grid)
    if not sources:
        return np.zeros((grid.cell_count, 2), dtype=np.float32)
    g_seed, is_border = seed_border_gravity(grid, sources, world.cell_size_m)
    return jacobi_diffuse_gravity(grid, g_seed, is_border, n_iters=n_iters)
