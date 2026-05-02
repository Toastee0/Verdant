"""
VerdantSim — Element Table loader & validator.

Loads `data/element_table.tsv`, validates physical sanity, and provides
encode/decode helpers for the u16 pressure and energy fields.

This is the single source of truth for material constants. The real reference
sim, `verify.py`, and any future CUDA kernel driver all consume from here.

See wiki/element-table.md for column semantics.
See wiki/cell-struct.md for encoding conventions.
"""

from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass, field, fields as _dc_fields
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Element:
    """Material constants for one element. All SI units unless noted."""

    # Identity
    symbol: str
    Z: int
    name: str
    element_id: int

    # Basic physics
    molar_mass: float                   # g/mol
    density_solid: float                # kg/m³
    density_liquid: float               # kg/m³
    density_gas_stp: float              # kg/m³  (0 for elements that aren't gaseous at STP)
    specific_heat_solid: float          # J/(kg·K)
    specific_heat_liquid: float         # J/(kg·K)
    specific_heat_gas: float            # J/(kg·K)
    thermal_conductivity_solid: float   # W/(m·K)
    thermal_conductivity_liquid: float  # W/(m·K)
    thermal_conductivity_gas: float     # W/(m·K)

    # Phase transitions
    melt_K: float
    boil_K: float
    L_fusion: float                     # J/kg
    L_vaporization: float               # J/kg
    critical_T: float                   # K
    critical_P: float                   # Pa

    # Solid mechanics
    mohs_max: int                       # 1..10
    mohs_multiplier: float              # typically power of 2 for shift decode
    elastic_modulus: float              # Pa
    elastic_limit: float                # Pa
    tensile_limit: float                # Pa

    # Thermodynamic P↔U coupling (per phase)
    P_U_coupling_solid: float
    P_U_coupling_liquid: float
    P_U_coupling_gas: float

    # Radiation
    emissivity_solid: float             # 0..1
    emissivity_liquid: float            # 0..1
    albedo_solid: float                 # 0..1
    albedo_liquid: float                # 0..1

    # Magnetism
    is_ferromagnetic: bool
    curie_K: float
    susceptibility: float
    remanence_fraction: float           # 0..1

    # Rate defaults (scenario-tunable)
    precipitation_rate_default: float
    dissolution_rate_default: float

    # u16 encoding scales (power-of-2 for shift decode)
    pressure_mantissa_scale_gas: int
    pressure_mantissa_scale_liquid: int
    pressure_mantissa_scale_solid: int
    energy_scale: float


# ----------------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------------

_BOOL_TRUE = {"true", "yes", "1", "t", "y"}
_BOOL_FALSE = {"false", "no", "0", "f", "n", ""}


def _coerce(raw: str, target_type: type) -> Any:
    """Coerce a TSV string cell to the target Python type."""
    s = raw.strip()
    if target_type is str:
        return s
    if target_type is int:
        # int() doesn't parse scientific floats; route through float for safety
        if any(c in s for c in ("e", "E", ".")):
            return int(float(s))
        return int(s)
    if target_type is float:
        return float(s)
    if target_type is bool:
        low = s.lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
        raise ValueError(f"Cannot parse boolean from {s!r}")
    raise TypeError(f"Unsupported type for TSV coercion: {target_type}")


def _expected_columns() -> list[tuple[str, type]]:
    """The (name, type) list for Element fields, in declaration order."""
    return [(f.name, f.type if isinstance(f.type, type) else _resolve_type(f.type))
            for f in _dc_fields(Element)]


def _resolve_type(annotation: Any) -> type:
    """Turn a string annotation (from `from __future__ import annotations`) into a type."""
    if isinstance(annotation, type):
        return annotation
    mapping = {"str": str, "int": int, "float": float, "bool": bool}
    if annotation in mapping:
        return mapping[annotation]
    raise TypeError(f"Unresolvable annotation: {annotation!r}")


# ----------------------------------------------------------------------------
# Loader
# ----------------------------------------------------------------------------

