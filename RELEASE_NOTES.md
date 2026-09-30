# Cloth NeXt 2.9.4

Cloth NeXt 2.9.4 corrects Sewing Strength for the path-based workflow. Intra-object seams now use the Strength of their own seam definition, just like cross-object seams, instead of silently inheriting the old object-wide loose-edge stiffness.

New seams default to Strength 100. The artist control is calibrated to the solver's raw force units: 100 produces a firm seam using a force factor of 50,000. Strength 0 disables the seam's force. Existing authored Strength values are preserved; set existing seams to 100 and rebake to use the firmer setting.

Explicit intra- and cross-object seams share the same dynamic stitch representation, including a bake with only one Cloth object. The existing wireframe overlay, path preview, compact rows, directional flipping, and legacy loose-edge compatibility remain available.

Real-solver regression tests verify rapid closure and free fall for intra, cross, and combined seams. The separately reported scene-specific floating behavior has not been reproduced, so this release does not claim a verified fix for that issue.

The external PPF Contact Solver is not bundled.
