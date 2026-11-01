# SPDX-License-Identifier: GPL-3.0-or-later
"""PATTERN NeXt geometry core (pure Python + numpy, no ``bpy``).

The Blender integration lives in ``cloth_next.blender.pattern_next``; this
package holds everything that can be tested without Blender:

* :mod:`.shapes` - parametric pattern contours (straight and Bezier
  segments, rectangle and ellipse primitives) and the 2D canvas transform;
* :mod:`.curves` - adaptive Bezier flattening and arc-length resampling that
  keeps a stable ``(segment uid, fraction)`` source for every boundary vertex;
* :mod:`.validation` - open/self-intersecting/degenerate contour detection;
* :mod:`.mesher` - constrained interior meshing driven by Particle Distance;
* :mod:`.seams` - seam intervals on stable point ids and their derived
  mesh-vertex correspondence;
* :mod:`.snapping` - grid/vertex/edge/angle snapping in canvas space.

Coordinates are metres. The 2D canvas maps to Blender's front (XZ) plane:
canvas ``(u, v)`` is world ``(u, 0, v)``; see ``docs/PATTERN_NEXT.md``.
"""

SCHEMA_VERSION = 1