class ElementTableError(Exception):
    """Raised when the TSV is malformed or fails validation."""


@dataclass
class ElementTable:
    """The complete loaded table. Indexed by symbol and by element_id."""

    elements: dict[str, Element] = field(default_factory=dict)
    by_id: dict[int, Element] = field(default_factory=dict)
    source_path: Path | None = None
    source_hash: str = ""

    def __len__(self) -> int:
        return len(self.elements)

    def __iter__(self):
        return iter(self.elements.values())

    def __getitem__(self, key: str | int) -> Element:
        if isinstance(key, str):
            return self.elements[key]
        return self.by_id[key]

    def get(self, key: str | int, default=None) -> Element | None:
        try:
            return self[key]
        except KeyError:
            return default


def load_element_table(path: str | Path) -> ElementTable:
    """Parse a TSV file at `path` and return a validated ElementTable."""

    p = Path(path)
    if not p.is_file():
        raise ElementTableError(f"Element table not found: {p}")

    raw_bytes = p.read_bytes()
    # Normalize line endings before hashing so the hash is identical across
    # platforms regardless of git autocrlf state. Without this, a Windows
    # checkout (CRLF on disk) and a Linux checkout (LF on disk) produce
    # different hashes for the same logical content, breaking
    # element_table_hash compatibility checks (verify.py, diff_ticks.py).
    normalized = raw_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    source_hash = "sha256:" + hashlib.sha256(normalized).hexdigest()[:16]

    expected = _expected_columns()
    expected_names = [n for n, _ in expected]

    table = ElementTable(source_path=p, source_hash=source_hash)

    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        if header is None:
            raise ElementTableError("TSV is empty")

        # Validate header exactly matches dataclass field order
        if header != expected_names:
            missing = set(expected_names) - set(header)
            extra = set(header) - set(expected_names)
            msg = ["Header does not match expected columns."]
            if missing:
                msg.append(f"  missing: {sorted(missing)}")
            if extra:
                msg.append(f"  unexpected: {sorted(extra)}")
            if not missing and not extra:
                msg.append(f"  wrong order.\n  expected: {expected_names}\n  got:      {header}")
            raise ElementTableError("\n".join(msg))

        for line_idx, row in enumerate(reader, start=2):
            if not row or all(cell.strip() == "" for cell in row):
                continue  # blank line
            if row[0].startswith("#"):
                continue  # comment row
            if len(row) != len(expected):
                raise ElementTableError(
                    f"Line {line_idx}: wrong column count "
                    f"(got {len(row)}, expected {len(expected)})"
                )

            values = {}
            for (name, tp), cell in zip(expected, row):
                try:
                    values[name] = _coerce(cell, tp)
                except ValueError as e:
                    raise ElementTableError(
                        f"Line {line_idx}, column {name!r}: {e}"
                    ) from None

            element = Element(**values)
            _validate_element(element, line_idx)

            if element.symbol in table.elements:
                raise ElementTableError(
                    f"Line {line_idx}: duplicate symbol {element.symbol!r}"
                )
            if element.element_id in table.by_id:
                raise ElementTableError(
                    f"Line {line_idx}: duplicate element_id {element.element_id}"
                )
            table.elements[element.symbol] = element
            table.by_id[element.element_id] = element

    return table


# ----------------------------------------------------------------------------
# Per-element validation
# ----------------------------------------------------------------------------

