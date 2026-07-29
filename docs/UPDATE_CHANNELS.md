# Update channels

Cloth NeXt publishes three Blender-compatible remote extension repositories through
the public `gh-pages` branch. Their `index.json` files are generated exclusively by
the official Blender tooling:

```text
blender --command extension server-generate
```

The authoritative rules live in [RELEASE_POLICY.md](RELEASE_POLICY.md).

## Channels

| Channel | Active candidate may be | Repository URL |
|---|---|---|
| stable | Stable | `https://polyworks-art.github.io/Cloth_neXT/stable/index.json` |
| beta | Beta or Stable | `https://polyworks-art.github.io/Cloth_neXT/beta/index.json` |
| dev | Dev, Beta, or Stable | `https://polyworks-art.github.io/Cloth_neXT/dev/index.json` |

Visibility is cumulative: Stable releases are published to all repositories, Beta
releases to Beta and Dev, and Dev snapshots only to Dev. Each repository exposes
exactly one active `cloth_next` candidate.

Every copied archive is byte-identical and SHA-256 verified.

## Pages-only release distribution

Stable, Beta, and Dev do not create GitHub Releases.

Stable and Beta retain immutable source tags, then publish the tested package through
GitHub Pages only. Their canonical release set is stored at:

```text
artifacts/<version>/
  cloth_next-<version>-windows-x64.zip
  release-manifest.json
  SHA256SUMS.txt
  RELEASE_NOTES.md
```

The channel repositories contain byte-identical copies used by Blender. Repair jobs
verify and restore a selected channel from the canonical Pages artifact rather than
from a GitHub Release asset.

Pages is public hosting. This layout removes the prominent GitHub Releases download
surface and reduces casual discovery, but it is not authentication or DRM. Blender's
repository index necessarily contains the active package URL.

GitHub Pages must serve the `gh-pages` branch from its root. Publication is serialized
so Stable, Beta, Dev, and repair workflows cannot race while updating indices.

## Adding a channel in Blender

Through Cloth NeXt:

1. Open Edit → Preferences → Add-ons → Cloth NeXt.
2. Pick the **Update Channel**. The installed `STABLE.BETA.DEV` version determines the
   default. Dev still requires Developer Tools and explicit risk acknowledgement.
3. Click **Add Channel Repository**. This registers the selected fixed repository URL
   in Blender's Get Extensions repositories only after explicit confirmation.
4. Click **Check for Updates**.
5. When an update is available, click **Update through Blender**. Cloth NeXt syncs the
   repository and opens Blender's native extension view.
6. Complete the update in Blender and restart when Blender requests it.

Update checks and installation are separate lifecycles. Cloth NeXt reads `index.json`
to report status; Blender performs the actual package replacement. The running add-on
never self-replaces.

Or add the repository manually:

1. Edit → Preferences → Get Extensions → Repositories → `+` → Add Remote Repository.
2. Enter one of the channel URLs above.
3. Synchronize the repository.

## Single-candidate repository rule

Older immutable archives may remain in a channel directory, but `index.json` exposes
exactly one active `cloth_next` candidate. Multiple records with the same package id
make Blender's displayed and installed candidate ambiguous.

Profiles that cached an older duplicate-entry index may need the repository removed and
re-added after the public index is repaired.

## Rules

- Prereleases never appear in the Stable repository.
- Beta accepts Stable and Beta candidates.
- Dev accepts Stable, Beta, and Dev candidates.
- Stable publishes to Stable, Beta, and Dev.
- Beta publishes to Beta and Dev.
- Dev publishes only to Dev.
- Channel archives match the canonical Pages artifact byte-for-byte.
- No Cloth NeXt release workflow creates or downloads GitHub Release assets.
- Channels distribute Cloth NeXt only, never the external PPF Contact Solver.
