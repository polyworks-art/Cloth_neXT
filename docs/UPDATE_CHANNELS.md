# Update channels

Cloth NeXt publishes three Blender-compatible remote extension repositories.
Their `index.json` files are generated exclusively by the official Blender
tooling (`blender --command extension server-generate`). Governed by
[RELEASE_POLICY.md](RELEASE_POLICY.md).

## Channels

| Channel | Accepted versions | Repository URL |
|---|---|---|
| stable | `vX.Y.Z` | `https://polyworks-art.github.io/Cloth_neXT/stable/index.json` |
| beta | `vX.Y.Z-beta.N`, `vX.Y.Z-rc.N` | `https://polyworks-art.github.io/Cloth_neXT/beta/index.json` |
| dev | `X.Y.Z-dev.N` (no tag) | `https://polyworks-art.github.io/Cloth_neXT/dev/index.json` |

GitHub Pages must be configured (repository settings → Pages) to serve the
`gh-pages` branch from its root. The release workflow updates only the channel
directory of the release being published and never removes the other channel.

## Adding a channel in Blender

Either through Cloth NeXt (preferred):

1. Edit → Preferences → Add-ons → Cloth NeXt.
2. Pick the *Update Channel* (Stable or Beta; Beta is preselected while a
   prerelease is installed). Dev is never automatic and requires Developer
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

Or manually:

1. Edit → Preferences → Get Extensions → Repositories → `+` → *Add Remote Repository*.
2. Enter the channel URL above.
3. Blender then lists Cloth NeXt, offers installation, and shows updates when a
   newer version appears in the channel index.

## Rules

- Prereleases never appear in the stable repository.
- The channel ZIP is byte-identical (SHA-256 verified) to the GitHub release asset.
- A GitHub release alone is not an update mechanism; only these repositories are.
- The channels distribute Cloth NeXt only — never the external PPF Contact Solver.
