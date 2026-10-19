# Cloth NeXt 2.9.33

Sewing now selects edge segments directly and stays active after each committed seam. Free Sewing follows explicitly selected connected edges. Both sides may belong to the same cloth object. Edit Sewing selects existing seams and supports reversal, enable/disable, deletion and boundary-side reassignment; Strength always edits the definition exported to the solver.

A separate overlay renderer adds recognizable seam colors, fine arc-length correspondence threads, directional notches and larger endpoint circles. Thread density and opacity, sewing lines, threads, notches, length differences and inactive seams have independent controls. Length mismatch warnings do not reject intentional gathers. Unequal vertex densities receive additional reverse dynamic stitches.

Includes Claude's complete changes through 2.9.32: frozen-contract PolyWorks account integration, explicit AETHER/GAIA engine selection, verified GAIA installation and Dev/Public build selection, Windows long-path installation, editable Initial Bend Shape and corrected Surface Weight guidance. Existing solver choices are preserved. No external solver runtime is bundled.

Development limitations: Multi Sewing supports a shared path (1:N or M:1) or equal-size ordered groups. General unequal M:N groups, dense-production picking performance and the full garment/character GUI acceptance matrix are not yet certified. The separately gated GAIA owner evaluation retains its existing limitations and is not enabled by this release.
