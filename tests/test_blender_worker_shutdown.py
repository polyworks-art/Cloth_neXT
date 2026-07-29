# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace


class ControlledWorker:
    def __init__(self, *, alive=True):
        self.alive = alive
        self.joins = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joins.append(timeout)


def test_update_shutdown_keeps_live_worker_registered(blender_env):
    module = blender_env.addon_update_operators
    worker = ControlledWorker(alive=True)
    module._worker = worker

    assert module.shutdown(join_timeout=0.1) is False
    assert worker.joins == [0.1]
    assert module._worker is worker

    worker.alive = False
    assert module.shutdown(join_timeout=0.1) is True
    assert module._worker is None


def test_installer_shutdown_keeps_live_worker_and_installer(blender_env):
    import cloth_next.blender.preferences as preferences

    worker = ControlledWorker(alive=True)
    installer = SimpleNamespace(cancelled=False)
    installer.cancel = lambda: setattr(installer, "cancelled", True)
    preferences._session.worker = worker
    preferences._session.installer = installer

    assert preferences.shutdown(join_timeout=0.2) is False
    assert installer.cancelled
    assert worker.joins == [0.2]
    assert preferences._session.worker is worker
    assert preferences._session.installer is installer

    worker.alive = False
    assert preferences.shutdown(join_timeout=0.2) is True
    assert preferences._session.worker is None
    assert preferences._session.installer is None
