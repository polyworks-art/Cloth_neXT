# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Blender projection. Contains no credential-vault/account operations."""
import json
from pathlib import Path


def read_status(root):
    result = dict(signed_in=False, email='', tier='Not activated',
                  verification='PolyWorks accounts — Coming Soon', devices=None)
    if root is None:
        return result
    try:
        path = Path(root)/'display-status.json'
        if path.stat().st_size > 65536:
            return result
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('schema') != 'polyworks.display.v1':
            return result
        result['signed_in'] = value.get('signed_in') is True
        for name in ('email', 'tier', 'verification'):
            if isinstance(value.get(name), str):
                result[name] = ''.join(c for c in value[name][:256] if c.isprintable())
        if isinstance(value.get('devices'), list):
            result['devices'] = value['devices'][:100]
        return result
    except (OSError, ValueError):
        return result


def publish_status(store, client, *, devices=None):
    """Companion-only writer. Display cache is never authoritative licensing proof."""
    from .storage import atomic_write
    status = client.status()
    # Only the device count is displayed; don't persist arbitrary API fields.
    status.update(schema='polyworks.display.v1', devices=[{} for _ in devices] if devices is not None else None)
    # Explicit allowlist: credentials must never reach Blender or this file.
    public = {k: status[k] for k in ('schema', 'signed_in', 'email', 'tier', 'verification', 'devices')}
    atomic_write(store.root/'display-status.json', json.dumps(public))
    return public
