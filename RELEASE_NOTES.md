# Cloth NeXt 2.9.15

Animated Collider capture now defaults to one sample per Blender frame. This matches the whole-frame poses consumed by the current solver. Existing explicitly saved sample counts remain unchanged; set these to 1 to reduce capture work.

Official solver preparation retains dense Collider animation tables as compact read-only numeric views instead of expanding them into Python lists and individual numbers, then re-encoding them through slow scalar loops. Whole-frame selection also avoids a full animation copy. The wire format and animation timing remain unchanged.

A bounded local decode/re-encode benchmark improved from 0.90 seconds to 0.027 seconds with byte-identical output. This measures that stage, not an entire Bake. Regression tests cover the codec, solver bridge, animation timeline and Bake lifecycle. Update through Blender's native extension manager. The external solver is not modified or bundled.
