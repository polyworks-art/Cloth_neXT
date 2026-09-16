import importlib
import json
import socket
import ssl
import threading
from types import SimpleNamespace
import urllib.error
import uuid

import pytest
from cloth_next import manifest_version
from cloth_next import presence as p


def test_identity_persistence_and_randomness(tmp_path):
    first = p.IdentityStore(tmp_path / "first.json")
    first.set_username("  Unknown Account  ")
    identifier = first.installation_id()
    reloaded = p.IdentityStore(first.path)
    assert reloaded.username() == "Unknown Account"
    assert reloaded.installation_id() == identifier
    assert uuid.UUID(identifier).version == 4
    assert p.IdentityStore(tmp_path / "second.json").installation_id() != identifier
    assert set(first.read()) == {"superhive_username", "install_id"}


@pytest.mark.parametrize("value", ["", "  ", "x" * 129, "a\n b"])
def test_invalid_username_leaves_saved_identity(tmp_path, value):
    store = p.IdentityStore(tmp_path / "identity.json")
    store.set_username("accepted")
    with pytest.raises(ValueError):
        store.set_username(value)
    assert store.username() == "accepted"


def test_maximum_and_payload():
    data = p.payload(" " + "x" * 128 + " ", str(uuid.uuid4()), manifest_version(), "BETA")
    assert set(data) == {"superhive_username", "install_id", "version", "channel"}
    assert data["version"] == manifest_version()
    assert data["channel"] == "beta"


def test_installed_replica_survives_updates_and_deletion(tmp_path):
    replica = tmp_path / "addon" / "resources" / ".state" / "r7.dat"
    store = p.IdentityStore(tmp_path / "config" / "presence.json", replica)
    store.set_username("Unknown")
    identifier = store.installation_id()
    assert json.loads(replica.read_text())["install_id"] == identifier
    replica.unlink()
    updated = p.IdentityStore(store.path, replica)
    updated.restore_replica()
    assert updated.installation_id() == identifier
    assert json.loads(replica.read_text())["install_id"] == identifier


@pytest.mark.parametrize("failure", [None, TimeoutError(), socket.gaierror(),
    ssl.SSLError(), ConnectionResetError(),
    urllib.error.HTTPError(p.ENDPOINT, 500, "error", {}, None)])
def test_transport_silent_and_exact(monkeypatch, failure, capsys):
    data = p.payload("unknown", str(uuid.uuid4()), manifest_version(), "DEV")
    def open_request(request, timeout):
        assert request.full_url == p.ENDPOINT
        assert request.method == "POST"
        assert json.loads(request.data) == data
        assert timeout == 4
        if failure is not None:
            raise failure
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): raise AssertionError("Response must not be read")
        return Response()
    monkeypatch.setattr(p.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    assert p.send_heartbeat(data) is None
    assert capsys.readouterr() == ("", "")


def test_worker_cleanup_and_single_request(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def send(data):
        entered.set()
        release.wait(2)
    monkeypatch.setattr(p, "send_heartbeat", send)
    service = p.Service()
    service.active = True
    try:
        service.submit({})
        assert entered.wait(1)
        worker = service.worker
        service.submit({})
        assert service.worker is worker
        service.stop()
        service.submit({})
        assert service.worker is worker
    finally:
        release.set()
        service.worker.join(2)
    assert not service.worker.is_alive()


def test_scheduler_reload_preferences_and_failures(blender_env, monkeypatch, tmp_path):
    runtime = importlib.import_module("cloth_next.blender.presence_runtime")
    identity = p.IdentityStore(tmp_path / "identity.json")
    monkeypatch.setattr(runtime, "store", lambda: identity)
    preferences = SimpleNamespace(update_channel="DEV", functional_state="ready")
    monkeypatch.setattr(runtime, "addon_preferences", lambda *args: preferences)
    sent = []
    monkeypatch.setattr(runtime._service, "submit", sent.append)
    runtime.username_set(preferences, "  Unknown  ")
    assert runtime.username_get(preferences) == "Unknown"
    runtime.register()
    runtime.register()
    timers = blender_env.bpy.app.timers.functions
    assert timers.count(runtime._pulse) == 1
    runtime._pulse()
    runtime._pulse()
    assert len(sent) == 1
    assert sent[0]["channel"] == "dev"
    assert sent[0]["version"] == manifest_version()
    runtime.username_set(preferences, "new name")
    runtime._pulse()
    assert len(sent) == 2
    monkeypatch.setattr(runtime._service, "submit", lambda data: (_ for _ in ()).throw(OSError()))
    runtime.username_set(preferences, "another")
    assert runtime._pulse() == 1.0
    assert preferences.functional_state == "ready"
    old = runtime._pulse
    service = runtime._service
    importlib.reload(runtime)
    runtime.register()
    assert old not in timers
    assert runtime._service is service
    assert timers.count(runtime._pulse) == 1
    runtime.unregister()
    assert runtime._pulse not in timers
    assert not service.active
    runtime.register()
    runtime.unregister()


def test_no_hardware_or_admin_identifiers():
    from pathlib import Path
    source = Path(p.__file__).read_text()
    for forbidden in ("MachineGuid", "Win32_BaseBoard", "wmic", "motherboard",
                      "serialnumber", "MAC address", "CLOTH_NEXT_ADMIN_API_KEY", "X-API-Key"):
        assert forbidden not in source


def test_first_run_prompt_is_once_and_empty_is_rejected(blender_env, monkeypatch, tmp_path):
    runtime = importlib.import_module("cloth_next.blender.presence_runtime")
    identity = p.IdentityStore(tmp_path / "identity.json")
    monkeypatch.setattr(runtime, "store", lambda: identity)
    prompts = []
    monkeypatch.setattr(runtime.bpy.app, "background", False, raising=False)
    monkeypatch.setattr(runtime.bpy.ops, "clothnext", SimpleNamespace(
        presence_username=lambda *args: prompts.append(args)), raising=False)
    runtime.register()
    try:
        runtime._pulse()
        runtime._pulse()
        assert prompts == [("INVOKE_DEFAULT",)]
        assert len(identity.installation_id()) == 36
        operator = runtime.CLOTHNEXT_OT_presence_username()
        operator.username = "  "
        operator.report = lambda *args: None
        assert operator.execute(None) == {"CANCELLED"}
        operator.username = " Unknown Username "
        assert operator.execute(None) == {"FINISHED"}
        assert identity.username() == "Unknown Username"
    finally:
        runtime.unregister()
