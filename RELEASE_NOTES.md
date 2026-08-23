# Cloth NeXt 2.3.1 Dev

Cloth NeXt 2.3.1 hardens cache cleanup and updates the public error catalogue to
match the modern Bake, solver, recovery, playback, and Companion lifecycle.
This is a Dev release for validation before the next Beta.

## Reliable cache cleanup

- Cloth NeXt now closes its playback readers and modifiers, PC2 writers, worker
  threads, Companion process, and owned solver processes before removing files
  they used.
- One shared safe-delete implementation handles owned caches, metadata, PC2,
  recovery, export, updater, temporary, and work-directory artifacts.
- Short Windows sharing violations, antivirus scans, and delayed handle release
  receive a small bounded retry/backoff sequence.
- An authenticated obsolete artifact that remains briefly locked can be renamed
  inside the same owned cache root to a unique `.clothnext-delete-*` tombstone.
  Later cache scans and Bake lifecycle points remove it.
- A removable obsolete temporary file no longer turns an otherwise valid,
  completed Bake into a failed cache. Final cleanup failures retain detailed
  local diagnostics under `CNX-E193`.

Ownership safety remains strict: out-of-root paths, legacy caches without Cloth
NeXt ownership metadata, and unknown user files are protected. Cloth NeXt never
terminates foreign processes.

## Accurate error guidance

- The complete public catalogue is generated from the runtime registry, and the
  same source now produces the GitHub Pages `errors.json` feed.
- New dedicated codes identify animated Collider capture (`CNX-E128`), invalid
  recovery checkpoints (`CNX-E129`), and occupied solver ports (`CNX-E136`).
- Structured solver failure kind, crash kind, and active lifecycle stage now
  classify startup, upload, build, simulation, and result-transfer failures
  without relying only on generic text.
- Historical false matches involving intersections, convergence, memory,
  recovery, authentication, topology, playback, Rod Curves, cleanup permissions,
  and missing native workers have been corrected while all existing identifiers
  remain compatible.

## Included validation

- Deterministic cleanup tests cover successful, missing, transiently locked,
  exhausted, tombstoned, unsafe, legacy, cancellation, finalization, metadata,
  and repeated-cleanup cases.
- Error tests cover every specific classifier, cross-stage negative cases,
  structured crash diagnostics, stable identifiers, and exact public-data
  synchronization.
- The normal repository suite passes 1,637 tests. Source compilation and
  extension validation pass.

The external PPF Contact Solver is unchanged, remains a separate installation,
and is not bundled with Cloth NeXt.
