# Solver distribution record

> Cloth NeXt does not distribute the PPF Contact Solver.
>
> The solver is downloaded separately from its official upstream provider after
> explicit user confirmation. Cloth NeXt only manages compatibility, installation
> location, health checks, process lifecycle, and Blender integration.

Cloth NeXt releases never contain, mirror, repackage, proxy, or redistribute the
solver executable or its runtime bundle (see
[RELEASE_POLICY.md](RELEASE_POLICY.md) section 6). Release artifacts are scanned
by `tools/scan_release_artifact.py`, and the release aborts on any hit.

## Current locally validated runtime

- Official project: `st-tech/ppf-contact-solver` (ZOZO, Inc.).
- Official release tag: `2026-08-12-15-47` (Lumen).
- Release tag commit: `53b8da89a8cbce1c54538f53690f6f5c506dbb47`.
- Asset: `ppf-contact-solver-2026-08-12-15-47-win64.zip`.
- URL: <https://github.com/st-tech/ppf-contact-solver/releases/download/2026-08-12-15-47/ppf-contact-solver-2026-08-12-15-47-win64.zip>
- Asset size: 447,922,058 bytes.
- Asset SHA-256: `f80d185b5c585b5f7749d747f317f4e7ab57d0522f10083f970089ff7d378733`.
- Reported package/protocol/schema: `0.1.0` / `0.18` / `2`.
- Real health check: passed.
- Local tree: about 1.43 GB, 15,070 files; not suitable for ordinary Git blobs.

Compatibility is established through the immutable release asset, exact executable
version, fail-closed frontend recipe, real health check, and real Bake scenarios.

## License and notices

Upstream is Apache License 2.0. The unchanged upstream license and license/notice files
found throughout the runtime are preserved under `LICENSES/`; 253 license/notice files
were collected by the current bootstrap. No license text is modified. A production
distribution still requires a deliberate third-party notice review; the automatic
collector is evidence preservation, not legal advice.

## Git strategy

Current strategy is **B: binary remains local and ignored**. Git LFS is installed, but
no LFS pattern is configured and no 1.43 GB runtime is silently committed. Tracked
files are the bootstrap/build code, documentation, `.gitkeep`, and solver README.
Generated runtime files including local `SOURCE.json` stay ignored.

Since Phase 2.6, no build mode bundles the solver anymore: `tools/build_extension.py`
has no `--with-solver` option, the artifact scanner rejects any solver material, and
the release policy forbids publishing, mirroring, or repackaging the solver through
any Cloth NeXt channel. Users install the solver separately through the add-on
preferences from the official `st-tech/ppf-contact-solver` release pinned in
`cloth_next/solver_compatibility.json`.
