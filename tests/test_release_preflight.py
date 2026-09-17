import zipfile

from tools.check_release_preflight import candidate_version


def test_candidate_version_is_read_from_artifact_not_worktree(tmp_path):
    archive = tmp_path / "candidate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("blender_manifest.toml", 'version = "0.9.0"')
    assert candidate_version(archive) == "0.9.0"


def test_matching_preflight_reuses_tested_bytes(tmp_path, monkeypatch):
    import json
    import subprocess
    from tools.check_release_preflight import matching_run
    from types import SimpleNamespace
    commit = "a" * 40
    archive_bytes = None

    def gh(command, **kwargs):
        nonlocal archive_bytes
        if command[1:3] == ["run", "list"]:
            return SimpleNamespace(stdout=json.dumps([
                {"databaseId": 1, "headSha": "b" * 40, "conclusion": "success"},
                {"databaseId": 2, "headSha": commit, "conclusion": "success"}]))
        assert command[1:4] == ["run", "download", "2"]
        from pathlib import Path
        archive = Path(command[-1]) / "cloth_next-2.6.0-windows-x64.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("blender_manifest.toml", 'version = "2.6.0"')
        archive_bytes = archive.read_bytes()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", gh)
    output = tmp_path / "output"
    assert matching_run(commit, "2.6.0", output) == 2
    assert (output / "cloth_next-2.6.0-windows-x64.zip").read_bytes() == archive_bytes
