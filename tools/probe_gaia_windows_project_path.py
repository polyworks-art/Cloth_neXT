# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Windows project-alias probe, without starting or patching a solver.

Only fresh, authenticated CNX probe targets and aliases are touched. Existing
projects are never adopted. Keep the JSON report when comparing execution from
Codex with execution in an ordinary Windows PowerShell terminal.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.ppf.project_links import ProjectLink, canonical_project_path
from cloth_next.core.safe_delete import delete_owned


def probe(executable, report):
    if os.name != 'nt':
        raise RuntimeError('This probe requires Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    kernel.CreateDirectoryW.restype = ctypes.c_int
    owned = Path(tempfile.mkdtemp(prefix='cnx-alias-probe-')).resolve()
    canonical = canonical_project_path(executable.resolve(), 'placeholder')
    vendor = Path(os.environ['LOCALAPPDATA']) / 'ClothNeXt'
    parents = [owned / 'control-aliases', vendor.parent, vendor, canonical.parent]
    rows = []
    for parent in parents:
        name = 'cnxprobe_' + uuid.uuid4().hex[:12]
        link = ProjectLink(parent / name, owned / name, owned_root=owned, project_name=name)
        row = {'alias_parent': str(parent), 'alias': str(link.path),
               'target': str(link.target), 'tests': {}}
        try:
            link.ensure()
            for kind in ('file', 'python_mkdir', 'win32_mkdir', 'resolved_mkdir'):
                child = link.path / kind
                try:
                    if kind == 'file':
                        child.write_bytes(b'cnx probe')
                    elif kind == 'win32_mkdir':
                        ctypes.set_last_error(0)
                        if not kernel.CreateDirectoryW(str(child), None):
                            raise ctypes.WinError(ctypes.get_last_error())
                    else:
                        os.mkdir(child.resolve() if kind == 'resolved_mkdir' else child)
                    row['tests'][kind] = {'result': 'PASS', 'physical_exists': (link.target / kind).exists()}
                except OSError as exc:
                    row['tests'][kind] = {'result': 'FAIL', 'winerror': getattr(exc, 'winerror', None),
                                          'errno': exc.errno, 'error': str(exc),
                                          'physical_exists': (link.target / kind).exists()}
        except Exception as exc:
            row['setup_error'] = repr(exc)
        finally:
            # ProjectLink's durable record authenticates precisely this target.
            if link.marker.is_file():
                link.validate_target_ownership()
                link.remove()
                outcome = delete_owned(link.target, root=owned, ownership_authenticated=True,
                                       recursive=True, lifecycle_stage='ALIAS_PATH_PROBE',
                                       artifact_type='probe')
                row['cleanup_success'] = outcome.success
            rows.append(row)
    result = {'python': sys.executable, 'solver': str(executable.resolve()),
              'cwd': str(Path.cwd()), 'owned_probe_root': str(owned), 'rows': rows}
    report.parent.mkdir(parents=True, exist_ok=True)
    # Do not overwrite an earlier comparison report.
    with report.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    probe(args.solver, args.report)
