# Cloth NeXt 2.9.28

The Release list in the Solver Engine section shows correct names again. In 2.9.27 its entries could appear garbled or empty because Blender keeps only pointers to the item texts of a dynamic menu; the list now stays referenced. Selection and the solver registry were never affected.

Pinning texts now describe what the solver does: pins use the Soft constraint by default, which may yield to cloth forces and contact, while Hard pins hold exactly. Verified with AETHER and GAIA. The AETHER/GAIA engines, the GAIA installation in Preferences and the Account & License card from 2.9.27 are unchanged. Neither solver is bundled.
