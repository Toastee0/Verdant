# Element Table

The material database. Every physical constant used by the simulation comes from this table. NIST / CRC Handbook / Wikipedia reference values in SI units — no hand-tuned fudge numbers.

Location: `data/element_table.tsv` (to be created in M1 — see `../PLAN.md`).

## Why TSV

- Human-editable (spreadsheet, text editor).
- Trivially parseable in Python and C.
- Diff-friendly for source control.
- Column order is explicit — no YAML/JSON key reordering.

## Required columns

### Identity

| Column | Type | Example (Si) | Notes |
|---|---|---|---|
| symbol | str | Si | IUPAC symbol, case-sensitive |
| Z | u8 | 14 | Atomic number |
| name | str | Silicon | Display name |
| element_id | u8 | 14 | Runtime ID = Z for elements; compound alias ≥200 |

### Basic physics

| Column | Type | Unit | Example (Si) |
|---|---|---|---|
| molar_mass | f32 | g/mol | 28.085 |
| density_solid | f32 | kg/m³ | 2329 |
| density_liquid | f32 | kg/m³ | 2570 |
| density_gas_stp | f32 | kg/m³ | see note |
| specific_heat_solid | f32 | J/(kg·K) | 710 |
| specific_heat_liquid | f32 | J/(kg·K) | 1000 (est.) |
| specific_heat_gas | f32 | J/(kg·K) | 840 |
| thermal_conductivity_solid | f32 | W/(m·K) | 149 |
| thermal_conductivity_liquid | f32 | W/(m·K) | 60 |
| thermal_conductivity_gas | f32 | W/(m·K) | 0.02 (est.) |

(Gas density at STP is usually 0 for non-gaseous materials at standard T/P; include anyway for completeness.)

### Phase transitions

| Column | Type | Unit | Example (Si) |
|---|---|---|---|
| melt_K | f32 | K | 1687 |
| boil_K | f32 | K | 3538 |
| L_fusion | f32 | J/kg | 1.79e6 |
| L_vaporization | f32 | J/kg | 1.38e7 |
| critical_T | f32 | K | ~5159 (est.) |
| critical_P | f32 | Pa | ~4.5e7 (est.) |

### Solid mechanics (solid phase)

| Column | Type | Unit | Example (Si) |
|---|---|---|---|
| mohs_max | u8 | scale 1–10 | 7 |
| mohs_multiplier | f32 | dimensionless | 1.5 |
| elastic_modulus | f32 | Pa | 1.7e11 |
| elastic_limit | f32 | Pa | 1e10 (est.) |
| tensile_limit | f32 | Pa | 1.3e8 |

### Thermodynamic coupling (per phase)

| Column | Type | Example (Si) | Notes |
|---|---|---|---|
| P_U_coupling_solid | f32 | 0.01 | Low — most compression stores as strain |
| P_U_coupling_liquid | f32 | 0.1 | |
| P_U_coupling_gas | f32 | 1.0 | Full γ-factor adiabatic |

### Radiation

| Column | Type | Example (Si) |
|---|---|---|
| emissivity_solid | f32 | 0.6 |
| emissivity_liquid | f32 | 0.3 |
| albedo_solid | f32 | 0.3 |
| albedo_liquid | f32 | 0.5 |

(Emissivity and albedo are geometry-dependent; these are crude approximations.)

### Magnetism

| Column | Type | Unit | Example (Si) | Example (Fe) |
|---|---|---|---|---|
| is_ferromagnetic | bool | – | false | true |
| curie_K | f32 | K | 0 | 1043 |
| susceptibility | f32 | dimensionless | 0 | 200 (pre-saturation) |
| remanence_fraction | f32 | 0–1 | 0 | 0.7 |

### Rate multipliers (scenario-tunable defaults)

| Column | Type | Example |
|---|---|---|
| precipitation_rate_default | f32 | 1.0 |
| dissolution_rate_default | f32 | 1.0 |

