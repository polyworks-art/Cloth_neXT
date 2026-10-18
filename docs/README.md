# Cloth NeXt documentation index

Start here instead of treating historical experiments as current product behavior.

## Current project contracts

- [Release policy](RELEASE_POLICY.md) — mandatory versioning, solver distribution, channels, packaging and publication rules.
- [GAIA 3.0 acceptance criteria](gaia/ACCEPTANCE_CRITERIA.md) — proposed, testable one-way FLIP → Cloth hydrodynamics acceptance and release gates; **not yet certified**.
- [PPF protocol](PPF_PROTOCOL.md) and [solver parameter mapping](PPF_PARAMETER_MAPPING.md).
- [Solver compatibility](SOLVER_COMPATIBILITY_MANIFEST.md), [distribution](SOLVER_DISTRIBUTION.md), [installation](SOLVER_INSTALLATION.md), [backend certification](SOLVER_BACKEND_CERTIFICATION.md).
- [Extension packaging](EXTENSION_PACKAGING.md), [update channels](UPDATE_CHANNELS.md), [security](SECURITY.md), [third-party notices](THIRD_PARTY_NOTICES.md).

## Current workflows and developer references

- [Cache format](CACHE_FORMAT.md), [bake preparation](BAKE_PREPARATION.md), [recovery and checkpoints](RECOVERY_CHECKPOINT_DIAGNOSTICS.md), [process lifecycle](PROCESS_LIFECYCLE.md).
- [Material library](MATERIAL_LIBRARY.md), [material zones](MATERIAL_ZONES.md), [object attachments](OBJECT_ATTACHMENTS.md).
- [UI architecture](UI_ARCHITECTURE.md), [dependencies](DEPENDENCIES.md), [error codes](ERROR_CODES.md), [limitations](LIMITATIONS.md).
- [Quick Assign](QUICK_ASSIGN.md) and [local wind fields](LOCAL_WIND_FIELDS.md).

## GAIA engineering evidence and work in progress

These are **dated reports**, not automatic release approvals. Read the individual stated gates and test limitations.

- [GAIA 0.26 long-sequence integration report](GAIA026_INTEGRATION_REPORT.md)
- [GAIA 0.26 production-scale validation](GAIA026_PRODUCTION_SCALE_REPORT.md)
- [GAIA large-particle simulations](GAIA_LARGE_SIMULATIONS.md)
- [GAIA preparation performance](GAIA_PREPARATION_PERFORMANCE.md)
- [GAIA upstream feature backlog](GAIA026_UPSTREAM_BACKLOG.md)
- [GAIA reactive-materials capability audit](GAIA_REACTIVE_MATERIALS_CAPABILITY_AUDIT.md)
- [GAIA ripping capability audit](GAIA_RIPPING_CAPABILITY_AUDIT.md)
- [Shared water-flow settings](GAIA_SHARED_FLOW_SETTINGS.md)
- [Water-push prototype, cadence and spatial audits](GAIA_WATER_PUSH_PROTOTYPE_AUDIT.md) — retained at their original paths because the report-generating tools reference them.
- [Water Flow response audit](GAIA_WATER_FLOW_RESPONSE_AUDIT.md) — also retained in place because of report tooling.
- [GAIA 0.26 validation results](validation/gaia026/TEST_RESULTS.md)

The experimental Hydrodynamic Pressure native CPU work from 15 Oct 2026 is not certified as the official 0.26 FluidVolume contract. Its evidence must be added and verified separately before ticking the new criteria.

## Historical reports

See [archive index](archive/README.md) for superseded architecture audits, old implementation plans, GAIA 0.23 lifecycle investigations, initial water-flow and coordinate/resolution/streaming reports. They remain available as historical evidence, not current instructions.

Historical release notes under [releases/](releases/) and the repository root CHANGELOG/RELEASE_NOTES remain in their normal locations; deleting them would lose provenance.

## Documentation maintenance rules

1. Put normative feature acceptance criteria in **docs/gaia/**; keep implementation history and validation evidence separate.
2. Archive superseded studies under **docs/archive/** with links and context instead of silently erasing experiments.
3. Keep documents referenced by scripts, CI, release workflows, legal notices and historical release machinery at stable paths unless all consumers are updated and verified.
4. Archive movement alone does not change add-on behavior, solver capabilities, releases, versions, or the current development environment.
