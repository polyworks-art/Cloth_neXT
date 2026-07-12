import zipfile

from tools.check_release_preflight import candidate_version


def test_candidate_version_is_read_from_artifact_not_worktree(tmp_path):
    archive = tmp_path / "candidate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("blender_manifest.toml", 'version = "0.9.0"')
    assert candidate_version(archive) == "0.9.0"