These are per-element defaults; scenarios override via a `scenario.rate_multipliers` section.

### Encoding scales

For log-scale pressure encoding (see [`cell-struct.md`](cell-struct.md)):

| Column | Type | Example (Si) |
|---|---|---|
| pressure_mantissa_scale_gas | f32 | 1.0 (1 unit = 1 Pa) |
| pressure_mantissa_scale_liquid | f32 | 8.0 |
| pressure_mantissa_scale_solid | f32 | 8.0 |

Energy encoding scale:

| Column | Type | Example (Si) |
|---|---|---|
| energy_scale | f32 | 100 (1 unit = 100 J, fits realistic range in u16) |

## Compound aliases

Element IDs ≥ 200 are compound macros. Not kernel-visible — expanded at cell init:

```
compound_200 = water   = [(H, 114), (O, 141)]       # 11.1/88.9% by mass for H₂O
compound_201 = CO₂     = [(C, 70),  (O, 185)]       # 27.3/72.7%
compound_202 = NaCl    = [(Na, 101), (Cl, 154)]      # 39.3/60.7%
compound_203 = CaCO₃   = [(C, 30),  (O, 144), (Ca, 102)]
compound_204 = SiO₂    = [(Si, 119), (O, 136)]
...
```

Each compound ID's expansion lives in `data/compounds.tsv` (separate file).

## Tier ladder (for implementation)

Bring-up doesn't need all elements at once. Priority order:

- **Tier 0**: Si (alone)
- **Tier 1**: + H, O (via water compound)
- **Tier 2**: + C, Fe
- **Tier 3**: + N (atmosphere)
- **Tier 4**: + Al, K, Ca, Mg, Na (realistic silicate rock)
- **Tier 5+**: full palette as scenarios demand

See `../PLAN.md` for milestone-per-tier sequencing.

## Sourcing

References I'd use in column order:

| Column category | Source |
|---|---|
| Identity (Z, symbol, molar_mass) | IUPAC periodic table, wikipedia |
| Density, specific heat, thermal conductivity | NIST Webbook, CRC Handbook |
| Melt/boil, L_fusion/vaporization | NIST WebBook, CRC |
| Critical point | NIST fluid property data |
| Elastic modulus, limits | Material datasheets, CRC, MatWeb |
| Mohs max | Mineral reference, standard Mohs tables |
| Emissivity, albedo | Engineering Toolbox, NIST emissivity tables |
| Magnetism | Material datasheets, CRC |
| P↔U coupling | Derived from bulk modulus + thermal expansion coeff |
| Rate multipliers | Placeholder 1.0; scenarios tune |

Each row of the final TSV should cite specific source values in a companion `data/element_table_sources.md` note.

## Validation

Harness-side sanity checks:

- `molar_mass > 0`
- `0 <= melt_K < boil_K < critical_T`
- `density_solid > density_liquid > density_gas` (typical; exceptions like water allowed with annotation)
- `elastic_modulus > elastic_limit > 0`
- `tensile_limit > 0`
- `curie_K == 0` iff `is_ferromagnetic == false`
- `0 <= emissivity, albedo <= 1`

`verify.py` can load the element table and run these checks at init. Fail fast on malformed entries.

## Hash integrity

Every JSON emission includes `element_table_hash` — sha256 of the canonical TSV contents (sort by element_id, strip whitespace-only diffs). Cross-tick comparisons only valid if hashes match.

```
element_table_hash: "sha256:abc123..."
```

If two JSON emissions have different hashes, compare-tick tools refuse to diff (you're comparing runs with different ground-truth physics).

## File layout example

```
data/
  element_table.tsv              # primary table
  element_table_sources.md       # citations per row
  compounds.tsv                  # alias expansions
  scenarios/
    t0_static.yaml
    t1_ice_melt.yaml
    ...
```

Scenarios reference elements by symbol (`"Si"`, `"H"`) — the TSV provides the lookup into per-cell composition values.
