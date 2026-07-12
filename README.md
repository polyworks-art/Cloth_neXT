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

## Documentation

- [Release policy](docs/RELEASE_POLICY.md)
- [Update channels](docs/UPDATE_CHANNELS.md)
- [Solver installation](docs/SOLVER_INSTALLATION.md)
- [Solver distribution](docs/SOLVER_DISTRIBUTION.md)
- [Solver compatibility manifest](docs/SOLVER_COMPATIBILITY_MANIFEST.md)
