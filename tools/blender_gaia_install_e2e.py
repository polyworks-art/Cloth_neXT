# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Real Blender: connect, check GAIA access, download, verify and install GAIA.

Run inside Blender with the installed extension and an isolated profile:

    blender --background --factory-startup --python tools/blender_gaia_install_e2e.py -- \
        --module bl_ext.user_default.cloth_next --report <json> [--expect-denied]

The API, download host and solver root come from CLOTH_NEXT_POLYWORKS_API_URL,
CLOTH_NEXT_GAIA_DOWNLOAD_HOST and LOCALAPPDATA (all pointed at local test
locations by the caller). Uses the real health check of the packaged server.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
import time
import webbrowser

import addon_utils


def _wait(module):
    worker = module._state["worker"]
    if worker is not None:
        worker.join(timeout=1800)
    return module.snapshot()


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expect-denied", action="store_true")
    args = parser.parse_args(argv)
    report = {"steps": []}
    webbrowser.open = lambda url, *a, **k: report["steps"].append({"browser_opened": url.split("?")[0]})
    addon_utils.enable(args.module, default_set=True)
    account = importlib.import_module(f"{args.module}.blender.gaia_account")
    engines = importlib.import_module(f"{args.module}.updater.engines")
    install_paths = importlib.import_module(f"{args.module}.updater.install_paths")
    registry_module = importlib.import_module(f"{args.module}.updater.solver_registry")

    def step(name, start):
        began = time.time()
        assert start(), f"{name} did not start: {account.snapshot()}"
        if name == "connect":
            # Background Blender has no timer loop: forward the browser URL ourselves.
            while account.busy() and account._state["browser"] is None:
                time.sleep(0.1)
            if account._state["browser"]:
                webbrowser.open(account._state["browser"])
                account._state["browser"] = None
        state = _wait(account)
        entry = {"step": name, "seconds": round(time.time() - began, 1), "error": state.error,
                 "message": state.message, "access": state.access, "signed_in": state.signed_in}
        report["steps"].append(entry)
        print(json.dumps(entry), flush=True)
        return state

    state = step("connect", account.connect)
    ok = bool(state.signed_in)
    if args.expect_denied:
        ok = ok and state.access != "granted"
        state = step("install-denied", account.install)
        ok = ok and "GAIA access" in state.error
        state = step("request-access", account.request_access)
        ok = ok and state.access == "pending"
    else:
        ok = ok and state.access == "granted" and state.available is not None
        state = step("install", account.install)
        paths = install_paths.ManagedSolverPaths.default()
        registry = registry_module.load_registry(paths.registry_json)
        gaia = engines.installations_for(registry, engines.GAIA)
        report["registry"] = {"selected": registry.selected_installation_id,
                              "gaia": [item.installation_id for item in gaia],
                              "healthy": [item.healthy for item in gaia]}
        ok = ok and not state.error and len(gaia) == 1 and gaia[0].healthy
        if ok:
            problem = engines.verify_gaia_files(gaia[0].root, engines.gaia_build_of(gaia[0]))
            report["verify"] = problem or "ok"
            ok = problem is None
        state = step("reinstall-idempotent", account.install)
        ok = ok and "already installed" in state.message
    report["result"] = "PASS" if ok else "FAIL"
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"result": report["result"]}), flush=True)
    addon_utils.disable(args.module)
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.exit(code)
