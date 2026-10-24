# Cloth NeXt 2.9.40

Cloud Bakes: choose Cloud as Bake Location to simulate on PolyWorks GPUs. Review & Send shows the binding cost limit before anything is sent, the upload resumes after interruptions, and the result is downloaded and attached automatically. Cloud stays locked until the PolyWorks Cloud service reports that it is ready; a Cloud Bake never falls back to this computer.

The Bake window follows a Cloud job with live server data: upload, server state, queue position, frames, ETA, cost limit, final cost and download. Closing it never stops the job.

Water Flow can only be switched on with GAIA access, and the legacy coupling controls are gone. The Sewing and Attachment toolbars match Blender's navigation buttons.

## 2.9.39

Wet Fabric for Hydrodynamic Pressure (needs GAIA 0.26.0-dev.20261022 or newer): fabric floats under water, soaks up water over time, hangs heavier once it leaves the water and dries again. Wet Cling makes soaked fabric grip the body only where it touches it, and Wet Damping stops soaked fabric from springing back and forth after it leaves the water. Pressure Strength and Tangential Drag let the flow push harder and pull fitted garments along.

Preparing a water container directly from FLIP particle files works again. Sewing picks seams through hair and props that are not Cloth NeXt objects.

Bakes in scenes with FLIP Fluids keep Blender responsive and show solver contacts, Newton steps and linear iterations again. The live preview no longer makes FLIP reload its display meshes for every streamed frame; the water is not updated live in the viewport during a Bake.

Animated Collider export is much faster in scenes with FLIP Fluids: preparation no longer makes FLIP reload its display meshes on every frame. A Bake started after a failed attempt no longer waits at "Running solver" forever when the Bake window was still open.

A Hydrodynamic Pressure Bake may now end on the last baked FLIP frame (for example FLIP and cloth both 1-250). The solver's final lookahead holds that frame instead of stopping with CNX-E174.

Bakes with Hydrodynamic Pressure no longer wait many minutes at "Preparing evaluated geometry". The cloth's extent is measured during the existing geometry pass; frames that pass did not visit are sampled at most 25 times with visible progress.

Sewing edges can now be picked from every side, including cloth with Solidify or deform modifiers, and hovering stays fast on dense garments. The Sewing and Object Attachment editors get a clickable icon toolbar below the viewport navigation buttons that follows the N panel; the icons are placeholders.

Hydrodynamic Pressure now works in any scene and for any number of frames. Enable it on the cloth's Fluid Interaction panel with a GAIA CUDA solver selected; the add-on checks the selected installation itself (hash list and native worker probe) and never switches solvers. The earlier owner evaluation package, frozen NEON scene and 130-159 frame limit are no longer needed.

FLIP surfaces are read straight from the FLIP cache. Small surface holes no longer stop the Bake. Preparation runs in parallel, needs about 15 MB per frame and is removed after each Bake.

Development limitations: pressure is one-way (water moves cloth), uses water density 1000 kg/m³ with Cn 1 and Ct 0, requires Blender Unit Scale 1, an unrotated FLIP domain and a shared FLIP cache for all wet cloth. Includes everything from 2.9.33.
