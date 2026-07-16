# Update channels

Cloth NeXt publishes three Blender-compatible remote extension repositories.
Their `index.json` files are generated exclusively by the official Blender
tooling (`blender --command extension server-generate`). Governed by
[RELEASE_POLICY.md](RELEASE_POLICY.md).

## Channels

| Channel | Active candidate may be | Repository URL |
|---|---|---|
| stable | Stable | `https://polyworks-art.github.io/Cloth_neXT/stable/index.json` |
| beta | Beta or Stable | `https://polyworks-art.github.io/Cloth_neXT/beta/index.json` |
| dev | Dev, Beta, or Stable | `https://polyworks-art.github.io/Cloth_neXT/dev/index.json` |

Visibility is cumulative: Stable releases are published to all repositories,
Beta releases to Beta and Dev, and Dev snapshots only to Dev. Each repository
still exposes exactly one active `cloth_next` candidate, and every copied ZIP is
byte-identical. Therefore users on an experimental channel always receive a
newer Stable release when it supersedes the current Beta/Dev line.

GitHub Pages must be configured (repository settings → Pages) to serve the
`gh-pages` branch from its root. Channel publication is serialized so Stable,
Beta, Dev, and repair workflows cannot race while updating cumulative indices.

## Adding a channel in Blender

Either through Cloth NeXt (preferred):

1. Edit → Preferences → Add-ons → Cloth NeXt.
2. Pick the *Update Channel*. The installed `STABLE.BETA.DEV` positions select
   the matching default channel. Dev still requires Developer
   Tools plus explicit risk acknowledgement.
3. Click *Add Channel Repository* — this registers the channel URL in
   Blender's Get Extensions repositories. Setup happens only on this explicit
   click, never automatically, and never creates a duplicate repository.
4. Click *Check for Updates*; when an update is available, click *Update
   through Blender*. This synchronizes the selected channel repository and
   opens Blender's native extension update view — Cloth NeXt itself never
   downloads, installs, or replaces its own package. Complete the update by
   clicking **Update** on Cloth NeXt in Blender's Get Extensions view, then
   restart Blender when Blender prompts for it.

Update checks and package installation are two separate lifecycles: Cloth
NeXt only reads the channel `index.json` to *report* status; Blender's own
extension manager performs the actual package replacement. Cloth NeXt never
self-replaces while it is running — replacing the active extension from its
own code can crash Blender at native level, which is why no in-add-on
install button exists.

### Dev repository cache repair

The Dev repository exposes exactly one active `cloth_next` candidate: the
newest eligible Dev, Beta, or Stable build. Older retained ZIPs remain downloadable, but are
not repeated as package records in `index.json`; duplicate package IDs make
Blender's displayed and installed candidate ambiguous.

Profiles that synchronized the earlier duplicate-entry Dev index may keep that
metadata in the repository's local `.blender_ext` cache. After the public index
is repaired, refresh the Dev repository in Get Extensions. If Blender still
shows the old version or repeatedly offers the same update, remove and re-add
the Cloth NeXt Dev repository (or remove its local repository cache while
Blender is closed), synchronize again, reinstall Dev 5, and restart Blender.
Changing the public index does not retroactively replace an already cached
repository index.

Or manually:

1. Edit → Preferences → Get Extensions → Repositories → `+` → *Add Remote Repository*.
2. Enter the channel URL above.
3. Blender then lists Cloth NeXt, offers installation, and shows updates when a
   newer version appears in the channel index.

## Rules

- Prereleases never appear in the stable repository.
- Beta repositories accept Stable and Beta candidates; Dev repositories accept
  Stable, Beta, and Dev candidates.
- The channel ZIP is byte-identical (SHA-256 verified) to the GitHub release asset.
- A GitHub release alone is not an update mechanism; only these repositories are.
- The channels distribute Cloth NeXt only — never the external PPF Contact Solver.
