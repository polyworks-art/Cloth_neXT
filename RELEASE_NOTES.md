# Cloth NeXt 2.9.12

Material Zone selection now samples visible surfaces directly. A small brush can select large or partly visible faces even when their centers lie outside the circle, while hidden geometry remains protected.

Selection displays and picks the mesh immediately before the Cloth NeXt modifier. Downstream modifiers, including Solidify and Subdivision, are permitted and temporarily hidden during selection. Their original visibility is restored on Enter, Escape and cleanup. Selection prefers the invoking 3D Viewport.

Validated with 80 targeted regression tests and real Blender perspective, orthographic and X-Ray viewport tests, including downstream modifier restoration and small-brush hits away from face centers.

Update through Blender's native extension manager. The external solver is not modified or bundled.
