# Water Flow in Cloud Bakes — handoff for Codex (2026-10-24)

Status: the add-on (2.9.43) already packages, quotes, uploads and imports
Hydrodynamic Pressure jobs. Only the server switch is missing. Nothing in the
contract has to change; `cloud-bakes-api-v1.md` already describes hydro
execution (sections "Manifest and files" and "Native execution and raw results").

## What blocks it today

`GET /v1/cloud/capabilities` → GAIA runtime
`gaia-0.26.0-dev.20261022+cuda12.8.windows-x64.cc9b0c13ec2f` reports
`water_flow: false`. The add-on refuses a manifest with `water_flow: true`
before the quote when the mapped runtime does not offer Water Flow (no credits
are reserved). A NEON Bake with water therefore cannot be sent.

## Server changes

1. **Capabilities**: set `water_flow: true` on that GAIA runtime once the Linux
   worker runs hydro jobs. The add-on unlocks as soon as it reads it; no
   add-on release is needed.
2. **Quote / create**: accept `water_mode: "hydrodynamic"` with
   `water_flow: true`, exactly one `hydro-model` file and one or more
   `hydro-volume` files (contract table, lines ~100–115). `native-fluid` is not
   produced by the add-on for GAIA 0.26 and can stay refused.
3. **Execution** (as in the contract):
   - materialize `/input/hydro-model/<name>` and keep every `hydro-volume`
     relative name unchanged below `/input/hydro-volume/`
     (e.g. `field-0-fluid/bricks-00000.bin`, `field-0-track.bin`,
     `force_field.toml`, `hydrodynamics.json`);
   - run with `PPF_EXPERIMENTAL_HYDRO_TEST_JSON=/input/hydro-model/<name>` and
     `PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=/input/hydro-volume`.
4. **Linux build**: the uploaded archive (upload_id
   `ec87858d47224cb7a4f8d869b41e41e6`, sha256 `df93eb25…1af6`) reports on
   `ppf-contact-solver --probe` (cuda and cpu): fluid-transport
   `experimental-velocity-sdf-bricks-v1`, sdf-bounds `fail-closed-v1`,
   wet-fabric, wet-damping, stamp `946c425688ff022e`. No hydro run on the Pod
   image has been done yet.
5. **Limits**: hydro packages are large (about 11.8 MB of volumes per Bake
   frame, 64 MB for the 11-frame dunk test). zstd uploads cut that roughly in
   half; `max_file_bytes` must allow single brick files of ~8.3 MB.

## Test material

- `gaia-hydro-v1` (dunk scene, Blender frames 50–60, 61 files, 64 MB): manifest,
  all blobs, identity, local reference PC2 — on Tim's PC under
  `.hydro-test-tmp/cloud0/gaia-hydro-v1-package.zip` (too large for the project
  folder).
- `gaia-dry-v1`: the same scene without water, already run successfully in the
  Cloud on 2026-10-24 (32 credits, 90.3 s, A5000).
- Caveat: in frames 50–60 wet and dry differ by at most 0.1 mm, so the dunk
  package proves the hydro path runs, not that the coupling acts. For a visible
  effect use a window where the sleeve enters the water, or NEON 130–159.

## Acceptance

From Blender: NEON (or dunk) with Hydrodynamic Pressure → Review & Send →
automatic Pod → progress → download → import; compare with a local GAIA run of
the same frames (expect sub-millimetre agreement, as for the dry run) and check
the settled credits.
