# Element Table — Sources and Notes

Per-field citations and provenance for `element_table.tsv`. Every non-derived value should trace to a reputable source; derived values (scale factors, rate defaults) are documented with their derivation logic.

Values are SI unless otherwise noted.

---

## Silicon (Si, Z=14)

Polycrystalline α-Si assumed unless noted. Reference state: 298 K, 1 atm, solid crystalline.

### Identity

| Field | Value | Source |
|---|---|---|
| symbol | Si | IUPAC |
| Z | 14 | IUPAC |
| name | Silicon | IUPAC |
| element_id | 14 | Convention: element_id = Z for periodic elements |

### Basic physics

| Field | Value | Unit | Source / Notes |
|---|---|---|---|
| molar_mass | 28.085 | g/mol | IUPAC 2021 standard atomic weight (natural Si isotope mix) |
| density_solid | 2329 | kg/m³ | At 298 K, crystalline. CRC Handbook 97th ed. |
| density_liquid | 2570 | kg/m³ | At melt point 1687 K. **Note: Si is denser liquid than solid**, like water. Mills (2002) "Recommended values of thermophysical properties for selected commercial alloys." |
| density_gas_stp | 0 | kg/m³ | Si is not gaseous at STP; set to 0 as placeholder. Real gas density at boiling point ≈ 1.4 kg/m³ (ideal gas estimate from molar mass). |
| specific_heat_solid | 710 | J/(kg·K) | At 298 K. CRC. Rises to ~900 near melt. Treating as constant for Tier 0. |
| specific_heat_liquid | 968 | J/(kg·K) | Mills & Keene (1987), liquid Si at melt point. |
| specific_heat_gas | 840 | J/(kg·K) | Rough estimate from 3/2 R / M for monatomic gas ≈ 440; vibrational contributions at high T push to ~800-900. Needs refinement. |
| thermal_conductivity_solid | 149 | W/(m·K) | At 300 K, pure crystalline Si. CRC. Falls sharply with T (only ~25 at 1500 K); constant approximation for Tier 0. |
| thermal_conductivity_liquid | 56 | W/(m·K) | Yamamoto et al. (1991), liquid Si near melt. |
| thermal_conductivity_gas | 0.020 | W/(m·K) | Rough estimate; gaseous Si is rare in any normal scenario. |

### Phase transitions

| Field | Value | Unit | Source |
|---|---|---|---|
| melt_K | 1687 | K | NIST WebBook (1687.15 K, rounded). |
| boil_K | 3538 | K | NIST (at 1 atm). |
| L_fusion | 1.788e6 | J/kg | 50.21 kJ/mol / 28.085 g/mol. CRC. |
| L_vaporization | 1.364e7 | J/kg | 383 kJ/mol / 28.085 g/mol. CRC. |
| critical_T | 5159 | K | Estimated (no direct measurement). Various theoretical and semi-empirical estimates cluster around 5000–5200 K. Value from Boivineau et al. (2006), likely ±10% uncertainty. |
| critical_P | 5.3e7 | Pa | Estimated, same source. Use with caution. |

### Solid mechanics

| Field | Value | Unit | Source / Notes |
|---|---|---|---|
| mohs_max | 7 | scale 1–10 | Crystalline Si measures 6.5 on Mohs; rounded to 7. |
| mohs_multiplier | 2.0 | — | Power-of-2 framework convention: each Mohs level doubles the pressure ceiling. Decode is a bit-shift (`mantissa << (scale_shift + mohs - 1)`), not a float multiply. Not material-specific currently; per-material tuning deferred to Tier 2+ when cross-element comparison is possible. |
| elastic_modulus | 1.70e11 | Pa (170 GPa) | Young's modulus, polycrystalline average. Anisotropic in monocrystalline (130–188 GPa across axes). CRC / MatWeb. |
| elastic_limit | 1.20e8 | Pa (120 MPa) | Bulk polycrystalline yield stress. Si is brittle — "yield" and "tensile" are nearly the same value; treating as elastic_limit < tensile_limit for sim flexibility. Monocrystalline whiskers can reach ~7 GPa. |
| tensile_limit | 1.30e8 | Pa (130 MPa) | Bulk polycrystalline ultimate tensile strength. Highly sample-dependent. |

