# Audit limitations and unsupported claims

- Pin Mode supports Static and Follow Animation for evaluated topology-preserving deformation such as Armature, Shape Keys, Lattice, Mesh Deform, Surface Deform, Hook, drivers, and object transforms. Any evaluated vertex-count change is rejected. Soft Pull, timed release, and operation stacks are not exposed.

Current production scope is multiple Cloth, Rod, and Soft Body deformables in
one shared solve with one or more static or animated Colliders. Every
deformable must use the same Bake range and scene-wide Contact setting.
Force objects are supported on Emptys: Gravity uses local `-Z`, Wind uses
local `+Z`, and multiple forces of each type are summed.
Animated Colliders require stable evaluated topology. Uniform object-local shell
pressure and pinning are supported. Bake ranges are limited to
10,000 output frames, and zero-step (`Start == End`) PPF runs are not supported.

PC2 creation is frame-streamed with `O(vertex_count)` animation memory.
Simulation acceleration affects only the solver phase; frame transfer, NumPy
coordinate conversion, PC2 writing, final disk flush, and modifier attachment
remain CPU/RAM/I/O work. Solver performance depends on the selected time step.
This optimization did not change the `dt = 0.001` default or any solver,
material, pressure, collision, quality, frame-range, or FPS value. Owned and
external solvers currently both use TCP result transfer; direct local reads are
disabled until the pinned server's exact path and atomic publication contract
can be proved without guessing.

When automatic Bake-window launch is enabled, inability to create a visible,
topmost, responsive companion is a fatal startup error. Blender remains
editable and the previous cache is preserved. Disabling automatic launch opts
into Blender-only progress without a global workflow lock.

- Upstream was audited at commit `7193f158` on 2026-07-12. Protocol/schema and docs can
  change; implementation must pin a compatible solver release.
- No NVIDIA solver binary was installed or launched in this audit. Findings are static
  source/documentation evidence, not a successful GPU solve on this workstation.
- A locally installed official solver passed the real health integration test during
  development. That external runtime is untracked local state and is never included in
  a Cloth NeXt package.
- Blender 5.1.2 is available through the local Steam installation and the automated
  registration/RNA smoke test passes. Background mode cannot verify final on-screen
  HUD contrast, clipping, DPI behavior, or icon appearance; those remain explicit
  interactive visual checks.
- PPF 0.11 status responses do not carry schema or package versions. Full verification
  is possible for an owned local executable via `--version`; external servers remain
  protocol-identified but schema-unverified.
- The official release tag commit (`4f42d8c1…`) is earlier than the audited source
  commit (`7193f158…`). Runtime compatibility is verified as package `0.1.0`, protocol
  `0.11`, schema `1`; source identity is not falsely claimed.
- The official Windows archive contained no root project license. Bootstrap used the
  unchanged Apache-2.0 license from the official checkout and preserved discovered
  third-party notices. Formal release notice review remains open.
- Exact CBOR scene schemas are extensive. This audit records the envelope and relevant
  keys, but implementation requires upstream format definitions and golden fixtures;
  filenames named `.pickle` must not be mistaken for Python pickle content.
- PPF supports incremental complete-frame fetching. It does not promise real-time
  delivery or a stable frame cadence; UI wording remains Buffered Live/Follow Solver.
- Cloth NeXt uses PC2 playback for constant topology. It cannot represent
  topology-changing tearing.
- Pressure is implemented as a uniform shell parameter. Target volume, compressibility,
  gas behavior and pressure animation are not yet verified and must not be exposed.
- Independent Blender-style self-collision controls and tension/compression/shear
  stiffness mappings are not established. PPF contact is unified; fake mappings are
  prohibited.
- Static and Follow Animation hard pinning are implemented for one vertex group;
  soft Pull, timed release, and operation stacks remain unsupported.
- Dynamic tearing/ripping is unsupported by the unchanged solver. Only disabled future
  interfaces may exist.
