# Cloth NeXt 2.9.13

Cancel now stops Bake preparation during validation, geometry export, animated capture, Companion waiting and the handoff to the solver. Synchronous preparation reads Cancel directly from authenticated IPC. Owned buffers, temporary evaluations, capture state and the Bake reservation are cleaned up. A native Blender evaluation already running finishes before the next cancellation checkpoint.

Static Collider geometry has its own verified cache. Changing Softbody Stretch Resistance can reuse it even when the complete Scene cache is unavailable. Collider geometry and transform changes still invalidate it, and the Bake window distinguishes reuse from export. Modifiers after Cloth NeXt no longer participate in solver-input dependency identity.

Softbody Material adds Appear Solid: high stiffness, preserved rest volume, damping and disabled permanent deformation give a rigid-looking deformable solid. Disabling the checkbox restores the previous material values, including after saving and reopening Blender. Mass and contact settings are retained.

Material Zone overlays are stronger and drawn slightly above the surface. Picking and occlusion retain the exact mesh geometry.

Validated with the regression suite and real Blender tests for authenticated preparation cancellation, preset save/load, independent collider reuse and geometry/transform invalidation. Update through Blender's native extension manager. The external solver is not modified or bundled.
