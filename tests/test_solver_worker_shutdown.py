# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later


class ControlledWorker:
    def __init__(self, *, alive=True):
        self.alive = alive
        self.joins = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joins.append(timeout)


def test_shutdown_preserves_worker_that_exceeds_join_timeout(blender_env):
    module = blender_env.solver_test
    worker = ControlledWorker(alive=True)
    plan = object()
    module._worker = worker
    module._active_plan = plan

    assert module.shutdown(join_timeout=0.25) is False
    assert worker.joins == [0.25]
    assert module._worker is worker
    assert module._active_plan is plan

    worker.alive = False
    assert module.shutdown(join_timeout=0.25) is True
    assert module._worker is None
    assert module._active_plan is None


def test_shutdown_clears_already_finished_worker(blender_env):
    module = blender_env.solver_test
    worker = ControlledWorker(alive=False)
    module._worker = worker
    module._active_plan = object()

    assert module.shutdown(join_timeout=0) is True
    assert worker.joins == []
    assert module._worker is None
    assert module._active_plan is None
