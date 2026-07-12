<p align="center">
  <img
    src="assets/Cloth_neXt_icon.svg"
    alt="Cloth NeXt Logo"
    width="220"
  />
</p>

<h1 align="center">Cloth NeXt</h1>

<p align="center">
  Professional Blender workflow powered by the external GPU-based
  <strong>PPF Contact Solver</strong>.
</p>

---

Cloth NeXt replaces Blender's built-in cloth simulation workflow with the
external GPU-based **PPF Contact Solver**.

> [!IMPORTANT]
> The PPF Contact Solver is external software developed and distributed by
> ST Tech / ZOZO (`st-tech/ppf-contact-solver`). Cloth NeXt never bundles,
> mirrors, or redistributes it. The add-on preferences install or select it
> separately, after explicit confirmation, directly from its official source.

## Installation and updates

Cloth NeXt installs and updates through Blender's extension system using the
remote repositories documented in
[docs/UPDATE_CHANNELS.md](docs/UPDATE_CHANNELS.md), including stable and beta
channels.

The solver is installed separately. See
[docs/SOLVER_INSTALLATION.md](docs/SOLVER_INSTALLATION.md).

## Release policy

All versioning, tagging, packaging, publishing, and solver compatibility rules
are governed by the mandatory
[Cloth NeXt Release Policy](docs/RELEASE_POLICY.md).

Read it before changing versions, releases, update channels, or solver metadata.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [PPF protocol](docs/PPF_PROTOCOL.md)
- [Limitations](docs/LIMITATIONS.md)
- [Release policy](docs/RELEASE_POLICY.md)
- [Update channels](docs/UPDATE_CHANNELS.md)
- [Solver installation](docs/SOLVER_INSTALLATION.md)
- [Solver distribution](docs/SOLVER_DISTRIBUTION.md)
- [Solver compatibility manifest](docs/SOLVER_COMPATIBILITY_MANIFEST.md)

## License

Cloth NeXt — Copyright © 2026 Tim Christmann and Cloth NeXt contributors.

This add-on is free software licensed under the
**GNU General Public License, version 3 or, at your option, any later version**
(`GPL-3.0-or-later`, matching `blender_manifest.toml`).

See [LICENSE](LICENSE) for the full license text.

Cloth NeXt is distributed **without any warranty**, including the implied
warranties of merchantability or fitness for a particular purpose.

The external PPF Contact Solver is **not** covered by the Cloth NeXt license.
It is separate software developed by ST Tech / ZOZO, licensed under the
Apache License 2.0, and is neither included in nor distributed with Cloth NeXt.