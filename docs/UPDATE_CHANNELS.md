# Update channels

Cloth NeXt uses one local Blender repository entry and three public feeds:

| Channel | Permitted release levels | Feed |
|---|---|---|
| Stable | Stable only | https://polyworks-art.github.io/Cloth_neXT/stable/index.json |
| Beta | Stable and Beta | https://polyworks-art.github.io/Cloth_neXT/beta/index.json |
| Dev | Stable, Beta, and Dev | https://polyworks-art.github.io/Cloth_neXT/dev/index.json |

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

After Install from Disk, Blender may store Cloth NeXt in a local repository.
Cloth NeXt enables that same repository's remote feed automatically; its module
and directory remain unchanged. Welcome appears once on a fresh installation;
What's New appears after an update. The initial update check is delayed until
onboarding has finished starting, and automatic checks do not run in headless
Blender sessions. Already-installed Dev builds keep a visible experimental warning
without asking users to acknowledge the channel they already installed.

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

Each feed exposes exactly one current target from its permitted release levels.
Stable releases are published to all three feeds, Beta releases to Beta and Dev,
and Dev releases to Dev only. For example, Beta 2.5.0 is offered through both
Beta and Dev; Dev users do not need to change their selected feed. Preserve
immutable archives. All three public paths remain available for bridge builds.
A Dev build is never inserted as a Stable or Beta target.

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

## Final GitHub Beta migration bridge (2.6.0)

The owner-approved final GitHub Beta customer release is 2.6.0. Keep its index
and immutable archive available indefinitely for older Beta installations.
Customer releases starting with 2.7.0 are distributed through Superhive; do not
publish them to GitHub Beta. Repository generation and repair accept only 2.6.0 as the Beta target. This publication restriction does not change installed channel semantics.

GitHub Dev remains an internal development channel at
https://polyworks-art.github.io/Cloth_neXT/dev/index.json with its existing owning
repository and publish-dev workflow. The 2.6.0 Beta publication still targets
Beta and Dev under the existing cumulative policy; Stable is unchanged. Future
Stable publication needs an explicitly reviewed policy update if it would also
advance the frozen Beta feed. No automatic Superhive migration is performed.

The 2.6.0 package excludes all development QuickAdd / Quick Assign components.
Only the existing What's New Companion announces migration, with the official
Superhive setup link and Continue. Existing installations remain functional.
Superhive 2.7.0 packaging and customer repository setup are separate future work.