- The official add-on's release index updates that add-on. No supported standalone
  solver stable/experimental manifest with checksums was found, so automatic solver
  updates cannot be implemented safely yet.
- The official add-on is a technical reference only. Cloth NeXt will not copy it in
  full or import it at runtime.
- The repository's GPL-3.0-or-later license covers Cloth NeXt, not the separately
  installed Apache-2.0 PPF Contact Solver.

## Required evidence before implementation claims support

For every PPF feature, record upstream commit/release, protocol/schema versions, source
definition, a serialized fixture, observed server response, and an integration test.
If any item is missing, present the feature as unsupported or experimental rather than
guessing an endpoint, parameter or format.
- The Phase 2.6 remote beta end-to-end test has not run yet: it needs a
  user-chosen `-beta.N` version, pushed code and tag, GitHub Pages configured to
  serve the `gh-pages` branch, and a local Blender 5.x installation for the
  install/update verification. A stable release is forbidden until it passes.
- The release workflow pins `BLENDER_VERSION` for the official extension
  tooling; the exact Blender 5.x download URL must be confirmed on first CI run.
- The solver compatibility manifest pins exactly one verified upstream release
  (`2026-07-09-04-39`). Newer upstream releases are not offered until tested
  and added through a reviewed manifest change.
- Blender-dependent UI paths have automated unit/smoke coverage; final interaction,
  display, DPI, and platform behavior still require testing in Blender itself.
- The Phase 2.7 Blender registration smoke test is wired into CI
  (`.github/workflows/ci.yml`, job `blender-smoke`) but has not run on this
  machine because Blender 5.x is not installed locally. The pinned CI Blender
  download URL and the `extension install-file` invocation must be confirmed on
  the first CI run.
- Phase 2.8A Add-Physics placement: Blender's `PHYSICS_PT_add` panel draws its
  native Add-Physics buttons inside an internal two-column `grid_flow` that is
  not exposed to appended draw callbacks. The "Cloth NeXt" entry therefore
  renders as a full-width native-style button directly below the native button
  grid, added through the stable `Panel.append`/`Panel.remove` API. Placing it
  inside the grid (e.g. beside a FLIP-Fluids button) would require replacing or
  monkey-patching Blender's `PHYSICS_PT_add.draw`, which is deliberately not
  done. The `MOD_CLOTH` icon is a temporary stand-in for a Cloth NeXt icon.
- Add-on update check (Phase 2.8B, hardened by the Phase-3B hotfix): Blender
  5.1.2 exposes public operators for repository management and updates
  (`preferences.extension_repo_add`, `extensions.repo_sync`,
  `extensions.userpref_show_for_update`) and the
  `preferences.extensions.repos` RNA, but **no public operator or RNA to query
  whether a specific package has an update available** — that information
  lives in the private `bl_pkg` add-on internals, which Cloth NeXt must not
  import. The *Check for Updates* status therefore reads the policy-defined
  channel `index.json` (official format, generated by the official Blender
  tooling) in a worker thread. The former direct
  `package_install(repo_directory=, pkg_id="cloth_next")` call was removed:
  invoking it from a Cloth NeXt operator makes Blender disable/replace/
  re-enable the extension whose code is still on the Python stack — a
  native-level crash risk that try/except and timer deferral cannot fix.
  *Update through Blender* now only synchronizes the exact channel repository
  and opens Blender's native update view; the user completes the installation
  there and restarts Blender when Blender prompts for it
  (`tests/test_update_selfinstall_policy.py` keeps the self-install path out).
