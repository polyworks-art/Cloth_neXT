# Cloth NeXt Superhive FAQ

## Is the PPF Contact Solver included?

No. Cloth NeXt and PPF are separate projects. The extension guides you through
installing or selecting a compatible official solver only after you confirm the
download. The Superhive ZIP does not contain the solver.

## Why is an NVIDIA GPU required?

The supported PPF Contact Solver release uses NVIDIA GPU acceleration. Cloth NeXt
handles the Blender workflow, but it does not change the solver's hardware requirements.

## What should I try first when a Bake fails?

Run **Scene Health Check** and follow the displayed action. If the problem remains,
record the `CNX-E…` error code and export a privacy-safe support report.

## Why did the Bake stop because of RAM usage?

RAM Auto Cancel protects the workstation before memory pressure makes Blender or
Windows unresponsive. Reduce mesh density or the frame range, close other memory-heavy
applications, or adjust the safety threshold carefully in the add-on preferences.

## Why can an animated collider pass through cloth?

Confirm that Collider Motion is set to **Animated**. Start with 8 Motion Samples per
Frame and try 12–16 for fast or strongly curved movement. A simpler simulation proxy
can reduce the preparation cost of dense animated colliders.

## Does Curve bevel control cable thickness?

No. Curve bevel changes only the rendered appearance. Use **Surface Offset** as the
uniform simulated cable radius. Variable physical cable thickness is not yet supported.

## How does Friction work?

Lower values slide more easily; higher values resist sliding. Cloth NeXt uses the
lower Friction value of the two touching objects, so both surfaces must have enough
Friction for a less slippery contact.

## Can I remove data from a failed Bake?

Yes. Open Scene Health, choose **Scan Cache Directory**, and then
**Remove Invalid Caches**. Cloth NeXt removes only incomplete or corrupt caches it can
identify as its own. Unverified legacy PC2 files are left untouched.

## What information should I send to support?

Send the `CNX-E…` error code, the privacy-safe report, Blender version, GPU model,
and concise reproduction steps. Do not send a production `.blend` unless support
specifically requests a reduced example.
