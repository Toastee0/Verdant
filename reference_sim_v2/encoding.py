"""
Log-scale u16 encoding for energy_raw — gen5 §"State representation".

The Tier 0 stub mapped energy_raw = round(E_J / element.energy_scale) — a
linear cast. For dense cells (solid silicon at 0.01 m cell size, mass ~2.3 mg)
this gives ~1 J / quantum and ~0.6 K / quantum: fine. For sparse cells
(gas-phase water at the same cell size, mass ~0.83 µg) the same 1 J / quantum
becomes ~175 K / quantum — useless near phase boundaries.

Log encoding solves this. We store `raw = round(log10(1 + E_J) × M)` where
`M = 10923 ≈ 65535 / log10(1 + 1e6)`. The +1 inside the log makes E=0 J
map cleanly to raw=0, and the multiplier stretches the encodable range
across the full u16 with sub-µJ resolution at the low end and ~17 J/quantum
at the high end:

      E_J     raw     dE/dRaw (resolution)
    1e-3       5         0.0005 J/quantum
    1.0     3289         0.00046 J/quantum
    100    22049         0.046 J/quantum
    1e4    43653         4.6 J/quantum
    1e6    65535        460 J/quantum

The per-element `energy_scale` column in element_table.tsv loses its
purpose under log encoding — the log scale itself handles the dynamic
range. Keep the column for now (Tier 0 cross-validation reads it) but
the gen5 path ignores it.

Decoding round-trips through f32 with negligible loss across the
encodable range. Cells outside the range (E_J < 0, E_J > 1e6) saturate
at the bounds — out-of-band scenarios should be redesigned.
"""

from __future__ import annotations

import numpy as np


# 65535 / log10(1 + 1e6) ≈ 10922.5 — pick an integer multiplier so the
# round-trip is reproducible across machine f64 representations.
LOG_E_MULTIPLIER: float = 10923.0


def encode_energy_J(E_J) -> np.ndarray:
    """Joules (≥ 0) → u16 log-encoded raw.

    Negative values are clamped to 0 (unphysical). Values above the
    encodable range (≈ 1e6 J at M=10923) saturate at u16 max."""
    arr = np.asarray(E_J, dtype=np.float64)
    arr = np.maximum(arr, 0.0)
    raw_f = np.log10(1.0 + arr) * LOG_E_MULTIPLIER
    return np.clip(np.round(raw_f), 0.0, 65535.0).astype(np.uint16)


def decode_energy_J(raw) -> np.ndarray:
    """u16 raw → joules (f32). Inverse of `encode_energy_J`."""
    arr = np.asarray(raw, dtype=np.float64)
    return (np.power(10.0, arr / LOG_E_MULTIPLIER) - 1.0).astype(np.float32)


def encode_energy_J_scalar(E_J: float) -> int:
    """Scalar convenience (returns Python int for clean scenario init code)."""
    return int(encode_energy_J(np.array([E_J]))[0])


def decode_energy_J_scalar(raw: int) -> float:
    return float(decode_energy_J(np.array([raw]))[0])
