# Cloth NeXt 2.4.4 Dev

Cloth NeXt 2.4.4 makes release-channel switching work through the existing
Blender repository, including switches to a lower Stable or Beta version.

## Update channels

- Stable, Beta and Dev reuse the repository owning the installed extension.
- Existing installations migrate automatically while keeping their repository
  directory, module, preferences and license state.
- Cross-channel downgrades now offer a Switch action; same-channel newer builds
  offer Update. Blender's Extension Manager still completes installation.
- Dev remains experimental and available without Developer Tools.
- Failed checks can be retried without replacing installed files.
- ZIP installations connect their existing local repository to updates automatically.
- The first update check waits for Welcome or What's New to finish starting;
  clearer next-step text guides the handoff to Blender's Update button.
- Release feeds expose one exact target for their own channel and retain
  immutable historical archives.

## Release scope

2.4.4 is a Dev-channel release. Beta and Stable feeds are unchanged by this
publication. The external PPF Contact Solver is unchanged, separately installed,
and never bundled.
