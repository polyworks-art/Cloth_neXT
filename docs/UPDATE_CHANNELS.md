# Update channels

Cloth NeXt uses one local Blender repository entry and three public feeds:

| Channel | Exact target | Feed |
|---|---|---|
| Stable | X.0.0 | https://polyworks-art.github.io/Cloth_neXT/stable/index.json |
| Beta | X.Y.0 | https://polyworks-art.github.io/Cloth_neXT/beta/index.json |
| Dev | X.Y.Z | https://polyworks-art.github.io/Cloth_neXT/dev/index.json |

The authoritative rules live in [RELEASE_POLICY.md](RELEASE_POLICY.md).

## Switching channels

1. Open Blender Preferences, Add-ons, Cloth NeXt.
2. Select Stable, Beta or Dev. Entering Dev retains the experimental risk
   acknowledgement. Developer Tools are not required.
3. Cloth NeXt changes the owning repository's remote URL and synchronizes it.
4. Click **Switch to Stable/Beta/Dev** or **Update through Blender** as offered.
5. Complete installation in Blender's Extension Manager and restart if requested.

A lower version is actionable across channels. Equal versions are up to date;
same-channel updates require a newer target. Actual replacement always belongs
to Blender, outside the running Cloth NeXt stack.

## Automatic bridge migration

A bridge build delivered through any existing feed resolves the active package's
`bl_ext.<repo-module>.cloth_next` namespace to its owning repository. On a deferred
startup callback it selects the saved channel's URL, synchronizes the same
repository directory, and validates its local index. Migration is considered
complete for this session only after validation; every startup rechecks safely.

The local module, directory, namespace, installed files, preferences and license
state remain unchanged. Other legacy repositories are retained. Migration never
adds or deletes repositories and does not require manual reinstallation. A failed
sync leaves Cloth NeXt usable; retry with **Check for Updates** or next startup.

## Publication and repair

Each feed exposes exactly one current target for its own release level. Publish
only to the release's channel; preserve immutable archives, including former
cumulative publications. All three public paths remain available for bridge builds.
Bridge distribution requires an appropriate numeric build for each release level;
a Dev build is never inserted as a Stable or Beta target.

Indexes are generated exclusively with official Blender tooling:

```text
blender --command extension server-generate
```

Generation stages only the current archive, then copies the resulting index beside
the retained archives. Each archive is byte-identical to the tested release artifact
and verified by SHA-256. Invalid or ambiguous indexes must be repaired at the feed;
users can then retry synchronization without deleting their local repository.

Stable and Beta retain source tags and publish through Pages, not GitHub Releases.
Their canonical artifact set remains at `artifacts/<version>/` with the package,
`release-manifest.json`, `SHA256SUMS.txt` and `RELEASE_NOTES.md`. Repair jobs verify
against those immutable artifacts. Dev publication creates no tag. Publication is
serialized across channels and repair workflows. Pages is public hosting, not DRM.
Channels distribute Cloth NeXt only, never the external PPF Contact Solver.

This implementation does not publish, tag or select an official release version.
