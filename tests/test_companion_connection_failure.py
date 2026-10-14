"""Failed local IPC startup exits without an unhandled frozen-app dialog."""
import pytest
from companion import app


@pytest.mark.parametrize('failure',[TimeoutError('timed out'),ConnectionRefusedError('closed')])
def test_connection_failure_exits_without_creating_window(monkeypatch,caplog,failure):
    def connect(*args):
        raise failure
    def window(*args,**kwargs):
        pytest.fail('A failed connection must not create a Bake window')
    monkeypatch.setattr(app,'LocalSocketClient',connect)
    monkeypatch.setattr(app,'BakeWindow',window)
    with pytest.raises(SystemExit) as caught:
        app.main(['--port','12345','--token','test-session'])
    assert caught.value.code==2
    assert 'companion connection failed' in caplog.text