- Extension operator `repo_index` parameters (verified in real Blender 5.1.2,
  `bl_pkg` `extension_repos_read_index`): they index the **filtered** list of
  enabled repositories with valid settings, not
  `preferences.extensions.repos`. As soon as any earlier repository is
  disabled, a raw-collection index resolves to the wrong repository or to
  none, and `repo_sync(repo_index=…)` raises "Repository not set". The same
  applies to `package_upgrade_all(use_active_only=True)`: it depends on the
  `active_repo` UI state. Cloth NeXt therefore always identifies its channel
  repository by the resolved `directory` RNA and passes `repo_directory=` —
  verified by `tools/blender_update_smoke_test.py` inside real Blender,
  including the disabled-repository condition. The fake-bpy unit tests check
  Cloth NeXt's own argument construction and fallback logic only; they make
  no claim about real Blender operator context compatibility.
# Phase 2.8B interface limitations

## Dev channel

The public Dev channel is unsupported and uses reduced validation. Builds may
be incomplete, unstable, incompatible, removed, or invalidate caches/settings.
Dev is never automatic; keep backups. Mandatory safety checks still apply.

## Phase 3B production slice

- Multiple deformable objects (Cloth shells, experimental Rod / Cable, and
  experimental Soft Body) in one interacting solve, one or more
  static/animated colliders, and an artist-selected
  Bake Start/End range with a 10,000-output-frame safety limit.
- For Cloth shells, Static and Follow Animation hard pins through one vertex
  group and uniform object-local pressure are supported. Soft Pull, timed pin release, shrink,
  stitching, plasticity, tearing, sand, PDRD,
  dynamic material/pressure animation, and a separate
  substeps control are unsupported. Time Step/Newton/PCG Quality use verified PPF
  keys; unsupported controls remain hidden rather than shown as fake settings.
- Playback is constant-topology PC2. Bake Start is the exported initial state;
  solver step `n` maps to Blender frame `Bake Start + n`.
- Rods accept Bezier and Poly Curves only. Playback keyframes Curve control
  points directly; existing user Curve animation blocks a bake rather than
  being overwritten. NURBS conversion, Rod pinning, and animated Rod rest
  topology are unsupported. Curve Bevel/Taper/point radius are visual only;
  `Surface Offset` provides one uniform collision-radius approximation for the
  simulated centerline. Variable physical cable thickness is unsupported.
- Static and Follow Animation Cloth pins are supported per object in
  multi-object bakes. Every animated pin track is captured independently and
  mapped to its deformable UUID.
- Soft Bodies require a closed manifold mesh. The external solver performs
  tetrahedralization and Cloth NeXt maps the simulated tetrahedral surface back
  to the original vertices. Soft Body pinning and animated source topology are
  unsupported.
- GPU telemetry depends on available NVIDIA `nvidia-smi` tooling and may be
  unavailable or temporarily stale. It is system GPU telemetry, not proof that
  PPF selected that exact CUDA device.

- Material, Damping, and Collision properties are really mapped to the PPF
  payload (Phase 3B). PPF's stiffness (`young-mod`) is a density-normalized
  wire value, **not** a textbook Young's modulus in pascals; Cloth NeXt uses
  that single representation everywhere.
- The built-in fabric presets are calibrated upstream starting points, not
  guarantees for every mesh scale, resolution, or scene setup.
- Static and Follow Animation hard Pinning through one Blender vertex group are
  supported. Pin indices require topology-preserving evaluated Cloth geometry.
  Timed release, soft Pull, multiple pin groups, animated
  Pressure, and native Blender Cloth remain unsupported.
- New Bakes use the versioned, SHA-256-authenticated production cache metadata
  described in [CACHE_FORMAT.md](CACHE_FORMAT.md). Legacy caches without that
  schema can remain visible for migration but cannot gain the authenticated
  Phase-4 status without a Rebake.
- Bake actions run the compatible external PPF solver; Developer Test Tools remain
  separately labeled diagnostics.
- The Viewport resource monitor is display-only and shows cached CPU, RAM, and
  VRAM history during a Bake. Bake status and cancellation remain dedicated UI
  actions outside the monitor.
- The optional Windows companion is bundled and hash-validated. When automatic
  launch is enabled, it receives real bake status and must open successfully before
  startup continues; users can disable it for Blender-only progress.
