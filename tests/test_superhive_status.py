from types import SimpleNamespace
import json
import pytest
from cloth_next.superhive_status import repository_status, SuperhiveStatus


def repository(tmp_path, url="https://superhivemarket.com/native/index.json", **kwargs):
    return SimpleNamespace(remote_url=url, enabled=True, use_remote_url=True,
                           directory=str(tmp_path), **kwargs)


def write_index(tmp_path, data):
    path = tmp_path / ".blender_ext/index.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"data": data}), encoding="utf-8")
    return path


def test_connection_and_purchase_are_independent(tmp_path):
    repo = repository(tmp_path)
    assert repository_status([]) == SuperhiveStatus(False, False)
    assert repository_status([repo]) == SuperhiveStatus(True, False)
    index = write_index(tmp_path, [{"id": "another_extension"}])
    assert repository_status([repo]) == SuperhiveStatus(True, False)
    write_index(tmp_path, [{"id": "cloth_next", "version": "2.7.0"}])
    assert repository_status([repo]) == SuperhiveStatus(True, True)
    index.write_text("{broken", encoding="utf-8")
    assert repository_status([repo]) == SuperhiveStatus(True, False)


@pytest.mark.parametrize("url", ["https://example.com/index.json", "https://superhivemarket.com.example.com/index.json", "http://superhivemarket.com/index.json", "file:///superhivemarket.com", "https://["])
def test_only_enabled_official_remote_repository_counts(tmp_path, url):
    write_index(tmp_path, [{"id": "cloth_next"}])
    assert repository_status([repository(tmp_path, url)]) == SuperhiveStatus(False, False)
    repo = repository(tmp_path)
    repo.enabled = False
    assert not repository_status([repo]).connected
    repo.enabled = True
    repo.use_remote_url = False
    assert not repository_status([repo]).connected


def test_credentials_are_not_read_and_configuration_is_unchanged(tmp_path):
    write_index(tmp_path, [{"id": "cloth_next"}])
    class Repo:
        enabled = True
        use_remote_url = True
        remote_url = "https://extensions.superhivemarket.com/index.json"
        directory = str(tmp_path)
        @property
        def access_token(self):
            raise AssertionError("must never read the token")
    assert repository_status([Repo()]) == SuperhiveStatus(True, True)


def test_an_unrelated_package_or_local_install_is_not_purchase_evidence(tmp_path):
    (tmp_path / "cloth_next").mkdir()
    assert not repository_status([repository(tmp_path)]).purchase_validated
    write_index(tmp_path, [{"id": "cloth_next_other"}])
    assert not repository_status([repository(tmp_path)]).purchase_validated
