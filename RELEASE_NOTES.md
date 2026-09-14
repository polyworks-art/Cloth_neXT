# Cloth NeXt 2.4.9 Dev

Cloth NeXt 2.4.9 fixes an occasional one-frame jump during Live View. The
Timeline now advances only after every growing PC2 has a complete frame that
the Bake worker has flushed. While the cache is growing, Blender holds the
last confirmed frame without interpolating toward an unwritten next sample.
Finalized playback continues to use its normal interpolation.

The compact New Look toolbar also gives Set Dir and normal Quality a slightly
lighter gray and lowers the BAKE/CANCEL label a little. F6 now slides the
toolbar below the viewport edge when hiding it and brings it back smoothly.

Cloth Shrink supports negative percentages for an expanded rest shape: +10%
sets the rest scale to 90%, while -10% sets it to 110%. Shrink and an enabled
non-zero Stretch Limit cannot be combined; Cloth NeXt reports this before
Bake. The external PPF solver is not included or modified.
