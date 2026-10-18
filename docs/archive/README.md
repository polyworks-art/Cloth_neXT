# Historical documentation archive

These files were moved out of active documentation because they are early implementation plans, fixed-version audits, or obsolete prototype reports. **Archived does not mean invalid as historical evidence.** Follow [documentation index](../README.md) and [GAIA acceptance criteria](../gaia/ACCEPTANCE_CRITERIA.md) for current goals.

## Legacy architecture, plans, and misc. investigations

- [Early architecture audit](legacy/ARCHITECTURE.md) — July repository bootstrap inventory, not current architecture.
- [Original implementation plan](legacy/IMPLEMENTATION_PLAN.md) — completed early phases, not the current roadmap.
- [Channel migration implementation report](legacy/CHANNEL_MIGRATION_IMPLEMENTATION.md) — retrospective; operative rules remain in the release policy.
- [Face-friction crash investigation](legacy/FACE_FRICTION_CRASH_INVESTIGATION.md) — past diagnostic evidence.
- [PDRD mixed-scene bug note](legacy/PRBD_Bug.md) — older version-specific warning, preserved pending fresh compatibility audit.

## GAIA 0.23 and initial Water Flow

- [0.23 investigation](legacy/GAIA_023_INVESTIGATION.md)
- [0.23 lifecycle findings](legacy/GAIA_023_LIFECYCLE_RESULTS.md)
- [0.23 Windows lifecycle](legacy/GAIA_023_WINDOWS_LIFECYCLE.md)
- [Original Water Flow implementation](legacy/GAIA_WATER_FLOW.md) — describes the previous 0.23 air-velocity model, **not** current hydrodynamic pressure.
- [Water Flow coordinate audit](legacy/GAIA_WATER_FLOW_COORDINATE_AUDIT.md)
- [Water Flow resolution audit](legacy/GAIA_WATER_FLOW_RESOLUTION_AUDIT.md)
- [Water Flow streaming audit](legacy/GAIA_WATER_FLOW_STREAMING_AUDIT.md)

## Why some old reports were not moved

Early Water Push and response reports remain in docs/ while report-generating tools still address those exact paths. Other validation evidence and releases remain discoverable at their original paths to preserve tool and external links.

No source binaries, tests, validation JSON, release notes, active policy or compatibility manifests were removed.
