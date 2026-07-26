from types import SimpleNamespace

from cloth_next import export_identity


def _object(identity="", role="CLOTH"):
    return SimpleNamespace(cloth_next=SimpleNamespace(
        persistent_export_id=identity, role=role))


def test_export_uuid_ignores_name_and_is_stable():
    identity = "0123456789abcdef"
    first = export_identity.export_uuid_from_identity(identity, "CLOTH")
    assert first == export_identity.export_uuid_from_identity(identity, "CLOTH")
    assert first != export_identity.export_uuid_from_identity(
        identity, "COLLIDER")


def test_duplicate_persistent_ids_are_resolved(monkeypatch):
    values = iter(("new-id",))
    monkeypatch.setattr(export_identity, "new_persistent_id",
                        lambda: next(values))
    first = _object("copied-id")
    duplicate = _object("copied-id")
    result = export_identity.ensure_unique_persistent_ids((first, duplicate))
    assert result == ((first, "copied-id"), (duplicate, "new-id"))
