# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run installed-extension onboarding persistence scenarios in real Blender."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("fresh", "same", "update"),
                        required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(values)


def main() -> None:
    args = _args()
    from bl_ext.user_default.cloth_next import manifest_version
    from bl_ext.user_default.cloth_next.blender import onboarding_manager
    from bl_ext.user_default.cloth_next.onboarding import SeenState

    version = manifest_version()
    preferences = onboarding_manager._preferences()
    onboarding_manager.unregister()
    if args.scenario == "fresh":
        preferences.onboarding_state = ""
        expected = "welcome"
    elif args.scenario == "update":
        preferences.onboarding_state = SeenState(
            True, ("2.3.4",), "2.3.4").to_json()
        expected = "whats-new"
    else:
        expected = None

    before = SeenState.from_json(preferences.onboarding_state)
    assert before.next_screen(version) == expected
    os.environ["CLOTH_NEXT_COMPANION_AUTO_CLOSE_MS"] = "350"
    if expected:
        ok, message = onboarding_manager.launch_screen(expected, manual=False)
        assert ok, message
        deadline = time.monotonic() + 20
        while onboarding_manager._pending and time.monotonic() < deadline:
            onboarding_manager._poll_startup()
            time.sleep(0.1)
    else:
        onboarding_manager._startup_pulse()
    after = SeenState.from_json(preferences.onboarding_state)
    assert after.next_screen(version) is None
    if args.scenario == "same":
        assert before == after
    bpy.ops.wm.save_userpref()
    args.report.write_text(json.dumps({
        "blender": bpy.app.version_string,
        "scenario": args.scenario,
        "expected": expected,
        "version": version,
        "state": json.loads(after.to_json()),
    }, indent=2), encoding="utf-8")
    print(f"CLOTH_NEXT_ONBOARDING_PASS {args.scenario} {version}")


main()
