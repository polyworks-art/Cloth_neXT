# SPDX-License-Identifier: GPL-3.0-or-later
"""Release source audit: every functional Claude change through 2.9.32 is included."""
from pathlib import Path
import subprocess
import hashlib
import json

root=Path(__file__).resolve().parents[1]
reference='origin/claude/project-thread-4kzutd'
files=['cloth_next/polyworks/'+name for name in ('__init__.py','api.py','ed25519.py','entitlements.py','gaia_downloads.py','messages.py','service.py','state.py','status.py','storage.py','trust.py','vault.py')]
files += ['cloth_next/updater/engines.py','cloth_next/updater/gaia_install.py','cloth_next/blender/account_status.py','cloth_next/blender/gaia_account.py','cloth_next/blender/solver_preferences_ui.py','cloth_next/blender/solver_release_naming.py','companion/account_window.py']
rows=[]
for name in files:
    expected=subprocess.check_output(['git','show',reference+':'+name],cwd=root).replace(b'\r\n',b'\n')
    actual=(root/name).read_bytes().replace(b'\r\n',b'\n')
    assert actual==expected, name+' diverges from the Claude release source'
    rows.append({'path':name,'sha256_lf':hashlib.sha256(actual).hexdigest()})
output=root/'docs/validation/sewing/claude-source-audit-2.9.33.json'
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps({'reference_commit':subprocess.check_output(['git','rev-parse',reference],cwd=root,text=True).strip(),'files':rows},indent=2)+'\n',encoding='utf-8')
print(f'{len(rows)} Claude account/solver files verified byte-for-byte (normalized line endings)')
