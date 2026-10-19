# Cloth NeXt 2.9.32

Use Initial Bend Shape can be changed again. It sat inside the Bend Plasticity section and was greyed out unless bend plasticity was enabled, although the solver always uses it; it now sits next to Bend Resistance.

The Surface Weight tooltip now says what the solver does: stretch, bend and contact stiffness scale with the weight, so a higher Surface Weight on its own barely changes how cloth falls. It matters against sewing, wind, water and other objects. If small cloth looks stiff, check Stretch Protection first: a low Maximum Stretch can hold it like a board.

Everything from 2.9.31 is unchanged, including GAIA builds offered as Dev or Public release. Neither solver is bundled.
