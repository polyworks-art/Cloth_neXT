from pathlib import Path
import json
import os

import pytest

from cloth_next.core.errors import ClothNextError
from cloth_next.ppf import project_links as links


def project(tmp_path, generation="new"):
    name = "clothnext_test"
    root = tmp_path / "authenticated-run" / "server-data"
    return links.ProjectLink(tmp_path / "official" / name,
                             root / generation / name,
                             owned_root=root, project_name=name)


def test_absent_repeated_ensure_and_cleanup_preserve_target(tmp_path):
    link = project(tmp_path)
    link.ensure()
    assert links.inspect_project_link(link.path).kind in {"junction", "symlink"}
    identity = link.path.lstat().st_ino
    (link.target / "child").mkdir()
    (link.target / "child" / "keep").write_text("keep")
    link.ensure()
    assert link.path.lstat().st_ino == identity
    assert json.loads(link.marker.read_text())["phase"] == "COMMITTED"
    link.remove(); link.remove()
    assert not os.path.lexists(link.path)
    assert (link.target / "child" / "keep").read_text() == "keep"


@pytest.mark.parametrize("kind", ["file", "directory", "empty-directory", "unknown-link", "broken-link"])
def test_unknown_collisions_fail_closed(tmp_path, kind):
    link = project(tmp_path)
    link.path.parent.mkdir(parents=True)
    foreign = tmp_path / "foreign"
    if kind == "file":
        link.path.write_text("user")
    elif kind in {"directory", "empty-directory"}:
        link.path.mkdir()
        if kind == "directory":
            (link.path / "keep").write_text("user")
    else:
        foreign.mkdir()
        links._create_link(link.path, foreign)
        if kind == "broken-link":
            foreign.rmdir()
    before = link.path.lstat()
    for operation in (link.ensure, link.remove):
        with pytest.raises(ClothNextError) as failure:
            operation()
        assert failure.value.record.user_message
    assert link.path.lstat().st_ino == before.st_ino
    assert not link.marker.exists()


def test_owned_broken_expected_alias_recreates_target_without_recreating_alias(tmp_path):
    link = project(tmp_path)
    link.ensure()
    identity = link.path.lstat().st_ino
    link.target.rmdir()
    assert not link.path.exists() and os.path.lexists(link.path)
    link.ensure(); link.ensure()
    assert link.target.is_dir() and link.path.lstat().st_ino == identity


def test_owned_stale_alias_replacement_preserves_both_targets(tmp_path):
    old, new = project(tmp_path, "old"), project(tmp_path)
    old.ensure()
    (old.target / "keep").write_text("old")
    new.ensure(); new.ensure()
    assert new.path.resolve() == new.target
    assert (old.target / "keep").read_text() == "old"
    assert not list(new.path.parent.glob(".cnx-link-*"))


