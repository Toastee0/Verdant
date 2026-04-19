"""
u8 cell flag bits. See `wiki/flags.md` for semantics.

Convention: each flag is a single bit. Flags compose via bitwise OR. The
entire set fits in a u8.
"""

from __future__ import annotations

# Persistent (describe what kind of cell it is; set at init, rarely change)
NO_FLOW         = 1 << 0   # mass cannot cross this cell's boundary
RADIATES        = 1 << 1   # emits blackbody radiation at Stage 4
INSULATED       = 1 << 2   # thermal conductivity across boundary = 0
FIXED_STATE     = 1 << 3   # stored state never changes (walls, sources, drains)

# Per-tick transient (cleared each tick or each sub-iteration)
CULLED          = 1 << 4   # didn't converge within sub-iteration budget
FRACTURED       = 1 << 5   # solid cell has exceeded tensile limit
RATCHETED       = 1 << 6   # ratcheting fired this tick (cleared at tick end)
EXCLUDED        = 1 << 7   # Tier 3 overflow hit; numerically frozen until rejoin


# Preset wall recipes — scenario init uses these to authoring convenience.
PRESET_SEALED_INSULATED = NO_FLOW | INSULATED | FIXED_STATE
PRESET_SEALED_RADIATIVE = NO_FLOW | RADIATES | FIXED_STATE
PRESET_FIXED_T_SOURCE   = NO_FLOW | FIXED_STATE
PRESET_OPEN_DRAIN       = FIXED_STATE
PRESET_RIGID_BARRIER    = NO_FLOW | INSULATED


# Named bit-list for debug emission (schema-v1 uses bool-per-flag form).
FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (NO_FLOW,      "no_flow"),
    (RADIATES,     "radiates"),
    (INSULATED,    "insulated"),
    (FIXED_STATE,  "fixed_state"),
    (CULLED,       "culled"),
    (FRACTURED,    "fractured"),
    (RATCHETED,    "ratcheted_this_tick"),
    (EXCLUDED,     "excluded"),
)


def flags_to_dict(flags_u8: int) -> dict[str, bool]:
    """Expand a packed u8 flag byte into the schema-v1 named-bool dict."""
    return {name: bool(flags_u8 & bit) for bit, name in FLAG_NAMES}


def flags_from_dict(d: dict[str, bool]) -> int:
    """Pack a schema-v1 named-bool dict back into a u8."""
    out = 0
    for bit, name in FLAG_NAMES:
        if d.get(name, False):
            out |= bit
    return out


def describe(flags_u8: int) -> str:
    """Human-readable compact flag summary, e.g. 'NO_FLOW|RADIATES'."""
    active = [name.upper() for bit, name in FLAG_NAMES if flags_u8 & bit]
    return "|".join(active) if active else "-"
