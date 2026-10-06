# Collider Attachments validation

Validated 2026-10-06 against the local checkout of
`codex/gaia-water-flow-2.9.7`, based on `8b55684`. `git fetch origin` confirmed
the local and remote branch tips were identical before implementation.
Existing uncommitted Blender, cache, companion and water-prototype work was
preserved. These initial results were recorded before release preparation.

The isolated 2.9.16 release checkout includes only the Attachment changes and
release metadata. Its full suite passed with **2156 passed, 20 skipped,
3 deselected**. A further real CUDA Deforming-Collider bake from that checkout
passed with a final anchor distance of **0.987 mm**.

## Behavior

Cloth and Soft Body can attach to an enabled Collider using the existing
Target Object picker, vertex groups or viewport Attachment editor. Static,
transform-animated and deforming Colliders use native Gaia `cross_stitch`
constraints. Collider sources remain unsupported.

Collider anchors use evaluated surface coordinates, including shape keys and
input modifiers. Their vertex indices and barycentric weights remain fixed
through animation. Bake validation uses the Bake-start pose and restores the
user's timeline frame and subframe.

Collider topology validation uses polygon loops, so a display-triangulation
change of a deformed quad does not invalidate a relationship. Changes to
vertex numbering or polygon connectivity require rebinding. Topology-changing
input modifiers must be applied before binding. A substituted collision proxy
is not silently used in place of an anchor on the original surface.

Shape keys are evaluated even when the simulation boundary is the first
modifier. Animated Collider capture consistently excludes modifiers after
that boundary. Viewport preview reads only the referenced Collider vertices.

Collider Attachments require Gaia 0.23. Older adapters fail with an explicit
instruction to select Gaia 0.23, while existing deformable-to-deformable
Attachments retain their solver compatibility.

## Automated checks

- Full repository suite: **2167 passed, 20 skipped, 3 deselected**.
- Final focused checks covering Attachments, modifier evaluation, pinning,
  validation snapshots and the official bridge: **138 passed**.
- Real Blender save/reopen Attachment workflow: **PASS**.
- Python compilation and `git diff --check`: **PASS**.

The existing skips concern unconfigured native integration fixtures and
platform/artifact-specific tests. Native Collider integration was checked
separately below.

## Real Blender and CUDA

Blender 5.2.2 LTS and the unmodified Gaia 0.23 CUDA runtime were used. Each
case exported via normal Cloth NeXt validation and `build_run_plan`, then
ran through `SolverSession` for eight output frames. Tests checked native
target UUIDs, constraint strength, animation payloads, finite output,
timeline restoration, unchanged anchor indices/weights and rejection of a
disabled target. The moving endpoint advanced 0.08 m; the attached endpoint
followed it.

| Source | Collider motion | Final anchor distance |
| --- | --- | --- |
| Cloth | Static | 1.000 mm |
| Cloth | Transform animated | 0.982 mm |
| Cloth | Deforming / shape key | 0.987 mm |
| Soft Body | Deforming / shape key | 0.989 mm |

Reports are generated under `dist/collider-attachments-smoke-v2/report.json`
and `dist/collider-attachments-softbody-smoke/report.json`.

The direct AppData runtime test hit the previously documented Windows
project-directory alias failure before simulation (`WinError 183` creating
`.cash`). Successful tests used the existing runtime copy under
`dist/gaia-streaming/official`. Its server executable is byte-identical to
the installed runtime (SHA-256
`B9727B87156B340B0047F0AC7303DBBFD4E1F1D4DBD4C98EA3692E3EB53EE483`).
No solver sources or binaries were modified. See the earlier
`GAIA_023_LIFECYCLE_RESULTS.md` for the AppData execution-context limitation.

## Reproduce

From the repository root, run factory-startup Blender with
`--background --factory-startup --python-exit-code 1 --python
tools/blender_collider_attachments_smoke.py -- --solver <Gaia-0.23-executable>
--output <absolute-report-directory>`.

The default runs the three Cloth cases. Add
`--source-role SOFT_BODY --motion DEFORMING_ANIMATED` for the tetrahedral-source
case. The harness uses isolated objects/projects and preserves the normal
solver registry.
