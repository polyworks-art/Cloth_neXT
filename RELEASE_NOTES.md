# Cloth NeXt 2.4.3 Dev

Cloth NeXt 2.4.3 fixes Rebake startup after changing the cache output folder.

## Bake reliability

- A recorded Cloth NeXt cache can remain in its previous output folder while
  the next Bake writes to a new destination.
- Missing old cache files and cancelled live previews no longer block this
  folder change with "previous cache could not be removed".
- Unrecognized previous mesh-cache paths report an authentication failure
  instead of claiming that deletion failed.
- Old output folders stay untouched. Playback rollback and authenticated
  Recovery partials retain their existing safeguards.

## Release scope

2.4.3 is a Dev-channel release. The external PPF Contact Solver is unchanged,
separately installed, and never bundled. Beta and Stable remain unchanged.
