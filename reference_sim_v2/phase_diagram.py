"""
Phase diagram lookup — gen5 §"Phase resolve".

Each element has a (T, P) → (phase, initial_mohs) lookup table. M5'.5
ships a 1D T-only stub for Si (Tier 0 / 1 elements at typical conditions
have weak P dependence; full 2D phase diagrams land at M6'+ when H₂O's
triple-point matters).

CSV format (`data/phase_diagrams/<symbol>.csv`):
    T_K,phase,initial_mohs
    0,solid,6
    1687,solid,6
    1687.001,liquid,0
    ...

Lookup rule: pick the highest-T row where row.T_K ≤ query.T_K. Phase is
the column value at that row. P is currently ignored (M5'.5 stub).
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .cell import (
    PHASE_GAS,
    PHASE_LIQUID,
    PHASE_PLASMA,
    PHASE_SOLID,
)


PHASE_FROM_NAME = {
    "solid":  PHASE_SOLID,
    "liquid": PHASE_LIQUID,
    "gas":    PHASE_GAS,
    "plasma": PHASE_PLASMA,
}


@dataclass(frozen=True)
class PhaseDiagram1D:
    """T-only phase diagram. P axis ignored at M5'.5."""
    element_symbol: str
    # Sorted ascending by T_K
    rows: tuple[tuple[float, int, int], ...]   # (T_K, phase_id, initial_mohs)
    source_path: Path | None = None
    source_hash: str = ""

    def lookup(self, T_K: float, P_Pa: float = 0.0) -> tuple[int, int]:
        """Return (phase_id, initial_mohs) for the queried (T, P).

        For multi-row tables, picks the row with the highest T_K that's
        still ≤ T_K. Out-of-range high T returns the last row; out-of-range
        low T returns the first."""
        if not self.rows:
            return (PHASE_SOLID, 0)
        # Linear scan (rows are short; <20 entries typically)
        best = self.rows[0]
        for row in self.rows:
            if row[0] <= T_K:
                best = row
            else:
                break
        return (best[1], best[2])

    def transition_threshold_T(self, current_phase: int, target_phase: int) -> float | None:
        """Return the boundary temperature between `current_phase` and
        `target_phase` per the diagram, or None if no boundary exists.

        Used by energy-balanced phase transitions: the cell's T after a
        partial transition should land exactly on this boundary, so the
        transition is self-stabilising rather than oscillation-prone.

        Convention: pick the *first* row whose phase equals `target_phase`
        and whose preceding row's phase equals `current_phase` (or vice
        versa, scanning either direction). For the typical Tier 0/1
        diagrams this returns the lower-T side for solid↔liquid, the
        liquid-side T for liquid↔gas, etc. — close enough to the true
        boundary for the energy-balance computation, which is dominated
        by L × Δm anyway.
        """
        if len(self.rows) < 2:
            return None
        for i in range(len(self.rows) - 1):
            a_T, a_phase, _ = self.rows[i]
            b_T, b_phase, _ = self.rows[i + 1]
            if a_phase == current_phase and b_phase == target_phase:
                return float(b_T)   # boundary is the first target-phase T
            if a_phase == target_phase and b_phase == current_phase:
                return float(a_T)   # boundary is the last target-phase T
        return None


def load_phase_diagram(path: str | Path) -> PhaseDiagram1D:
    """Load a phase diagram CSV into a PhaseDiagram1D."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"phase diagram not found: {p}")

    raw_bytes = p.read_bytes()
    # Normalise line endings before hashing — same lesson as element_table
    normalized = raw_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    source_hash = "sha256:" + hashlib.sha256(normalized).hexdigest()[:16]

    rows: list[tuple[float, int, int]] = []
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            if row[0].strip().lower() in ("t_k", "t"):
                continue   # header
            try:
                T = float(row[0].strip())
                phase_str = row[1].strip().lower()
                mohs = int(row[2].strip())
            except (IndexError, ValueError):
                continue
            phase = PHASE_FROM_NAME.get(phase_str)
            if phase is None:
                raise ValueError(f"unknown phase {phase_str!r} in {p}")
            rows.append((T, phase, mohs))
    rows.sort(key=lambda r: r[0])
    if not rows:
        raise ValueError(f"phase diagram {p} contains no rows")

    # Element symbol from the file stem (e.g., "Si.csv" → "Si")
    return PhaseDiagram1D(
        element_symbol=p.stem,
        rows=tuple(rows),
        source_path=p,
        source_hash=source_hash,
    )


def load_phase_diagrams_for_table(
    table,
    diagrams_dir: Path,
) -> dict[int, PhaseDiagram1D]:
    """Load `<symbol>.csv` for every element in `table`. Returns a dict
    keyed by element_id. Missing files are skipped silently — scenarios
    that need phase resolution for an element without a diagram will see
    no transitions for that element."""
    out: dict[int, PhaseDiagram1D] = {}
    for element in table:
        path = diagrams_dir / f"{element.symbol}.csv"
        if not path.is_file():
            continue
        out[element.element_id] = load_phase_diagram(path)
    return out
