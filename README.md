# Cloth NeXt

Professional Blender workflow that replaces the built-in cloth simulation with
the external GPU-based **PPF Contact Solver**.

> The PPF Contact Solver is external software developed and distributed by
> ST Tech / ZOZO (`st-tech/ppf-contact-solver`). Cloth NeXt never bundles,
> mirrors, or redistributes it. The add-on preferences install or select it
> separately, after explicit confirmation, directly from its official source.

## Installation and updates

Cloth NeXt installs and updates through Blender's extension system using the
remote repositories documented in [docs/UPDATE_CHANNELS.md](docs/UPDATE_CHANNELS.md)
(stable and beta channels). The solver installs separately; see
[docs/SOLVER_INSTALLATION.md](docs/SOLVER_INSTALLATION.md).

## Release policy

All versioning, tagging, packaging, publishing, and solver-compatibility rules
are governed by the mandatory
[Cloth NeXt Release Policy](docs/RELEASE_POLICY.md). Read it before changing
versions, releases, update channels, or solver metadata.

## License

Cloth NeXt — Copyright (C) 2026 Tim Christmann and Cloth NeXt contributors.

This add-on is free software, licensed under the
**GNU General Public License, version 3 or (at your option) any later version**
(`GPL-3.0-or-later`, matching `blender_manifest.toml`). See [LICENSE](LICENSE)
for the full text. It is distributed WITHOUT ANY WARRANTY; without even the
implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

The external PPF Contact Solver is **not** covered by this license: it is
separate software by ST Tech / ZOZO, licensed under Apache License 2.0, and is
neither included in nor distributed with Cloth NeXt.

## Documentation

- [Release policy](docs/RELEASE_POLICY.md)
- [Update channels](docs/UPDATE_CHANNELS.md)
- [Solver installation](docs/SOLVER_INSTALLATION.md)
- [Solver distribution](docs/SOLVER_DISTRIBUTION.md)
- [Solver compatibility manifest](docs/SOLVER_COMPATIBILITY_MANIFEST.md)
