"""Real Blender registration/operator smoke; never starts a bake or network call."""
import json
import sys
import time
from pathlib import Path
import bpy

args = sys.argv[sys.argv.index('--')+1:]
package, report = map(Path, args)
sys.path.insert(0, str(package))
import cloth_next
cloth_next.register()
from cloth_next.blender import onboarding_manager
from cloth_next.blender.preferences import CLOTHNEXT_AddonPreferences
from cloth_next.polyworks.display import read_status

preferences = bpy.context.preferences.addons.get('cloth_next')
if preferences is None:
    # Direct registration in an isolated factory-startup process doesn't install
    # an add-on entry. Supply just the existing preference adapter, never bypass
    # Companion validation or change seen-state behavior.
    class Preferences:
        onboarding_state = ''
        show_whats_new_after_updates = True
    preferences = Preferences()
    onboarding_manager._preferences = lambda: preferences
else:
    preferences = preferences.preferences

before = preferences.onboarding_state
root = onboarding_manager.account_root()
before_files = set(root.rglob('*')) if root.exists() else set()
display = read_status(root)
assert (set(root.rglob('*')) if root.exists() else set()) == before_files
assert not any('polyworks' in name.lower() for name in CLOTHNEXT_AddonPreferences.bl_rna.properties.keys())
result = bpy.ops.clothnext.manage_polyworks_account('EXEC_DEFAULT')
assert result == {'FINISHED'}, result
deadline = time.monotonic()+15
acknowledged = False
while onboarding_manager._pending and time.monotonic() < deadline:
    for pending in onboarding_manager._pending:
        ready = pending[2]
        if ready.is_file() and ready.read_text() == pending[3]:
            acknowledged = True
    onboarding_manager._poll_startup()
    time.sleep(.05)
assert acknowledged, 'Packaged EXE did not acknowledge its Welcome/account window'
assert not onboarding_manager._pending, 'Companion did not close after isolated auto-close'
assert preferences.onboarding_state == before, 'Manual account opening changed Welcome lifecycle'
payload = dict(result='PASS', actual_blender=bpy.app.version_string,
    actual_operator='clothnext.manage_polyworks_account', exe_ready=acknowledged,
    account_fields_in_blender=False, read_only_display=display, seen_state_unchanged=True)
report.write_text(json.dumps(payload, indent=2))
cloth_next.unregister()
