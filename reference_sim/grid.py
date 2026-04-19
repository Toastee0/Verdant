"""
Hex grid substrate.

Axial coordinates (q, r). Six neighbors per cell. Canonical neighbor ordering
is fixed — this is what the `gradient` array in the emission schema indexes
into and what `cohesion[cell][direction]` references.

Canonical neighbor directions (axial q, r deltas):
    0: (+1,  0)   east
    1: (+1, -1)   north-east
    2: ( 0, -1)   north-west
    3: (-1,  0)   west
    4: (-1, +1)   south-west
    5: ( 0, +1)   south-east

Position 0 is "east" then it wraps counter-clockwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# Canonical neighbor direction deltas in axial (q, r). DO NOT reorder — the
# emission schema and debug harness index into this tuple.
NEIGHBOR_DELTAS: tuple[tuple[int, int], ...] = (
    ( 1,  0),   # 0: east
    ( 1, -1),   # 1: north-east
    ( 0, -1),   # 2: north-west
    (-1,  0),   # 3: west
    (-1,  1),   # 4: south-west
    ( 0,  1),   # 5: south-east
)

# Opposite direction lookup: index is a direction, value is the 180° opposite.
# Useful for scatter-gather: if A bids to B in direction d, B receives from A
# in direction OPPOSITE[d].
OPPOSITE_DIRECTION: tuple[int, ...] = (3, 4, 5, 0, 1, 2)


@dataclass(frozen=True)
class HexGrid:
    """A finite hex grid. Stores the list of (q, r) coordinates and a dense
    neighbor index table.

    `coords` is the ordered list of cell (q, r) tuples.
    `id_of[(q, r)]` returns the cell id (index into the cell arrays).
    `neighbors[cell_id][dir]` returns either the neighbor's cell id or -1 if
       the neighbor falls outside the grid.
    """

    coords: tuple[tuple[int, int], ...]
    id_of: dict[tuple[int, int], int]
    neighbors: tuple[tuple[int, ...], ...]   # [cell_id][direction] -> neighbor_id or -1
    shape: str                                # e.g. "hex_disc"
    rings: int                                # ring count (0 = single cell)

    @property
    def cell_count(self) -> int:
        return len(self.coords)


def axial_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Axial-coord hex distance (manhattan-style on the hex lattice)."""
    aq, ar = a
    bq, br = b
    return (abs(aq - bq) + abs(aq + ar - bq - br) + abs(ar - br)) // 2


def ring_of(coord: tuple[int, int]) -> int:
    """Ring index for a cell: 0 for center, increasing outward."""
    return axial_distance(coord, (0, 0))


def hex_disc_coords(rings: int) -> list[tuple[int, int]]:
    """Generate (q, r) axial coordinates for a hex disc with N rings.

    0 rings = single center cell (1 cell total).
    5 rings = 91-cell disc (the bring-up substrate).

    Cells are ordered by ring, then by (q, r) within each ring — deterministic
    and stable across runs / platforms.
    """
    if rings < 0:
        raise ValueError(f"rings must be >= 0 (got {rings})")

    coords: list[tuple[int, int]] = []
    for q in range(-rings, rings + 1):
        r_min = max(-rings, -q - rings)
        r_max = min(rings, -q + rings)
        for r in range(r_min, r_max + 1):
            coords.append((q, r))
    # Sort: ring, then q, then r — deterministic ordering
    coords.sort(key=lambda qr: (ring_of(qr), qr[0], qr[1]))
    return coords


def build_hex_disc(rings: int) -> HexGrid:
    """Construct a complete HexGrid for a disc of the given ring count."""
    coords = tuple(hex_disc_coords(rings))
    id_of = {qr: idx for idx, qr in enumerate(coords)}

    neighbor_table: list[tuple[int, ...]] = []
    for (q, r) in coords:
        row: list[int] = []
        for (dq, dr) in NEIGHBOR_DELTAS:
            nq, nr = q + dq, r + dr
            row.append(id_of.get((nq, nr), -1))
        neighbor_table.append(tuple(row))

    return HexGrid(
        coords=coords,
        id_of=id_of,
        neighbors=tuple(neighbor_table),
        shape="hex_disc",
        rings=rings,
    )


def valid_neighbors(grid: HexGrid, cell_id: int) -> Iterable[tuple[int, int]]:
    """Yield (direction, neighbor_id) for each in-grid neighbor. Skips -1 entries."""
    for direction, nid in enumerate(grid.neighbors[cell_id]):
        if nid != -1:
            yield direction, nid


def is_boundary(grid: HexGrid, cell_id: int) -> bool:
    """True if any of this cell's neighbor slots is -1 (outside the grid)."""
    return any(nid == -1 for nid in grid.neighbors[cell_id])
