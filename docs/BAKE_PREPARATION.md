# Bake preparation and Softbody shortcuts

Cancel applies during scene validation, geometry extraction, animated Pin/Collider/Force capture, Companion startup and the handoff to the solver. Synchronous preparation checks the authenticated Companion request queue directly between objects and stages, so cancellation does not depend on Blender timer polling or an existing solver worker. Cancellation restores the capture frame and modifier visibility, removes owned temporary evaluations and collider buffers, releases the Bake reservation and does not start the solver. A native Blender evaluation already running completes before the next cancellation checkpoint.

Static Collider geometry is stored as an independent hash-verified artifact in the deformable Bake cache. Changing a Softbody material such as Stretch Resistance does not invalidate it. Collider mesh, transform, upstream modifier dependencies, frame range or unsafe dependencies still cause a fresh export. The Bake window distinguishes reusing verified Collider geometry from exporting it. Geometry signatures computed by scene validation are reused rather than scanned again for the cache key. Modifiers after the Cloth NeXt boundary do not participate in solver input dependency identity.

## Appear Solid

Softbody Material provides an **Appear Solid** checkbox. It keeps the ARAP Softbody solver and tetrahedralization, while applying Stretch Resistance 10,000,000, Sideways Response 0.45, Volume Scale 1, Shape Damping 0.05, and disabled permanent deformation (creep rate 0). These are finite solver material values intended to give a stiff, shape-preserving appearance; this remains a deformable solid rather than a mathematical rigid body. Weight density, friction, contact settings and tetrahedralizer are preserved.

The affected controls are disabled while the shortcut is enabled. Prior material values and Shape Damping are saved in the .blend file and restored when the checkbox is disabled, including after saving and reopening the file. High stiffness can increase solver work; no automatic global solver-quality changes are made.