def _validate_element(e: Element, line: int) -> None:
    """Sanity-check a single element. Raises ElementTableError on failure."""

    def fail(msg: str) -> None:
        raise ElementTableError(f"Line {line} ({e.symbol}): {msg}")

    # Identity
    if not e.symbol:
        fail("empty symbol")
    if e.Z <= 0:
        fail(f"Z must be > 0 (got {e.Z})")
    if not (0 < e.element_id < 256):
        fail(f"element_id must be in 1..255 (got {e.element_id})")

    # Mass & density
    if e.molar_mass <= 0:
        fail(f"molar_mass must be > 0 (got {e.molar_mass})")
    for f_name in ("density_solid", "density_liquid"):
        val = getattr(e, f_name)
        if val <= 0:
            fail(f"{f_name} must be > 0 (got {val})")
    if e.density_gas_stp < 0:
        fail(f"density_gas_stp must be >= 0 (got {e.density_gas_stp})")

    # Specific heats & conductivities
    for f_name in ("specific_heat_solid", "specific_heat_liquid", "specific_heat_gas",
                   "thermal_conductivity_solid", "thermal_conductivity_liquid",
                   "thermal_conductivity_gas"):
        val = getattr(e, f_name)
        if val < 0:
            fail(f"{f_name} must be >= 0 (got {val})")

    # Phase transitions
    if not (0 < e.melt_K < e.boil_K):
        fail(f"require 0 < melt_K < boil_K (got {e.melt_K}, {e.boil_K})")
    if e.critical_T <= e.boil_K:
        fail(f"critical_T must be > boil_K (got {e.critical_T}, boil {e.boil_K})")
    if e.L_fusion < 0 or e.L_vaporization < 0:
        fail("latent heats must be >= 0")
    if e.critical_P <= 0:
        fail(f"critical_P must be > 0 (got {e.critical_P})")

    # Mohs
    if not (1 <= e.mohs_max <= 10):
        fail(f"mohs_max must be in 1..10 (got {e.mohs_max})")
    if e.mohs_multiplier <= 1.0:
        fail(f"mohs_multiplier must be > 1 (got {e.mohs_multiplier})")

    # Solid mechanics ordering: elastic_limit should be <= tensile_limit,
    # and both < elastic_modulus (strain = stress/modulus < 1 at yield)
    if e.elastic_modulus <= 0:
        fail(f"elastic_modulus must be > 0 (got {e.elastic_modulus})")
    if e.elastic_limit <= 0 or e.tensile_limit <= 0:
        fail("elastic_limit and tensile_limit must be > 0")
    if e.elastic_limit > e.elastic_modulus:
        fail("elastic_limit must be <= elastic_modulus")
    # Tensile limit being slightly below elastic limit is unusual but legal for
    # brittle materials where fracture precedes yield. Warn via comment, not error.

    # P↔U couplings
    for f_name in ("P_U_coupling_solid", "P_U_coupling_liquid", "P_U_coupling_gas"):
        val = getattr(e, f_name)
        if not (0 <= val <= 1.0):
            fail(f"{f_name} must be in 0..1 (got {val})")

    # Radiation
    for f_name in ("emissivity_solid", "emissivity_liquid",
                   "albedo_solid", "albedo_liquid"):
        val = getattr(e, f_name)
        if not (0 <= val <= 1):
            fail(f"{f_name} must be in 0..1 (got {val})")

    # Magnetism
    if e.is_ferromagnetic:
        if e.curie_K <= 0:
            fail("ferromagnetic element requires curie_K > 0")
    else:
        if e.curie_K != 0 or e.susceptibility != 0 or e.remanence_fraction != 0:
            fail("non-ferromagnetic element must have curie_K, susceptibility, "
                 "remanence_fraction = 0")
    if not (0 <= e.remanence_fraction <= 1):
        fail(f"remanence_fraction must be in 0..1 (got {e.remanence_fraction})")

    # Rate defaults
    if e.precipitation_rate_default < 0 or e.dissolution_rate_default < 0:
        fail("rate defaults must be >= 0")

    # Encoding scales must be positive and (for the pressure scales) powers of 2
    for f_name in ("pressure_mantissa_scale_gas",
                   "pressure_mantissa_scale_liquid",
                   "pressure_mantissa_scale_solid"):
        val = getattr(e, f_name)
        if val <= 0:
            fail(f"{f_name} must be > 0 (got {val})")
        if (val & (val - 1)) != 0:
            fail(f"{f_name} should be a power of 2 for shift-decode (got {val})")
    if e.energy_scale <= 0:
        fail(f"energy_scale must be > 0 (got {e.energy_scale})")

    # Framework convention: mohs_multiplier is a power of 2 for shift-decode.
    # Not strictly required but strongly recommended; downgrade to warning by
    # silently allowing floats. If we want to enforce, flip to `fail`.
    # (Leaving permissive for now; a future per-material calibration may use
    # non-integer multipliers.)


