# Adaptive GAIA Flow grids — 2026-10-14

Implemented locally after 2.9.25. No version change or publication has been performed.

The reported NEON failure used Auto, producing a 125 x 250 x 125 coupling grid. Its estimated native encoding window was 216.21 MiB, exceeding the existing 128-MiB budget only after lengthy preparation.

Auto, Medium, High, Extreme and Custom now represent requested coupling detail. Before reading particle data, the planner searches for the largest isotropic grid that fits the reconstruction, frame encoding and transport budgets. It can adapt below the previous requested minimum of 100 when a long sequence requires that. FLIP simulation resolution, source particles, cloth geometry and coupling parameters remain unchanged. The derived coupling grid can have lower spatial detail; it is not a promise of identical coupling quality at arbitrary requested resolutions.

Native planning accounts for 128-MiB active encoding, 32-MiB packed frames and the unchanged 1-GiB complete transport limit. Sequence length and distinct per-object influences are included. Identical fields shared by disjoint Cloth groups count once; differently weighted fields count separately. The transport estimate assumes every brick is occupied and reserves 64 MiB for the base scene plus 4 KiB per exported field/frame for metadata. This deliberately favors a conservative bound over a late failed export.

Existing runtime guards remain in place. If even the smallest grid cannot fit an extraordinarily long sequence, planning fails immediately with a request to split the frame range. Invalid source data and independent solver failures remain possible. This does not remove finite resource limits.

The cache metadata records requested and effective resolution, the selected dimensions and the budget recipe. The panel shows the prepared grid and any adaptation. Budget changes that leave the physical grid identical do not invalidate a verified cache merely because the number of targets changes. Changes to the actual grid regenerate the derived cache atomically; source FLIP data are untouched.

## Validation

- 20 budget tests cover Auto, Extreme and Custom requests up to 4096, 1/250/1000-frame sequences, multiple distinct fields, maximal fitting grids, legacy velocity windows and impossible ranges without allocating field arrays.
- Real Blender 5.2.2 NEON preparation with Custom 1000 and three frames passed. The effective grid was 92 x 184 x 92; estimated encoding memory was 126.19 MiB. All particles were streamed from the real FLIP cache. One shared preparation worker captured three frames for the existing three Cloth objects.
- Real native file encoding of those frames passed: 27,172,454-byte PARAM, largest packed frame 9,057,216 bytes. Warm preparation took 1.12 seconds with no particle captures and unchanged cache bytes. This encoding test alone is not a solver simulation.
- Complete clean-source Windows regression: 2,289 passed, 7 skipped, 17 deselected in 147.73 seconds. The Git-dependent executable-tracking check was run separately in the actual repository and passed. Generated release executables are excluded from the clean source snapshot.
- Real Blender provider menu/RNA smoke testing passed for legacy and native Flow UI, registration cleanup, disabled providers and exact tooltips.
- A real GAIA 0.26 native Bake of the existing three NEON Cloth meshes (23,356 vertices) passed for three frames with requested Custom 1000 and Coupling Strength 0.2. The resulting native PARAM was 27,199,294 bytes; native simulation and import took 7.05 seconds. All coordinates in the three resulting PC2 files were independently checked as finite. Temporary native payloads were cleaned up, and source `.blend` plus the ten checked FLIP cache files were unchanged. This was an actual solver run, not replacement host forces.
- A complete 250-frame adaptive NEON Bake has not been executed. The full-sequence payload bounds are conservatively tested by the planner; actual motion over 250 frames remains untested with this new effective grid.

## Provider dropdown

Only FLIP Fluids is supported and selectable. Mantaflow, Houdini FX and LiquiGen are shown disabled. Calling the provider operator directly with an unsupported identifier is rejected.

Tooltips: Mantaflow — `Blenders Fluidsolver`; Houdini FX — `You should know how to export particle Data`; LiquiGen — `Export liquid particles as Alembic with Export velocity enabled.` LiquiGen's particle Alembic and per-particle velocity export are documented by [JangaFX](https://docs.jangafx.com/liquigen/pages/references/node_list.html#export-particles); these exports are not yet implemented as a Cloth NeXt importer.
