# Update channels

Cloth NeXt publishes two Blender-compatible remote extension repositories.
Their `index.json` files are generated exclusively by the official Blender
tooling (`blender --command extension server-generate`). Governed by
[RELEASE_POLICY.md](RELEASE_POLICY.md).

## Channels

| Channel | Accepted versions | Repository URL |
|---|---|---|
| stable | `vX.Y.Z` | `https://polyworks-art.github.io/Cloth_neXT/stable/index.json` |
| beta | `vX.Y.Z-beta.N`, `vX.Y.Z-rc.N` | `https://polyworks-art.github.io/Cloth_neXT/beta/index.json` |

GitHub Pages must be configured (repository settings → Pages) to serve the
`gh-pages` branch from its root. The release workflow updates only the channel
directory of the release being published and never removes the other channel.

## Adding a channel in Blender

1. Edit → Preferences → Get Extensions → Repositories → `+` → *Add Remote Repository*.
2. Enter the channel URL above.
3. Blender then lists Cloth NeXt, offers installation, and shows updates when a
   newer version appears in the channel index.

## Rules

- Prereleases never appear in the stable repository.
- The channel ZIP is byte-identical (SHA-256 verified) to the GitHub release asset.
- A GitHub release alone is not an update mechanism; only these repositories are.
- The channels distribute Cloth NeXt only — never the external PPF Contact Solver.
