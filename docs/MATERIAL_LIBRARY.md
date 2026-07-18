# Material preset library

Cloth NeXt includes 37 read-only starting points grouped into seven hover
menus. Seven **Essentials** preserve the existing PPF presets. The other 30
presets are derived from laboratory measurements in the MIT Fabric Properties
Dataset published with Bouman, Xiao, Battaglia, and Freeman, *Estimating the
Material Properties of Fabric from Video* (ICCV 2013).

## What is measured

For each research preset, the source ID and the arithmetic mean of its two
orthogonal laboratory measurements are stored alongside the solver values:

- area weight in `oz/yd²`;
- bending stiffness in `lbf·in²`.

The solver's `surface_weight` is the direct unit conversion

```text
kg/m² = oz/yd² × 0.033905747
```

PPF uses a density-normalized shell bend parameter rather than the physical
laboratory unit. A single global calibration is therefore applied to every
research sample:

```text
PPF bend = mean bending [lbf·in²] × 14.3491 / area weight [kg/m²]
```

This preserves the measured bend-to-mass ordering while producing useful PPF
starting values. It is a documented solver calibration, not a claim that the
PPF number is an SI bending modulus.

## What is calibrated

The MIT dataset does not provide PPF-compatible stretch resistance, Poisson
response, contact friction, or stretch limits. Those fields use conservative
category calibrations. This limitation matters: real textiles are generally
nonlinear and anisotropic, while the current PPF fabric controls exposed by
Cloth NeXt are compact isotropic artist controls. The research presets should
therefore be treated as unusually well-grounded starting points, not digital
certificates for a specific commercial textile.

## Categories

| Menu | Presets |
|---|---:|
| Essentials | 7 |
| Light & Flowing | 5 |
| Natural Wovens | 5 |
| Knits & Stretch | 4 |
| Pile & Soft | 5 |
| Heavy & Structured | 7 |
| Technical & Coated | 4 |

Hover a category in the Material Preset menu to open its fabric submenu. A
check mark identifies the currently selected preset. Editing any controlled
material field still switches the selection to **Custom** without resetting
the edited values.

## Scientific sources

- Katherine L. Bouman, Bei Xiao, Peter Battaglia, and William T. Freeman,
  “Estimating the Material Properties of Fabric from Video,” ICCV 2013.
  [Project and dataset](https://people.csail.mit.edu/klbouman/materialproperties/)
  · [paper](https://people.csail.mit.edu/klbouman/pw/papers_and_presentations/iccv2013_bouman.pdf)
- Huamin Wang, James F. O'Brien, and Ravi Ramamoorthi, “Data-Driven Elastic
  Models for Cloth: Modeling and Measurement,” ACM Transactions on Graphics
  30(4), 2011. [Paper and data](https://wanghmin.github.io/publication/wang-2011-dde/)

Only derived numeric measurements and citations are bundled. Source photos,
videos, and spreadsheets are not redistributed.