# ----------------------------------------------------------------------------
# Pressure & energy encode/decode
# ----------------------------------------------------------------------------

# Phase identifiers. Match cell-struct.md 2-bit phase field.
PHASE_SOLID = 0
PHASE_LIQUID = 1
PHASE_GAS = 2
PHASE_PLASMA = 3

PHASE_NAMES = {
    PHASE_SOLID: "solid",
    PHASE_LIQUID: "liquid",
    PHASE_GAS: "gas",
    PHASE_PLASMA: "plasma",
}

# u16 mantissa mask: bits 0..11 (12 bits, 0..4095)
MANTISSA_MASK = 0x0FFF
MANTISSA_MAX = 4095


def decode_pressure(element: Element, raw: int, phase: int, mohs_level: int = 1) -> float:
    """Decode a u16 pressure_raw into absolute Pa for the given element/phase.

    Uses the power-of-2 scales from the element table. For solids the scale
    additionally shifts by (mohs_level - 1) to implement the Mohs ladder.
    """
    mantissa = raw & MANTISSA_MASK

    if phase == PHASE_GAS:
        return float(mantissa * element.pressure_mantissa_scale_gas)
    if phase == PHASE_LIQUID:
        return float(mantissa * element.pressure_mantissa_scale_liquid)
    if phase == PHASE_SOLID:
        if mohs_level < 1:
            mohs_level = 1
        base = mantissa * element.pressure_mantissa_scale_solid
        return float(base) * (element.mohs_multiplier ** (mohs_level - 1))
    if phase == PHASE_PLASMA:
        # Plasma uses gas scale × 64 for now; revisit when plasma is implemented.
        return float(mantissa * element.pressure_mantissa_scale_gas * 64)
    raise ValueError(f"Unknown phase {phase}")


def encode_pressure(element: Element, pressure_pa: float, phase: int, mohs_level: int = 1) -> int:
    """Encode a Pa pressure into a u16 mantissa. Clamps to MANTISSA_MAX on overflow.

    Returns just the mantissa (12 bits). The caller packs it into the full u16
    with phase / mohs bits as needed — that packing is cell-struct's job.
    """
    if phase == PHASE_GAS:
        mantissa = pressure_pa / element.pressure_mantissa_scale_gas
    elif phase == PHASE_LIQUID:
        mantissa = pressure_pa / element.pressure_mantissa_scale_liquid
    elif phase == PHASE_SOLID:
        if mohs_level < 1:
            mohs_level = 1
        scale = element.pressure_mantissa_scale_solid * (element.mohs_multiplier ** (mohs_level - 1))
        mantissa = pressure_pa / scale
    elif phase == PHASE_PLASMA:
        mantissa = pressure_pa / (element.pressure_mantissa_scale_gas * 64)
    else:
        raise ValueError(f"Unknown phase {phase}")

    if mantissa < 0:
        return 0
    # round-half-even for deterministic cross-platform behavior
    m_int = int(round(mantissa))
    if m_int > MANTISSA_MAX:
        return MANTISSA_MAX
    return m_int


def decode_energy(element: Element, raw: int) -> float:
    """Decode u16 raw energy into Joules per cell."""
    return float(raw) * element.energy_scale


def encode_energy(element: Element, joules: float) -> int:
    """Encode J/cell into u16, clamped to 0..65535."""
    v = int(round(joules / element.energy_scale))
    if v < 0:
        return 0
    if v > 0xFFFF:
        return 0xFFFF
    return v


