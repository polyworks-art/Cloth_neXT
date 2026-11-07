# Cloth NeXt 2.9.68

AETHER Live starts from the Fit Pose like a Bake, so clothes no longer start stuck inside the body, and fast grabbing no longer explodes (update AETHER in Preferences > Solver Engine).

## 2.9.67

AETHER: no frozen vertices anymore. Cloth that was held in place by its own folds is released and slides or falls instead of hanging in mid-air.

The AETHER and TETHYS engines are no longer inside the add-on download. Install or update them in Preferences > Add-ons > Cloth NeXt > Solver Engine (AETHER needs no account, TETHYS needs GAIA access); the selected engine never changes on its own.

## 2.9.66

AETHER Live: a grabbed vertex no longer freezes when you lift a seam end or a corner; it pushes the cloth in its way ahead of it.

## 2.9.65

AETHER sewing closes like Marvelous Designer (fast, no fight at the seam line, sewing fold angle), PATTERN NeXt no longer crashes next to Grab Cloth and refuses to sew an edge twice, pulled cloth slides along the skin instead of sticking to it, and Grab Cloth stays on for the whole Live Simulation.

## 2.9.64

AETHER now simulates Wind from Force Empties, Shrink Warp / Weft, Surface Offset, Permanent Stretch / Bends and Inflate Pressure, moves cloth out of the way of fast body motion before it gets caught, can start a bake from the pose a garment was made for (Start from Fit Pose), pulls cloth that starts inside the body out more reliably, Live Grab no longer leaves vertices stuck and has no stretch limit, and GAIA Water Flow and TETHYS settings can stay on when you switch to AETHER.

## 2.9.63

PATTERN NeXt reacts to clicks and keys again when another add-on runs a background modal operator.

## 2.9.62

The Wind preview stays out of the render thread during animation renders.

## 2.9.61

Bake All Wetness: every TETHYS cloth of the scene bakes at once, and a cancelled group continues where it stopped.

## 2.9.60

AETHER pulls cloth that starts inside the body out at the start of a bake, runs scenes with gravity-only Force objects, and add-on updates no longer get blocked by a running Companion or AETHER Engine.

## 2.9.59

TETHYS wetness bakes are about 4-5x faster, water details are as strong as before 2.9.52, and a cancelled wetness bake continues where it stopped.

## 2.9.58

AETHER bakes are exactly reproducible again and stay free of intersections in fast scenes.

## 2.9.57

Pulling cloth in the AETHER Live Simulation feels like Marvelous Designer: the grabbed point sticks to the mouse, cloth snaps back calmly when you let go and never hangs in the air, and static drapes run about twice as fast.

## 2.9.56

AETHER feels much closer to Marvelous Designer: cloth rests calmly, fitted garments keep their shape, Grab is always on in the Live Simulation and pulls the garment as a whole, and Stop & Keep lets you drape once and continue from there.

## 2.9.55

Older scenes run with the AETHER Engine again: a hidden Sewing switch from earlier versions no longer blocks Live Simulation and Bake on cloth that has nothing to sew.

## 2.9.54

Wetness now ends exactly at the water: TETHYS reads the liquid surface of your FLIP cache, so the water line on clothing no longer sits a few centimetres above the liquid.

## 2.9.53

PATTERN NeXt arrives: a new workspace where you draw garment patterns in a precise 2D editor and get a filled, triangulated cloth panel the moment you close the outline. Edit points and curves, sew edges together, and arrange the panels in 3D, all as regular Cloth NeXt cloth. This build also includes the experimental AETHER Engine for fast GPU cloth on Windows.

## 2.9.52

Wetness now has real-world proportions: drops, holes and rivulets keep their millimetre sizes on every garment and texture resolution, and the water stays as thin as real water on fabric. The TETHYS Engine also uses several CPU cores and bakes wetness about five times faster.

## 2.9.51

GAIA Water Flow takes more water: a baked Mantaflow liquid straight from Blender, or liquid particles imported from Houdini or LiquiGen. Hydrodynamic Pressure works with all of them. Bake Wetness now shows its progress in the Bake window, and on Windows the Cloth NeXt Companion lives quietly in the tray and keeps your licence current.

## 2.9.50

The water panels are tidier. TETHYS Wetness now has its own panel with its own logo next to GAIA Water Flow: choose the fabric, bake, and set the wet look up front, with everything technical folded away. GAIA Water Flow starts with the source, the domain and the fluid cache status, and Wet Fabric Physics now says what it does.

## 2.9.49

TETHYS Wetness arrives under GAIA: cloth now gets wet from a GAIA water simulation. Fabrics soak, darken, dry and shed water according to their material, and every Material Zone behaves on its own. On waterproof fabric water beads into drops of every size, running drops leave trails, rivulets form on slopes, and a water sheet pulled out of water tears into holes before it breaks into drops. Everything plays as image sequences and a water normal map in your materials, without extra geometry.

## 2.9.48

The Cloud Jobs list cleans itself up: jobs that no longer exist on the server and discarded uploads disappear, and a Clean Up button does it on request. Downloaded results stay. Uploads wait politely when the Cloud asks them to slow down.

## 2.9.47

Cloud uploads are about three times faster: up to eight files travel at once over kept-alive connections. When the service refuses large upload blocks, the upload continues with smaller blocks instead of pausing.

## 2.9.46

A Cloud Bake that failed on the server side without cost can be retried from the Cloud Jobs list. The uploaded scene is reused, and Review & Send asks you to confirm the new cost limit before the retry starts.

## 2.9.45

An unchanged FLIP cache is no longer prepared again for every Bake, also after a Blender restart: the Water Flow cache and the Hydrodynamic Pressure surfaces are reused until a FLIP file, the frame range or the Fluid Cache settings change. The Bake window keeps error text steady while it shows a failed Cloud job.

## 2.9.44

Cloud Bakes are confirmed in the Bake window: it shows the GPU, the credit ceiling and the maximum runtime of the quote, and Send starts the job. A Cloud Bake with Water Flow says clearly that Water Flow is not offered in the Cloud yet; nothing is sent and no credits are used.

## 2.9.43

Bake · Cloud is clickable again: with Cloud as Bake Location the Bake button is enabled as soon as the PolyWorks Cloud is ready. The Bake button in the floating viewport bar now shows only its icon: croissant for This Computer, cloud for Cloud.

## 2.9.42

Cloud Bakes now appear on your Cloud Bakes dashboard with their name, for example the object and its frame range, instead of a job number.

## 2.9.41

Cloud Bakes are live. Choose Cloud as Bake Location: Review & Send shows the GPU the server selected, how long one credit lasts and the maximum runtime next to the credit ceiling, and nothing is sent before you confirm. The Bake window follows the job with server data and shows the settled cost once billing is final; the result is downloaded and attached automatically.

The add-on checks your PolyWorks account and the server's size limits before asking for a quote, and the Cloud Jobs list links to your Cloud Bakes dashboard.

## 2.9.40

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