### Thermodynamic coupling (P↔U)

| Field | Value | Source / Notes |
|---|---|---|
| P_U_coupling_solid | 0.0078125 (= 2⁻⁷ = 1/128) | Low — Si has low thermal expansion coefficient (2.6×10⁻⁶ /K), most compression stores as elastic strain rather than heat. Power-of-2 chosen to match `dt = 1/128 s`; a per-tick coupling step is a bit-shift. Framework default; revisit after Tier 0 scenario testing. |
| P_U_coupling_liquid | 0.125 (= 2⁻³ = 1/8) | Framework default for liquids. Power-of-2 for cheap decode. |
| P_U_coupling_gas | 1.0 (= 2⁰) | Full γ-factor adiabatic compression for ideal-gas-like behavior. |

### Radiation

| Field | Value | Source / Notes |
|---|---|---|
| emissivity_solid | 0.60 | Rough-surface crystalline Si, 8–14 µm band. Polished Si ≈ 0.3; oxidized surface ≈ 0.7. Picked mid-range. Engineering Toolbox. |
| emissivity_liquid | 0.30 | Liquid Si near melt point. Shvarev et al. (1978). |
| albedo_solid | 0.30 | Crystalline Si at visible wavelengths. Variable; depends on doping, surface finish. Rough approximation. |
| albedo_liquid | 0.70 | Liquid metals are reflective; placeholder. |

### Magnetism

| Field | Value | Source |
|---|---|---|
| is_ferromagnetic | false | Si is diamagnetic (very weak, negative susceptibility). Not treated as magnetic in the sim. |
| curie_K | 0 | N/A (non-ferromagnetic) |
| susceptibility | 0 | Real value is −3.9×10⁻⁶ (dimensionless volume susceptibility). For sim purposes, treated as 0. |
| remanence_fraction | 0 | N/A |

### Rate multipliers

| Field | Value | Source |
|---|---|---|
| precipitation_rate_default | 1.0 | Placeholder. Scenarios tune for visible geological behavior on playable timescales. Real silica precipitation is extraordinarily slow. |
| dissolution_rate_default | 1.0 | Placeholder. Real silicate dissolution in water is slow (pH and T dependent). |

### Encoding scales

These are **sim-specific** derivations, not NIST-sourced. They control how the u16 `pressure_raw` and `energy` fields map to SI values. Power-of-2 scales throughout so decoding is a bit-shift rather than a float multiply.

| Field | Value | Derivation |
|---|---|---|
| pressure_mantissa_scale_gas | 64 (= 2⁶) | Pa per mantissa unit. Max encodable gas pressure = 4095 × 64 = 262.08 kPa (~2.6 atm). Range covers normal atmospheric and elevated-P gas scenarios. Decode: `mantissa << 6`. |
| pressure_mantissa_scale_liquid | 32768 (= 2¹⁵) | Pa per unit. Max liquid pressure = 4095 × 32768 ≈ 134 MPa (~1330 atm). Covers deep-ocean to mid-crust pressures. Decode: `mantissa << 15`. |
| pressure_mantissa_scale_solid | 65536 (= 2¹⁶) | Pa per unit at Mohs 1. Decoded = mantissa × 65536 × 2^(mohs-1). At Mohs 1: 0–268 MPa (2× Si elastic_limit). At Mohs 7 (Si max): 0–17.2 GPa (covers deep-crustal pressures). Decode: `mantissa << (16 + mohs - 1)`. |
| energy_scale | 1.0 | J per unit. Cell volume at 1 cm³ has max thermal content ~43 kJ (298 K → fully vaporized Si). u16 range (65535) covers this with ~50% headroom. |

### Solid-phase pressure ceiling sanity check

With `pressure_mantissa_scale_solid = 65536` and `mohs_multiplier = 2.0`:

