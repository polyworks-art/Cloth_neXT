# 2.9.26 published owner package validation

2026-10-17. Functional owner evaluation passed for the frozen NEON scene on
Windows, Blender 5.2.2 LTS, with the separately installed patched CUDA runtime.
This is not production certification or support for arbitrary FLIP scenes.

## Published identity and update

- Publication run: https://github.com/polyworks-art/Cloth_neXT/actions/runs/39033639914
- Package source: `8933732b05f841f162c50000e1895ca9ad920913`.
- ZIP SHA-256: `382b14bb2b37f3a3bd7e576c051db535e6180d6f9e3d372c8d2a28fc447176c0`.
- Downloaded published ZIP matches the channel index hash; version-bound owner
  marker and approved dataset digest verified.
- Live HTTP index exposes 2.9.26. A separate isolated Blender profile installed
  the previous published 2.9.25, then updated through the actual remote channel
  to 2.9.26 with matching source metadata. Blender online access is required.
- Only the intended private channel files and permitted error guidance changed;
  legacy channels were unchanged. The private channel URL is deliberately omitted.
- Source CI run 39033702252 passed. Its final commit changes tests only; the
  published artifact remains pinned to the source SHA above.

## Actual installed-package CUDA test

Fresh extension-manager installation of the downloaded published ZIP, installed
namespace `bl_ext.gaia_owner.cloth_next`, regular `bpy.ops.clothnext.bake`,
NEON 130–159 with real frame-160 fluid lookahead. Original three cloth objects,
materials and 186 attachments in four groups retained. One-way hydrodynamics;
Cn=1, Ct=0; no alternate host force implementation or weakened bounds guard.

- Bake: PASS / FINISHED, 386.02 seconds (excludes subsequent verification).
- Three PC2 files, 30 samples each; native-to-PC2 maximum error 6.104e-8 m.
- Save/reload and a separate fresh Blender process: PASS; playback error 0 m.
- Native telemetry: 29 force samples; normal load 91.1289–144.0380 N;
  wetted area 0.537591–0.672909 square metres; tangential load zero.
- Finite telemetry, wet-area bounds and Cn=1 drag upper-bound checks: PASS.
- Independent reciprocal cloth/collider segment tests: zero crossings across
  all 30 sampled frames. Endpoint exclusion 1 micrometre. Collider is not
  watertight; this is not an inside/outside or between-frame collision proof.
- Patched server, worker and CUDA DLL hashes remain identical to the approved
  local runtime manifest. No solver binaries were bundled or replaced.

The first sandboxed attempt failed before native startup with CNX-E180 when
creating a Windows junction. Evidence was retained. The unchanged package and
inputs passed outside the sandbox; no package patch or acceptance bypass was used.

## Comparison and remaining limits

Against the preserved 2.9.25 owner 30-frame run, maximum force difference is
2.2084 N and maximum wetted-area difference 0.001690 square metres. Corresponding
native material-vertex trajectory RMS / maximum differences, measured in world
metres, are 0.831 / 13.022 mm for the main cloth, 0.0123 / 0.0951 mm for cloth
001, and 0.0202 / 0.9483 mm for cloth 002. These are material-vertex differences,
not surface distances; raw PC2 local coordinates are not world metres.

The main-cloth maximum exceeds the existing 8 mm investigation screen. Five
force-step flags remain pending contextual review (outputs 1→2, 19→20, 20→21,
22→23, 23→24; largest step 19.4295 N). Do not relabel these as a visual-stability
pass. Existing CPU/material-zone and general-scene restrictions remain. No new
CPU certification, VRAM-exhaustion test or production approval is claimed.

## Local evidence and setup

Preserved under `.owner-test-tmp/20261017/`:

- `published-2926/`: archive/hash verification, real channel-update proof,
  runtime integrity and reference comparison.
- `published-2926-full/`: failed sandbox attempt.
- `published-2926-full-host/`: successful bake, fresh-process verification,
  temporal audit and native session evidence.
- `published-2926-collider-audit/`: independent sampled-frame collision results.

The update installs the add-on and Companion, not a replacement official solver.
For this workstation, select the existing local manifest
`.owner-test-tmp/20261017/published-2926/local-runtime/owner-runtime.json` in
Preferences → GAIA Owner Runtime, select the separate patched installation under
Active Solver and CUDA, then save preferences. The manifest and its referenced
approved dataset/runtime must remain available. The ordinary Blender profile was
not modified by these tests. See GAIA_OWNER_TEST_BUILD.md for owner scope and
rollback instructions; its original 2.9.25 snapshot evidence remains historical.