def solid_pressure_ceiling(element: Element, mohs_level: int) -> float:
    """Return the maximum encodable pressure (Pa) at the given Mohs level.

    Useful for scenario sanity checks: confirm elastic_limit < ceiling(1) so the
    elastic regime is representable before pressure saturates.
    """
    return decode_pressure(element, MANTISSA_MAX, PHASE_SOLID, mohs_level)


# ----------------------------------------------------------------------------
# CLI entry point — smoke test
# ----------------------------------------------------------------------------

def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Load and validate an element table TSV.")
    ap.add_argument("path", type=Path, nargs="?",
                    default=Path(__file__).resolve().parent.parent / "data" / "element_table.tsv")
    ap.add_argument("--symbol", default=None,
                    help="Show detailed info for one element symbol")
    args = ap.parse_args()

    try:
        table = load_element_table(args.path)
    except ElementTableError as e:
        print(f"ERROR: {e}")
        return 1

    print(f"Loaded element table: {args.path}")
    print(f"  hash:     {table.source_hash}")
    print(f"  elements: {len(table)}  ({', '.join(e.symbol for e in table)})")
    print()

    target_symbols = [args.symbol] if args.symbol else [e.symbol for e in table]
    for sym in target_symbols:
        e = table.get(sym)
        if e is None:
            print(f"  (no such symbol: {sym})")
            continue

        print(f"--- {e.symbol} ({e.name}, Z={e.Z}) ---")
        print(f"  molar mass:        {e.molar_mass} g/mol")
        print(f"  density  s/l/g:    {e.density_solid} / {e.density_liquid} / {e.density_gas_stp} kg/m³")
        print(f"  melt / boil:       {e.melt_K} K / {e.boil_K} K")
        print(f"  L_f / L_v:         {e.L_fusion:.3e} / {e.L_vaporization:.3e} J/kg")
        print(f"  mohs_max:          {e.mohs_max}")
        print(f"  mohs_multiplier:   {e.mohs_multiplier}")
        print(f"  elastic_modulus:   {e.elastic_modulus:.3e} Pa")
        print(f"  elastic_limit:     {e.elastic_limit:.3e} Pa")
        print(f"  tensile_limit:     {e.tensile_limit:.3e} Pa")
        print(f"  ferromagnetic:     {e.is_ferromagnetic}")
        print()

        # Pressure scale sanity check
        print("  Solid pressure ceilings by Mohs level:")
        for m in (1, 2, 3, 5, 7, 10):
            if m > e.mohs_max:
                continue
            p = solid_pressure_ceiling(e, m)
            unit = "MPa" if p < 1e9 else "GPa"
            val = p / 1e6 if p < 1e9 else p / 1e9
            print(f"    Mohs {m:2d}:  {val:8.2f} {unit}")
        ceiling_1 = solid_pressure_ceiling(e, 1)
        ratio = ceiling_1 / e.elastic_limit
        status = "OK" if ceiling_1 > e.elastic_limit else "TOO LOW — ratchet fires before yield"
        print(f"  Mohs-1 ceiling / elastic_limit = {ratio:.2f}×  [{status}]")
        print()

        # Encode/decode round-trip spot checks
        print("  Encode/decode round trip:")
        for phase_id, phase_name in [(PHASE_GAS, "gas"), (PHASE_LIQUID, "liquid"), (PHASE_SOLID, "solid")]:
            for pa in [1e3, 1e5, 1e7, 1e8]:
                mohs = 1 if phase_id == PHASE_SOLID else 0
                enc = encode_pressure(e, pa, phase_id, mohs)
                dec = decode_pressure(e, enc, phase_id, mohs)
                err = abs(dec - pa) / pa if pa > 0 else 0
                marker = "OK" if err < 0.01 or enc == MANTISSA_MAX else f"err={err*100:.1f}%"
                clamped = " (CLAMPED)" if enc == MANTISSA_MAX else ""
                print(f"    {phase_name:6s}  {pa:10.1e} Pa -> mantissa {enc:5d} -> {dec:10.1e} Pa  {marker}{clamped}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
