# Dependency policy

Phase 1 performs no dependency installation, protocol encoding or hardware probing.
Cloth NeXt will never run an unsolicited `pip install` in Blender's Python environment.

| Dependency | Classification | Distribution decision | Missing behavior |
|---|---|---|---|
| `cbor2` | required from the first real PPF protocol adapter | platform-matched wheel declared/bundled in the Blender extension; version pinned to tested builds | PPF features unavailable with a categorized Dependency error; registration remains usable |
| `psutil` | optional HUD/process telemetry | prefer a bundled, pinned platform wheel only when HUD phase proves it necessary | use standard-library process data where safe or show metric unavailable |
| NVML / `pynvml` | optional NVIDIA HUD telemetry | do not make core dependency; consider a pinned wheel in a platform-specific extension build | fall back to throttled `nvidia-smi`, then unavailable |
| `nvidia-smi` | optional external executable supplied by NVIDIA drivers | never bundle or download it; invoke later with an argument list and timeout | GPU telemetry unavailable; solver capability is diagnosed separately |

Blender recommends self-contained extensions and wheels for third-party Python
dependencies. Cloth NeXt will prefer platform-specific wheels in the extension manifest
over modifying Blender's interpreter. Pure-Python vendoring is reserved for small,
auditable packages where wheel packaging is unsuitable. Optional monitoring failures
must never prevent add-on registration or simulation. Required protocol dependencies
must fail before transfer with an actionable typed error.

No wheel is included in Phase 1 because none is used. Dependency detection must be lazy
and located in the feature adapter that needs it. Downloads, if ever supported, require
explicit user action, Blender online-access compliance, checksums and the later updater
security design.

Phase 2.5 bundles the official solver as a complete redistributable tree, including its
embedded Python and native runtime files. These are solver runtime dependencies, not
modules installed into Blender Python. Cloth NeXt injects bundle-relative environment
paths only into the owned solver child process.

Source: Blender's official [extension add-on guidance](https://docs.blender.org/manual/en/5.0/advanced/extensions/addons.html)
recommends bundling external Python dependencies as wheels and using relative imports.
