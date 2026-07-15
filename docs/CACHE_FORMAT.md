# Cloth NeXt playback cache format

## Scope

Each completed Bake publishes one `cn_test_cloth_<id>.pc2` file and one
`cn_test_cloth_<id>.meta.json` sidecar. The PC2 contains constant-topology
float32 positions. The sidecar is the authority for ownership, completeness,
identity, invalidation, and integrity; an unauthenticated pair is never attached
as a new production result.

## Transaction states

Before frame generation starts, Cloth NeXt atomically writes a sidecar with
`completion_state: "partial"`. The PC2 writer streams into a unique temporary
file, flushes and validates its exact length, and atomically replaces the final
PC2 only after every expected frame exists. Cloth NeXt then calculates the PC2
SHA-256, writes the complete metadata to another temporary file, flushes it,
and atomically replaces the partial sidecar.

Cancellation or failure removes an unpublished PC2 and leaves a small
`cancelled` or `failed` sidecar for diagnosis. Every state other than
`complete` is classified as partial and is unusable for playback.

The previous attached result is not removed until the new PC2/metadata pair is
authenticated and attached successfully.

## Metadata schema version 1

Required top-level fields are:

- `schema_version`: currently `1`.
- `completion_state`: `partial`, `complete`, `cancelled`, or `failed`.
- `cache_format`, `cache_file`, and `hash_algorithm`.
- `fingerprints`: settings, geometry, topology, object, scene, and their
  combined Bake fingerprint.
- `identities`: Cloth NeXt, Blender, object, and exact solver protocol/schema
  identities.
- `expected`: vertex count, frame count, start frame, and sample rate known
  before simulation.
- `details`: material, quality, Collider, range, and Pinning inputs.
- Complete records additionally contain `cache_size`, `cache_sha256`, the exact
  PC2 layout, timing diagnostics, and `metadata_digest`.

`metadata_digest` is SHA-256 over canonical UTF-8 JSON containing every
semantic metadata field except the digest itself. `cache_sha256` authenticates
every PC2 byte. File length and the PC2 header are checked independently.

## Invalidation

The cheap settings fingerprint includes material/contact values, quality,
range, FPS, roles and identities, Pinning intent, Collider modes/materials, and
deformable/Collider world transforms. It can be compared during panel drawing
without reading a mesh.

Explicit Validate and Bake additionally hash object-local positions and
connectivity for the deformable and every Collider, Pin membership, and ordinary
Action keyframes. Geometry/depsgraph changes first demote the result to “needs
validation”; only the explicit full validation may classify it as matching or
stale. Solver or add-on updates do not mutate existing cache files.

## Integrity classifications

- `READY`: metadata and PC2 authenticate and requested fingerprints match.
- `STALE_SETTINGS`: integrity is valid but solver-visible settings changed.
- `STALE_GEOMETRY`: integrity is valid but validated geometry changed.
- `PARTIAL`: generation did not reach a complete atomic publication.
- `CORRUPT`: schema, metadata digest, PC2 hash/size/header, or file identity is
  invalid.
- `MISSING`: the expected pair is incomplete or absent.

Clear Result removes only playback marked as Cloth NeXt-owned and cache files
whose owned filename begins with `cn_test_cloth_`. It does not traverse or
delete unrelated project files.
