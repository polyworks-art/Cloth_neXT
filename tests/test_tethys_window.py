from types import SimpleNamespace

from cloth_next.bake.controller import BakeController
from cloth_next.bake.status import BakeJobKind, BakeSnapshot, BakeState, CompanionMode
from companion.app import BakeWindow, progress_display_text, wetness_display


def test_wetness_bake_walks_the_bake_window_states():
    """The path the TETHYS operator takes through the shared Bake state is allowed."""
    c = BakeController()
    c.transition(BakeState.PREPARING, job_kind=BakeJobKind.WETNESS, status_title="TETHYS Wetness")
    for state in (BakeState.STARTING_COMPANION, BakeState.WAITING_FOR_COMPANION,
                  BakeState.COMPANION_READY, BakeState.STARTING_RUN, BakeState.EXPORTING,
                  BakeState.STARTING_SOLVER, BakeState.SIMULATING, BakeState.IMPORTING):
        s = c.transition(state, status_title="TETHYS Wetness")
        assert s.active and s.can_cancel and s.job_kind is BakeJobKind.WETNESS
    s = c.transition(BakeState.FINISHED, progress_current=1000, progress_total=1000)
    assert progress_display_text(s) == "Wetness baked"


def test_wetness_progress_text_and_roundtrip():
    c = BakeController()
    c.transition(BakeState.PREPARING, job_kind=BakeJobKind.WETNESS)
    c.transition(BakeState.STARTING_RUN)
    c.transition(BakeState.EXPORTING)
    c.transition(BakeState.STARTING_SOLVER)
    s = c.transition(BakeState.SIMULATING, status_title="TETHYS Wetness", progress_current=420,
                     progress_total=1000, current_frame=40, estimated_remaining_seconds=12.0,
                     activity_label="Simulating wetness · 39 / 95")
    assert progress_display_text(s) == "Wetness · 42%"
    back = BakeSnapshot.from_json(s.to_json())
    assert back.job_kind is BakeJobKind.WETNESS and wetness_display(back)
    assert back.activity_label == "Simulating wetness · 39 / 95"
    assert progress_display_text(c.request_cancel()) == "Wetness · 42%"
    assert progress_display_text(c.transition(BakeState.CANCELLED)) == "Wetness cancelled"


def test_cancelled_or_failed_wetness_is_named():
    c = BakeController()
    c.transition(BakeState.PREPARING, job_kind=BakeJobKind.WETNESS)
    assert progress_display_text(c.fail("TETHYS-E140 no GAIA access")) == "Wetness bake failed"


def test_wetness_window_title():
    titles = []
    window = SimpleNamespace(
        root=SimpleNamespace(title=titles.append), _custom_titlebar=False)
    for wetness in (True, False):
        window._wetness_display = wetness
        window._water_display = wetness  # wetness reuses the compact water layout
        BakeWindow._set_window_title(window, CompanionMode.BAKE)
    assert titles == ["TETHYS Wetness", "Cloth NeXt Bake"]


def test_old_window_reads_wetness_as_a_plain_bake():
    """A newer add-on talking to an older window never breaks its parser."""
    data = BakeSnapshot(job_kind=BakeJobKind.WETNESS).to_dict()
    data["job_kind"] = "SOMETHING_NEWER"
    assert BakeSnapshot.from_dict(data).job_kind is BakeJobKind.BAKE