| Mohs level | Max decoded pressure (Pa) | Notes |
|---:|---:|---|
| 1 | 268 MPa | 2× Si elastic_limit (120 MPa) — safe headroom for elastic regime before ratchet fires. Also covers Si tensile_limit (130 MPa). |
| 2 | 537 MPa | Post-ratchet compacted Si. |
| 3 | 1.07 GPa | |
| 5 | 4.29 GPa | Crustal-rock range. |
| 7 | 17.2 GPa | Si mohs_max. Deep-crust granite pressures. |
| 10 | 137 GPa | Meteoritic-impact / deep-mantle / diamond-formation range. |

The critical property: Mohs 1 ceiling (268 MPa) > Si elastic_limit (120 MPa). A cell loaded elastically can reach the yield point *before* saturating its u16, giving the elastic regime a legitimate range before ratcheting is triggered. If the ceiling were below elastic_limit, the sim would ratchet before the material physically should.

### Derivation sanity check

For a 1 cm³ Si cell at 298 K:
- mass = 2329 kg/m³ × 1e-6 m³ = 2.329 g = 0.002329 kg
- Energy to fully vaporize from 0 K: thermal heating (0→1687) + L_fusion + (1687→3538 as liquid) + L_vap
  - = 0.002329 × (710 × 1687 + 1.788e6 + 968 × 1851 + 1.364e7)
  - = 0.002329 × (1,197,770 + 1,788,000 + 1,791,768 + 13,640,000)
  - = 0.002329 × 18,417,538
  - ≈ 42,895 J
- u16 range at energy_scale = 1: 0–65535 J ✓ fits with headroom

For pressure at mohs 7, mantissa 4095:
- decoded = 4095 × 65536 × 2⁶ = 4095 × 65536 × 64 ≈ 17.2 GPa
- Comfortably above Si's 130 MPa tensile limit — wide headroom for strongly compressed post-ratchet material.

---

## Uncertainties and known gaps

- **`mohs_multiplier` is a framework placeholder** at 1.5 for all materials currently. Real Mohs scale is not strictly exponential; this needs per-element calibration once we have more elements to compare (Tier 2+).
- **`elastic_limit` for brittle materials like Si is ambiguous** — brittle materials fail by fracture, not by yielding. Treating as "below this, pure elastic; above this but below tensile_limit, ratchet; above tensile, fracture." Not strictly physical but gives the sim a plastic regime to work with.
- **Specific heat and thermal conductivity in gas phase** are weakly constrained. Si gas is rare; values are order-of-magnitude estimates.
- **Critical point** is theoretical (Si vapor at ~5000 K is too high-T to measure directly).
- **Emissivity/albedo** are surface-finish dependent; values are typical.

## What's deliberately deferred

- **Temperature-dependent properties** (k(T), c_p(T), solubility(T)). Starting with constants at reference state; upgrade when a scenario demands it.
- **Anisotropic properties** (elastic modulus along different crystal axes). Using polycrystalline averages.
- **Doping effects** (pure crystalline Si only for now; no dopant-modulated thermal or electrical behavior).
- **Amorphous Si** (metallurgical vs. semiconductor-grade). Only α-Si (crystalline) considered.

## Sources referenced

- IUPAC (2021). *Atomic weights of the elements 2021*.
- Haynes, W.M. (ed.). *CRC Handbook of Chemistry and Physics*, 97th ed., CRC Press.
- NIST Chemistry WebBook, NIST Standard Reference Database Number 69.
- Mills, K.C. (2002). *Recommended Values of Thermophysical Properties for Selected Commercial Alloys*. Woodhead Publishing.
- Mills, K.C., Keene, B.J. (1987). *Physicochemical properties of liquid iron/silicon alloys*. International Materials Reviews.
- Yamamoto, K., et al. (1991). *Thermal conductivity of molten silicon*. Journal of Crystal Growth.
- Boivineau, M., et al. (2006). *Thermophysical Properties of Solid and Liquid Silicon at High Temperatures*. International Journal of Thermophysics.
- Shvarev, K.M., et al. (1978). *Emissivity of liquid silicon*.
- MatWeb (online materials database) for engineering constants.
- Engineering Toolbox (online) for radiation properties.
