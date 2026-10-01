from types import SimpleNamespace
from cloth_next.bake.controller import BakeController
from cloth_next.bake.status import BakeJobKind, BakeSnapshot, BakeState
from companion.app import BakeWindow, progress_display_text


def test_water_window_hides_details_button_instead_of_disabling_it():
    calls = []
    window = SimpleNamespace(_details_visible=True,
        details_button=SimpleNamespace(pack_forget=lambda:calls.append('hide-button'),
            configure=lambda **kw:calls.append(kw), pack=lambda **kw:calls.append('show-button')),
        details_panel=SimpleNamespace(grid_remove=lambda:calls.append('hide-panel')),
        status_tooltip=SimpleNamespace(text=''), _fit_window_to_content=lambda:calls.append('resize'))
    BakeWindow._set_water_display(window,True)
    assert calls[:3] == ['hide-button','hide-panel','resize']
    assert not window._details_visible
    BakeWindow._set_water_display(window,False)
    assert calls[-1] == 'show-button'


def test_water_progress_describes_field_preparation_and_roundtrips():
    c = BakeController()
    owner = c.transition(BakeState.PREPARING, job_kind=BakeJobKind.WATER_FIELD)
    s = c.transition(BakeState.PREPARING_WATER, progress_current=3, progress_total=6,
                     preparation_current=1, preparation_total=2, current_frame=100)
    assert s.active and s.can_cancel
    assert s.status_title == 'Preparing Water Flow'
    assert progress_display_text(s) == 'Water Flow · 50%'
    assert BakeSnapshot.from_json(s.to_json()).preparation_total == 2
    c.transition(BakeState.PREPARING, job_id=owner.job_id)
    assert c.snapshot().job_id == owner.job_id
