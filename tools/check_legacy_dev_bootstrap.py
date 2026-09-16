"""Check a candidate Dev feed using the actual previously shipped updater.

Run with an extracted old extension's parent directory and the candidate's
official Blender index. The subprocess imports only the old implementation;
changing INSTALLED_VERSION in the current updater cannot substitute for this.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


PROBE = r'''
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from cloth_next import manifest_version
from cloth_next.updater import addon_updates as model
from cloth_next.updater.addon_versions import parse_version
source = Path(model.__file__).resolve()
assert source.is_relative_to(Path(sys.argv[1]).resolve()), source
installed = parse_version(manifest_version())
payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
available = model.parse_index_versions(payload, model.UpdateChannel.DEV)
decision = model.decide_update(installed, available, model.UpdateChannel.DEV)
assert decision.state is model.AddonUpdateState.UPDATE_AVAILABLE, decision
assert decision.selected_channel == "dev", decision
print(f"SHIPPED_DEV_BOOTSTRAP_ACCEPTED {installed} -> {decision.target_version}")
print(f"Loaded legacy updater: {source}")
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-source", type=Path, required=True)
    parser.add_argument("--candidate-index", type=Path, required=True)
    args = parser.parse_args()
    source = args.legacy_source.resolve()
    index = args.candidate_index.resolve()
    if not (source / "cloth_next" / "blender_manifest.toml").is_file():
        parser.error("legacy source must contain the extracted cloth_next extension")
    if not index.is_file():
        parser.error("candidate index does not exist")
    return subprocess.run(
        [sys.executable, "-I", "-c", PROBE, str(source), str(index)],
        cwd=source, check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
