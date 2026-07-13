# Security Across All Release Channels

Every **Cloth NeXt** build follows the same security and distribution rules, whether it is published through the **Dev**, **Beta**, or **Stable** channel.

> [!WARNING]
> Dev builds are experimental and unsupported, but they are never exempt from security requirements.

## Every Release Is Checked For

- source-code secrets
- packaged-file secrets
- companion application identity
- companion application hashes
- Blender extension package validity
- forbidden artifacts
- unintended local development files

## No Release May Contain

- PPF Contact Solver binaries or redistributed solver files
- simulation caches
- `.blend` files
- `.pc2` files
- logs
- credentials
- API tokens
- private keys
- local file-system paths
- environment dumps
- developer-specific configuration files

## Same Rules for Every Channel

| Channel | Purpose | Security requirements |
|---|---|---|
| **Dev** | Experimental development snapshots | Full validation required |
| **Beta** | Public testing builds | Full validation required |
| **Stable** | Production-ready releases | Full validation required |

All published packages must pass the required security, packaging, and distribution checks before they are added to a public release channel.
