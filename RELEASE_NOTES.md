# Cloth NeXt 2.2.29 Dev

Cloth NeXt 2.2.29 repairs the complete cancel-to-Recovery workflow and keeps
live Bake progress synchronized in Blender.

## Live Bake viewport

- Auto-Frame Cloth During Bake keeps every live-baked deformable visible in
  open 3D viewports.
- Smooth and Cinematic motion styles control how deliberately the view follows
  position and scale changes.
- Framing response and margin are configurable in Add-on Preferences.
- Fast cloth expansion pulls the view back immediately to avoid cropping;
  closer framing remains softly damped.
- Camera view is never modified.

## Cancellation and Recovery

- Cancel is latched before startup ownership can move between the controller,
  pending plan, and worker.
- The active plan owns the run before the EXPORTING transition is published.
- A cancelled Bake remains in its terminal state so the Bake Window and
  Recovery UI can reliably observe checkpoint results.
- The next Bake or Resume performs the controlled transition back to a new
  PREPARING state without a background reset timer.
- Recovery compatibility uses the canonical solver parameter payload identity,
  so a freshly saved checkpoint is accepted by its own Resume operation.
- Cancel waits for and reconciles durable solver state even when the status
  connection closes before the checkpoint file becomes visible.
- Rebake accepts Cloth NeXt's private live PC2 and the exact authenticated
  Recovery partial without weakening cache ownership checks.

## Live progress

- Timeline marker and Cloth NeXt Bake strip now follow SIMULATING and FETCHING
  progress, including resumed Bakes.
- Growing PC2 playback remains attached before Blender evaluates the next
  timeline frame, preserving live loading.

## Compatibility

- Velune protocol 0.13 and Lumen protocol 0.18 remain supported.
- The external PPF Contact Solver remains a separate explicit download and is
  not included in the extension archive.

Version `2.2.29` is published only to the Dev channel.
