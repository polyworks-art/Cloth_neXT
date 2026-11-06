# Cloth NeXt 3.0 — Release Notes (Entwurf)

> **Hinweis für Tim (nicht Teil der Notes):** Entwurf aus den Changelog-Einträgen Dev 2.9.55 bis 2.9.64,
> nach Themen statt nach Versionen sortiert, interne Testszenen-Namen und Messwerte weggelassen.
> Stand der Quellen: 2.9.55–2.9.62 sind im Dev-Kanal veröffentlicht; **2.9.63 liegt nur auf `main`,
> 2.9.64 nur auf Branch `claude/project-thread-mhu45t` — beide noch nicht veröffentlicht.**
> Für die Stable-Notes 3.0 fehlen noch die großen Themen vor 2.9.55 (PATTERN NeXt 2.9.53, TETHYS Wetness
> 2.9.49–2.9.54, Cloud Bakes 2.9.40–2.9.48, GAIA Water Flow mit Mantaflow/Houdini/LiquiGen 2.9.51);
> sie sind am Ende als Gerüst angedeutet. Englisch, weil Changelog und Superhive-Seite englisch sind.

---

## AETHER Engine

**Live Simulation that feels like a pattern-making tool**
- Grab Cloth is always on: drag the cloth with the left mouse button, clicks elsewhere work as usual. The grabbed point sticks to the mouse and the cloth follows as far as you pull; the grab only waits briefly while the cloth catches up, so it never tears into spikes. Letting go no longer flings the cloth.
- Pulling cloth no longer leaves vertices stuck on the body.
- Stop & Keep: stopping the Live Simulation keeps the drape, and the next Live Simulation or Bake continues from it. Clear Drape returns to the rest shape.
- Presets Normal, Animation and Fitting with a light air drag, so cloth settles calmly and moves heavy instead of wobbling.
- While you orbit, pan or zoom, the preview steps back so moving the view stays smooth.
- New Look toolbar: preset icons and one main button (Live, Stop, Bake; hold and pull right to switch Live / Bake). Buttons over cloth stay clickable during the Live Simulation.

**Cleaner contact and fitting**
- Cloth is moved out of the way before a moving body part reaches it, instead of being caught and carried afterwards. Far fewer garment layers end up crossed in fast motion, at a few percent more bake time.
- Garments modelled for another pose (starting inside the body or inside other cloth) are pulled out cleanly at the start of the bake instead of staying caught and tearing.
- Resting and layered garments no longer jitter, fitted garments hold their shape, and cloth that crumples falls freely instead of hanging in the air.
- Bakes are exactly reproducible and put zero intersections first.
- When the animated body touches itself (a hand pressed onto the hip), the finished bake names those frames as pinched. Cloth caught there cannot stay free of intersections in any solver; this is a hint, not an error.

**Wind and scene compatibility**
- Wind: a Force Empty's Wind (speed, radius and falloff, gusts, turbulence, Air Density and Air Friction) blows the cloth in Live and in bakes, with the same air field as the Wind preview and GAIA. Wind obstacles and wakes are not simulated by AETHER yet and are listed as a note.
- Scenes with a gravity-only Force object run.
- GAIA Water Flow and TETHYS Wetness can stay switched on: AETHER lists them as not simulated instead of refusing the scene.
- Older files with the former loose-edge Sewing switch no longer stop AETHER (CNX-E106). Where loose edges really exist, the switch is shown in the Sewing panel so it can be turned off; use Sewing definitions instead.
- Faster: static drapes run about twice as fast; bending follows GAIA on coarse meshes.

## TETHYS Wetness

- About 4–5x faster wetness bakes: the water solver uses several CPU cores and is no longer slowed down by a few tiny cells.
- Bake All Wetness bakes every cloth with TETHYS switched on at once (up to three side by side, largest first). Cancel pauses; baking again continues where it stopped, also for a single cloth.
- Water details are as strong as before: thin water keeps its true height; only thicker water levels off softly.
- Water contact with FLIP is computed on a finer grid: cloth lying against the body gets full contact, and the water line stays on the real liquid surface. Streaming water drags the surface water along on partly wet cloth.
- The temporary water-contact file is less than half the size.

## PATTERN NeXt

- The 2D pattern editor works next to other add-ons that keep a modal operator running in the background (for example overlay hover trackers). Before, the workspace showed the patterns but did not react to clicks or keys.

## Stability and updates

- Add-on updates no longer fail while the tray Companion, an AETHER Live Simulation or a bake is running: both run from a copy outside the add-on folder.
- Wind: the viewport preview no longer refreshes from Blender's render thread during an animation render (F12), so it stays out of EEVEE's way.
- A Cancel just before a stall deadline is honoured instead of being reported as a stall. *(main nach 2.9.62, im Changelog nicht eigens erwähnt — prüfen, ob in die Notes)*

---

## Gerüst für die vollständigen 3.0-Notes (seit letztem Stable)

1. **AETHER Engine** — native NVIDIA-GPU-Engine für Live und Bake (neu in 3.0), Abschnitt oben.
2. **PATTERN NeXt** — 2D-Schnittmuster-Workspace (2.9.53): Werkzeuge, automatisches Remeshing, Nähte als Sewing Definitions.
3. **TETHYS Wetness** — Nässe auf Stoff aus GAIA-Wasser (2.9.49–2.9.61).
4. **GAIA Water Flow** — Quellen FLIP, Mantaflow, Houdini, LiquiGen; Hydrodynamic Pressure; Wet Fabric (Kennzeichnung "experimental" offen, siehe Beta-Checkliste).
5. **Cloud Bakes** — PolyWorks Cloud (2.9.40–2.9.48).
6. **Sewing** — Segment/Free/Multi/Edit, Toolbar.
7. **Companion** — Hintergrund-Companion mit Tray, Konto-Fenster.
8. **Requirements** — Blender-Version(en), Windows x64 + NVIDIA für AETHER, GAIA-Zugang für Water Flow/TETHYS, Linux-Umfang.
