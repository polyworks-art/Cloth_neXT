import os
import time

import pytest

from cloth_next.polyworks.storage import PRESENCE_FILE, LocalStore
from companion import app, tray
from companion.account_window import AccountTray


def test_tray_arguments_belong_to_tray_mode():
    for argv in (["--mode", "account", "--blender-pid", "12"], ["--mode", "bake", "--open-window"]):
        with pytest.raises(SystemExit) as error:
            app.main(argv)
        assert error.value.code == 2


def test_tray_mode_starts_the_tray_runner(monkeypatch):
    seen = {}
    import companion.account_window as account_window
    monkeypatch.setattr(account_window, "run_account_tray",
                        lambda content_root, **kw: seen.update(kw) or 0)
    with pytest.raises(SystemExit) as done:
        app.main(["--mode", "account-tray", "--blender-pid", "42", "--open-window"])
    assert done.value.code == 0
    assert seen["blender_pid"] == 42 and seen["open_window"] is True


def test_only_a_live_other_tray_counts(tmp_path):
    store = LocalStore(tmp_path)
    assert not AccountTray.other_tray_running(store)
    store.write_json(PRESENCE_FILE, {"pid": os.getpid(), "mode": "tray", "heartbeat_at": time.time()})
    assert not AccountTray.other_tray_running(store)  # that is this process itself
    store.write_json(PRESENCE_FILE, {"pid": os.getppid(), "mode": "account", "heartbeat_at": time.time()})
    assert not AccountTray.other_tray_running(store)  # an account window is not a tray
    store.write_json(PRESENCE_FILE, {"pid": os.getppid(), "mode": "tray",
                                     "heartbeat_at": time.time() - 3600})
    assert not AccountTray.other_tray_running(store)  # stale heartbeat
    store.write_json(PRESENCE_FILE, {"pid": os.getppid(), "mode": "tray", "heartbeat_at": time.time()})
    assert AccountTray.other_tray_running(store) == tray.process_alive(os.getppid())


@pytest.mark.skipif(not tray.AVAILABLE, reason="Windows tray")
def test_process_alive_never_signals():
    assert tray.process_alive(os.getpid())
    assert not tray.process_alive(2_000_000_000)
