# Dependency policy

Cloth NeXt will never run an unsolicited `pip install` in Blender's Python environment.

| Dependency | Classification | Distribution decision | Missing behavior |
|---|---|---|---|
| Built-in CBOR codec | internal pure-Python module | shipped as `cloth_next.ppf.schema.cbor_codec`; no `cbor2` runtime dependency | protocol encoding remains self-contained |
| NumPy | Blender-provided Python module | not separately installed by Cloth NeXt | a bake fails with an actionable dependency error; registration remains usable |
| `nvidia-smi` | optional external executable supplied by NVIDIA drivers | never bundled or downloaded; invoked with an argument list and timeout | GPU telemetry is unavailable; solver capability is diagnosed separately |
| Newton Physics 1.4.0 / Warp 1.15.0 | optional experimental external backend | explicit confirmed install into a dedicated application-data Python 3.11 environment; never bundled or installed into Blender | PPF Bake remains available and Live Preview reports Newton unavailable |

Blender recommends self-contained extensions and wheels for third-party Python
dependencies. Cloth NeXt will prefer platform-specific wheels in the extension manifest
over modifying Blender's interpreter. Pure-Python vendoring is reserved for small,
auditable packages where wheel packaging is unsuitable. Optional monitoring failures
must never prevent add-on registration or simulation. Required protocol dependencies
must fail before transfer with an actionable typed error.

The extension currently declares no Python wheels. Dependency detection is lazy and
located in the feature adapter that needs it. The managed solver download is a separate,
explicitly confirmed workflow with host restrictions, size limits, checksums, safe
extraction, version probing, and a health check. The solver is installed outside the
extension and is never redistributed in a Cloth NeXt package.

The same isolation rule applies to the optional Newton environment. Its setup,
licenses, exact pins and runtime boundary are documented in
[NEWTON_LIVE_PREVIEW.md](NEWTON_LIVE_PREVIEW.md).

Source: Blender's official [extension add-on guidance](https://docs.blender.org/manual/en/5.0/advanced/extensions/addons.html)
recommends bundling external Python dependencies as wheels and using relative imports.
