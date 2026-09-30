# Cloth NeXt 2.9.6

Cloth NeXt 2.9.6 fixes a Bake-start failure caused by empty Cloth NeXt simulation modifiers. Blender can save their unset cache field as a relative directory; this is now recognized as an unbaked pass-through boundary rather than an old cache that needs authentication. Recorded playback caches still require ownership checks, and unrelated files remain protected.

The Bake Companion now wraps error summaries, recommendations, and metadata to the actual available panel width. The details window adjusts its height to the wrapped content, preventing long summaries from running past the window edge.

The affected saved scene passed cache preparation and simulated through frame 40. Regression tests cover empty boundaries, rejection of unauthenticated recorded caches, and diagnostic wrapping; the error layout was also inspected in the real Companion window. The Sewing mapping and solver-error fixes from 2.9.5 remain included.

The external PPF Contact Solver is not modified or bundled. Update through Blender's native extension manager and rebake existing caches to use the fix.
