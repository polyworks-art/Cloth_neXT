# Cloth NeXt 2.9.2

Cloth NeXt 2.9.2 replaces the old loose-edge Sewing controls with a direct viewport workflow. Choose two endpoints for Side A and two for Side B; Cloth NeXt follows the shortest original-mesh path between each pair, preferring boundary edges where possible, and previews the resulting stitch direction before it is committed.

Sewing works within one Cloth object or between two Cloth objects. Persistent definitions include compact expandable rows, independent overlay visibility, directional flipping, topology safety, and the solver-backed strength appropriate to the selected relationship. The global Show Sewing switch hides the display without changing simulation data, while playback and active bakes suppress it temporarily.

Legacy loose-edge stitches remain readable and export through the established backend. Explicit stitches are deduplicated against them so existing files continue to simulate without duplicate constraints.

The external PPF Contact Solver is not bundled.
