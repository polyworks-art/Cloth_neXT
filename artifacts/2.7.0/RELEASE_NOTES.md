# Cloth NeXt 2.7.0

Quick Assign adds a floating Add bubble above the existing simulation toolbar.
Hold and drag to choose among Cloth, Cable, Rigid Body, Soft Body and Collider.
The selected bubble and its labeled radial sector are blue. Releasing assigns
an eligible role; moving back to the center, outside, Escape or right-click cancels.
Errors and other messages appear to the right of Bake, outside the toolbar pill.

The Update Channel selector and Dev acknowledgement are removed from Preferences.
Updates use the installation's owning native Blender repository, without changing
its URL or applying Stable/Beta/Dev filters. Normal customers update through
Superhive; no custom authentication is introduced.

Private GitHub releases with any version greater than 2.6.0 share one configured
repository. Its URL exists only in the publisher's GitHub Actions secret and is
not included in source or in the extension ZIP. The old GitHub Beta migration
bridge remains at 2.6.0; existing legacy feed contents are preserved.

The external PPF Contact Solver is not bundled. The release includes the existing
Companion and exact-version What's New resources.

Preferences show a Superhive status section instead of update controls:
`Superhive connected` means an enabled official Superhive repository is configured;
`Purchase validated` means that repository's locally cached Blender index contains
`cloth_next`. These are informational states only. They read no access tokens,
perform no network request in drawing, and never enable or disable features.