def test_interrupted_replacement_is_reentrant(tmp_path, monkeypatch):
    old, new = project(tmp_path, "old"), project(tmp_path)
    old.ensure()
    original = os.replace
    def interrupt(source, destination):
        if str(source).endswith(".temporary") and Path(destination) == new.path:
            raise OSError("injected interruption after backup rename")
        return original(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(links.os, "replace", interrupt)
        with pytest.raises(ClothNextError):
            new.ensure()
    assert not os.path.lexists(new.path)
    assert json.loads(new.marker.read_text())["phase"] == "PREPARING"
    new.ensure(); new.ensure()
    assert new.path.resolve() == new.target
    assert not list(new.path.parent.glob(".cnx-link-*"))


def test_wrong_target_is_not_adopted_even_with_valid_marker(tmp_path):
    link = project(tmp_path)
    link.ensure()
    link._unlink(link.path, str(link.target))
    foreign = tmp_path / "user-data"
    foreign.mkdir(); (foreign / "keep").write_text("user")
    links._create_link(link.path, foreign)
    for operation in (link.ensure, link.remove):
        with pytest.raises(ClothNextError) as failure: operation()
        assert dict(failure.value.record.context)["ownership_proven"] == "False"
    assert link.path.resolve() == foreign and (foreign / "keep").read_text() == "user"


def test_recreated_artifact_after_cleanup_is_unknown(tmp_path):
    link = project(tmp_path)
    link.ensure(); link.remove()
    links._create_link(link.path, link.target)
    with pytest.raises(ClothNextError): link.remove()
    with pytest.raises(ClothNextError): link.ensure()


def test_corrupt_metadata_fails_closed(tmp_path):
    link = project(tmp_path)
    link.ensure()
    link.marker.write_text("[]")
    with pytest.raises(ClothNextError): link.ensure()
    assert link.path.resolve() == link.target


def test_concurrent_authority_is_rejected_not_raced(tmp_path):
    link = project(tmp_path)
    with link._lock():
        with pytest.raises(ClothNextError): link.ensure()
    assert not link.marker.exists()


def test_target_outside_authenticated_root_rejected(tmp_path):
    with pytest.raises(ValueError):
        links.ProjectLink(tmp_path / "official" / "project",
                          tmp_path / "foreign" / "project",
                          owned_root=tmp_path / "owned", project_name="project")


def test_session_cleanup_is_reentrant_after_process_join(tmp_path, monkeypatch):
    from cloth_next.ppf_run import session as module
    link = project(tmp_path)
    link.ensure()
    worker = module.SolverSession.__new__(module.SolverSession)
    worker._manager = object()
    worker._project_link = link
    worker._health_project_link = None
    worker._delete_link_after_join = True
    worker._recovery_record = None
    worker._recovery = None
    with pytest.raises(ClothNextError): worker._cleanup_project_link()
    assert os.path.lexists(link.path)
    worker._manager = None
    original = module.delete_owned
    with monkeypatch.context() as patch:
        patch.setattr(module, "delete_owned", lambda *a, **k: (_ for _ in ()).throw(OSError("injected cleanup failure")))
        with pytest.raises(OSError): worker._cleanup_project_link()
    assert worker._delete_link_after_join and link.target.is_dir()
    worker._stop_owned(); worker._stop_owned()
    assert not worker._delete_link_after_join
    assert not os.path.lexists(link.path) and not link.target.exists()


def test_marker_cannot_authorize_outside_transaction_paths(tmp_path):
    link = project(tmp_path)
    link.ensure()
    value = json.loads(link.marker.read_text())
    value["backup"] = str(tmp_path / "user-data")
    link.marker.write_text(json.dumps(value))
    with pytest.raises(ClothNextError): link.ensure()
    with pytest.raises(ClothNextError): link.remove()
    assert link.path.resolve() == link.target


def test_recovery_cleanup_uses_same_alias_authority(tmp_path):
    link = project(tmp_path)
    link.ensure()
    links.remove_link_for_owned_target(link.root, link.target, link.name, required=True)
    links.remove_link_for_owned_target(link.root, link.target, link.name, required=True)
    assert link.target.is_dir() and not os.path.lexists(link.path)


def test_recovery_missing_ownership_fails_closed_for_linked_generation(tmp_path):
    link = project(tmp_path)
    with pytest.raises(ClothNextError):
        links.remove_link_for_owned_target(link.root, link.target, link.name, required=True)
    links.remove_link_for_owned_target(link.root, link.target, link.name, required=False)


def test_unexpected_reparse_fails_closed(tmp_path, monkeypatch):
    link = project(tmp_path)
    original = links.inspect_project_link
    monkeypatch.setattr(links, "inspect_project_link",
                        lambda path: links.LinkState("unexpected-reparse", tag=0xA0000099)
                        if path == link.path else original(path))
    with pytest.raises(ClothNextError): link.ensure()
    assert not link.marker.exists() and not link.target.exists()


def test_hardlinked_metadata_is_not_ownership_proof(tmp_path):
    link = project(tmp_path)
    link.ensure()
    os.link(link.marker, tmp_path / "foreign-marker-link")
    with pytest.raises(ClothNextError): link.ensure()
    with pytest.raises(ClothNextError): link.remove()
    assert link.path.resolve() == link.target


def test_native_directory_symlink_removal_preserves_target(tmp_path):
    link = project(tmp_path)
    link.ensure()
    link._unlink(link.path, str(link.target))
    link.path.symlink_to(link.target, target_is_directory=True)
    (link.target / "keep").write_text("owned target remains")
    assert links.inspect_project_link(link.path).kind == "symlink"
    link.ensure(); link.remove()
    assert (link.target / "keep").read_text() == "owned target remains"
    assert not os.path.lexists(link.path)


def test_interruption_after_promotion_reconciles_metadata_and_backup(tmp_path, monkeypatch):
    old, new = project(tmp_path, "old"), project(tmp_path)
    old.ensure()
    (old.target / "keep").write_text("old")
    original = links.ProjectLink._unlink
    def interrupt(self, path, target):
        if str(path).endswith(".backup"):
            raise OSError("injected interruption after replacement promotion")
        return original(self, path, target)
    with monkeypatch.context() as patch:
        patch.setattr(links.ProjectLink, "_unlink", interrupt)
        with pytest.raises(ClothNextError): new.ensure()
    assert new.path.resolve() == new.target
    assert json.loads(new.marker.read_text())["phase"] == "PREPARING"
    new.ensure(); new.ensure()
    assert json.loads(new.marker.read_text())["phase"] == "COMMITTED"
    assert (old.target / "keep").read_text() == "old"
    assert not list(new.path.parent.glob(".cnx-link-*"))


def test_tcmd_does_not_compete_with_cnx_alias_deletion(tmp_path):
    from cloth_next.ppf_run import session as module
    link = project(tmp_path)
    link.ensure()
    worker = module.SolverSession.__new__(module.SolverSession)
    worker._address = object()
    worker._project_link = link
    worker._delete_link_after_join = False
    commands = []
    worker._request = commands.append
    worker._delete_project()
    assert commands == [module.wire.REQUEST_TERMINATE]
    assert worker._delete_link_after_join and os.path.lexists(link.path)


def test_same_project_id_cannot_cross_solver_alias_identity(tmp_path):
    old = project(tmp_path)
    old.ensure()
    new = links.ProjectLink(tmp_path / "other-solver" / old.name,
                            old.target, owned_root=old.root, project_name=old.name)
    with pytest.raises(ClothNextError): new.ensure()
    assert old.path.resolve() == old.target and not os.path.lexists(new.path)


def test_failed_start_never_authorizes_unknown_target_cleanup(tmp_path):
    from cloth_next.ppf_run.session import SolverSession
    link = project(tmp_path)
    link.target.mkdir(parents=True)
    (link.target / "user-data").write_text("preserve")
    with pytest.raises(ClothNextError): link.ensure()
    with pytest.raises(ClothNextError): SolverSession._cleanup_link_target(link)
    assert (link.target / "user-data").read_text() == "preserve"


def test_cleanup_of_partial_transaction_does_not_create_alias(tmp_path, monkeypatch):
    link = project(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(links, "_create_link", lambda *a: (_ for _ in ()).throw(OSError("injected creation failure")))
        with pytest.raises(ClothNextError): link.ensure()
        assert json.loads(link.marker.read_text())["phase"] == "PREPARING"
        link.remove(); link.remove()
    assert json.loads(link.marker.read_text())["phase"] == "REMOVED"
    assert not os.path.lexists(link.path) and link.target.is_dir()


def test_long_path_io_keeps_durable_identity_unprefixed(tmp_path):
    owned = tmp_path / ("a" * 100) / ("b" * 100)
    link = links.ProjectLink(tmp_path / "aliases" / "longpath", owned / "longpath",
                             owned_root=owned, project_name="longpath")
    link.ensure(); link.ensure()
    record = json.loads(links._io_path(link.marker).read_text())
    assert record["root"] == str(owned)
    assert record["target"] == str(owned / "longpath")
    assert links.inspect_project_link(link.path).target == record["target"]
    link.remove(); link.remove(); link.validate_target_ownership()
    assert links._io_path(link.target).is_dir()
