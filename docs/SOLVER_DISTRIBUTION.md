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
- Official release tag: `2026-07-13-21-05`.
- Release tag commit: `4f42d8c1bcb3945668ff7dbf6e4c768fc7fd6f2e`.
- Previously audited later commit: `7193f158e3843597070f66cb29af19efd9bdcff7`.
- Asset: `ppf-contact-solver-2026-07-13-21-05-win64.zip`.
- URL: <https://github.com/st-tech/ppf-contact-solver/releases/download/2026-07-13-21-05/ppf-contact-solver-2026-07-13-21-05-win64.zip>
- Asset size: 450,996,388 bytes.
- Asset SHA-256: `ad51f4fedfe1dfdf4c837b8e55cc4fe2a73efb5fe9b6abff3d2e9cc742a0b6f9`.
- Installed EXE SHA-256: `4deccdd138c17c3b9eb002b49bcf0f5bad9d4befa236239232a1c9b61e9360b3`.
- Reported package/protocol/schema: `0.1.0` / `0.11` / `1`.
- Real health check: passed.
- Local tree: about 1.43 GB, 15,070 files; not suitable for ordinary Git blobs.

The release predates the audit commit, so the two commit IDs are deliberately not
claimed identical. Compatibility is established through the exact executable version
and real wire check required by Cloth NeXt, not by rewriting provenance.

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
