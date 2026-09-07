# Channel migration implementation report

Implemented on 2026-09-07. No release, tag, version change, solver change or
publication was performed. The existing manifest remains 2.4.3; 2.3.5 is used
only as a requested regression example, not as a newly selected release version.

## Architecture and migration

URL-based repository lookup and per-channel repository creation were replaced
by `find_owning_repo`. For `bl_ext.<module>.cloth_next`, exactly one matching
Blender RNA repository module is required. A source/legacy fallback requires the
actual package directory to match `<repo.directory>/cloth_next`; a URL match
alone is never sufficient. Ambiguous/missing owners fail visibly.

A deferred startup callback and channel-property callbacks configure the saved
selected feed, sync that same repository directory through Blender, and inspect
its cached `.blender_ext/index.json`. Only `remote_url` is changed. Module,
directory, installed files, namespace, preferences and license state are retained.
Other legacy repository entries are neither removed nor used as substitutes.

Migration completion is a session marker set only after successful synchronization
and valid target inspection. It is recomputed on every startup rather than
permanently suppressing future validation. Partial configuration and failed syncs
remain retryable through Check for Updates or next startup. No package files are
written by production updater code.

## Decision and UI

`UpdateDecision` tracks installed/selected/target channels and versions,
`same_artifact`, `channel_changed`, version relationship and action state.
All six cross-channel transitions are actionable, including lower versions.
Identical versions are up to date; same-channel newer targets update. Invalid,
wrong-level, duplicate or missing candidates cannot initiate a handoff. Poll and
post-sync validation use the canonical decision model. Changing selection clears
stale actions. Preferences show installed version/channel, selection, available
target and Update/Switch action.

Dev remains public and experimental, with explicit risk acknowledgement when
entering it. Existing Dev installations migrate without a new acknowledgement.
The checked-in runtime already had no Developer Tools prerequisite; the obsolete
requirement in UPDATE_CHANNELS.md was removed and tests explicitly exercise Dev
selection, checks and handoffs with `developer_tools=False`. Unrelated diagnostic
Developer Tools functionality remains intact.

## Release infrastructure

Each publication updates only its own channel. Index policy requires exactly one
candidate of that level, classified by `AddonVersion.channel_name`; release
validation delegates classification to that same pure model. Historical suffix
compatibility remains readable. Stable/Beta/Dev URLs remain unchanged. Historical
archives, including former cumulative copies, stay immutable and addressable;
Dev archive pruning was removed. Official Blender index generation remains in use.

Bridge publication will require appropriate numeric builds for each release level;
this implementation deliberately does not publish a Dev version into Stable/Beta.

## Files changed

Runtime:
- cloth_next/updater/addon_updates.py
- cloth_next/updater/channel_policy.py
- cloth_next/blender/addon_update_operators.py
- cloth_next/blender/preferences.py
- cloth_next/blender/physics_ui.py
- cloth_next/blender/registration.py

Release tooling:
- tools/validate_release_policy.py
- tools/build_extension_repository.py
- .github/workflows/release.yml
- .github/workflows/publish-dev.yml

Tests and integration:
- tests/test_channel_migration.py (new)
- tests/test_addon_update_ui.py
- tests/test_addon_updates.py
- tests/test_channel_policy.py
- tests/test_dev_channel.py
- tests/test_release_policy.py
- tools/blender_update_smoke_test.py
- tools/run_blender_channel_repository_regression.py (new)

Documentation:
- docs/RELEASE_POLICY.md
- docs/UPDATE_CHANNELS.md
- docs/CHANNEL_MIGRATION_IMPLEMENTATION.md (this report)

## Validation commands and results

Commands ran with Python 3.11 and Blender 5.2.1 LTS on Windows.

```text
python -m pytest -q --tb=short
python tools/validate_extension.py cloth_next --phase source
```

The original workspace contains pre-existing ignored generated companion artifacts,
so source validation there rejects the source directory. Those files were preserved.
For a clean validation, tracked files were copied from the current working tree
(including edits), along with the new migration tests and native regression script,
into a temporary directory. `git init -q` and `git add .` supplied the Git metadata
required by the existing tracking test. The two commands above then passed:
source validation passed; **1730 passed, 10 skipped, 3 deselected**. The skipped
tests require an external PPF solver; the deselected tests require a supplied
production release artifact.

After the final poll guard and four additional tests:

```text
python -m pytest tests/test_channel_migration.py tests/test_addon_update_ui.py tests/test_addon_updates.py tests/test_channel_policy.py tests/test_dev_channel.py tests/test_release_policy.py tests/test_update_selfinstall_policy.py -q --tb=short
```

Result: **151 passed**. Includes classification, all switch directions, lower/equal/
newer relationships, namespace/path ownership, duplicates, repeated and partial
migration, invalid/unavailable feeds, retained preferences/files, Dev access without
Developer Tools, native handoff guards and self-install prohibition.

Additional packaging/release checks ran through:

```text
python -m pytest tests/test_addon_versions.py tests/test_addon_updates.py tests/test_channel_policy.py tests/test_release_policy.py tests/test_release_preflight.py tests/test_extension_build.py tests/test_package_structure.py tests/test_dev_channel.py tests/test_update_selfinstall_policy.py -q --tb=short
python -m tools.run_blender_smoke
python tools/run_blender_channel_repository_regression.py
```

The packaging selection initially hit the same generated-source artifact issue;
all those tests subsequently passed in the clean full-suite run. Registration
smoke passed. The native regression generated three indexes using official Blender
server-generate, served fixture archives over loopback HTTP, synchronized them and
performed all six actual native package replacements in one isolated repository.
All passed, including Dev 2.3.5 to Stable 2.0.0 and Beta 2.3.0. Fixture packages are
disposable; the real Cloth NeXt installation and solver are not modified.

The real updater/RNA handoff smoke also passed, including all six directions,
repository identity retention and failure paths, with the synchronization/UI
boundary recorded instead of using public network services. It ran in an isolated
`BLENDER_USER_RESOURCES` directory with this command:

```text
"C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe" --factory-startup --background --online-mode --python-exit-code 1 --python tools/blender_update_smoke_test.py
```

Final static checks passed:

```text
python -m ruff check cloth_next/updater/addon_updates.py cloth_next/updater/channel_policy.py cloth_next/blender/addon_update_operators.py tools/build_extension_repository.py tools/validate_release_policy.py tools/run_blender_channel_repository_regression.py tools/blender_update_smoke_test.py tests/test_channel_migration.py tests/test_addon_update_ui.py tests/test_addon_updates.py tests/test_channel_policy.py tests/test_release_policy.py
git diff --check
```

An exploratory wider Ruff invocation also reported pre-existing E701/E702 formatting
in tests/test_dev_channel.py; that unrelated formatting was left unchanged.

## Limits

- Real Blender validation used 5.2.1; older supported Blender versions were not run.
- Public feeds were not changed or contacted by the integration regression.
- Real replacement testing uses disposable fixture packages, while the actual
  Cloth NeXt operator is tested through its safe native handoff boundary.
- Blender synchronization executes through its synchronous supported operator on
  the main thread and can temporarily block the UI during network access.
- No production release ZIP was built or published; source/packaging tests and
  official Blender fixture repository generation were validated instead.
