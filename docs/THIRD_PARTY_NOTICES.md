# Third-party notices

## PPF Contact Solver fabric preset data

`cloth_next/materials/ppf_fabric_presets.toml` contains numeric material
preset values (Silk, Flag, Cotton, Wool, Denim, Leather) derived from the
PPF Contact Solver project:

- Source project: [st-tech/ppf-contact-solver](https://github.com/st-tech/ppf-contact-solver)
- Pinned commit: `7193f158e3843597070f66cb29af19efd9bdcff7`
- Source path: `blender_addon/presets/materials.toml`
- Source license: Apache License 2.0
  (<https://www.apache.org/licenses/LICENSE-2.0>)
- Copyright: © ZOZO, Inc. (ppf-contact-solver contributors)

Only the calibrated data values were copied; no upstream Python or UI code
is included. The values follow the upstream convention: shell Young's
modulus entries are already density-normalized PPF wire values, shell
density is an area density (kg/m²) shipped at a uniform 1.0, and strain
limits are percentages converted to wire fractions at encode time.

Cloth NeXt is an independent project licensed under GPL-3.0-or-later. The
PPF Contact Solver itself is separate software that users install from its
official source; Cloth NeXt never bundles, mirrors, or redistributes it.

## Croissant icon

Croissant icon by [Noun Project](https://thenounproject.com/browse/icons/term/croissant/).
