# Solver backend architecture

Cloth NeXt owns the artist workflow and canonical scene/material settings. PPF
and Newton are interchangeable execution engines behind one persistent scene
selection (`PPF`, default, or `NEWTON`).

The pure `cloth_next.simulation.backends` module owns backend identity,
capabilities, and formal material mapping classifications: `EXACT`,
`APPROXIMATE`, `UNSUPPORTED`, and `SOLVER_SPECIFIC`. The Blender-facing
`cloth_next.blender.solver_backends` module is the single Bake/cancel routing
boundary and performs cheap selected-backend capability checks. Protocol and
process details stay inside the existing PPF and Newton packages.

PPF remains unchanged as the mature contact/recovery backend. Newton reuses the
external managed environment, bounded worker protocol, atomic results, PC2
writer, common Bake controller, Bake window readiness handshake, modal lock,
and cache attachment path. Newton's worker additionally owns fTetWild volume
meshing for Soft Bodies and rigid-body surface reconstruction. There is no
Newton-specific artist workflow.

Backend selection is independent of installation state. Old scenes and unknown
values resolve deterministically to PPF. A legacy Newton-selection property is
read only for migration; new UI and routing use the canonical solver property.

Capability flags describe only features verified in Cloth NeXt, not everything
an external engine advertises. Unsupported roles or canonical features produce
actionable preflight errors and retain their values for a future backend switch.
