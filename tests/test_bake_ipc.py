import socket
import time

import pytest

from cloth_next.bake.status import BakeSnapshot, BakeState
from cloth_next.bake.transport import LocalSocketClient, LocalSocketServer

def wait_for(predicate, timeout=2):
    end=time.time()+timeout
    while time.time()<end:
        value=predicate()
        if value: return value
        time.sleep(.01)
    return None

def test_local_session_publish_cancel_disconnect_and_port_release():
    server=LocalSocketServer(); assert server.host=="127.0.0.1" and server.port>0
    client=LocalSocketClient(server.port,server.token)
    assert client.receive(1)["type"]=="session_hello"
    client.send("ready"); assert wait_for(server.poll_request)=="ready"
    snap=BakeSnapshot(state=BakeState.SIMULATING, preview=True)
    server.publish(snap); message=client.receive(1)
    assert message["type"]=="bake_status" and message["snapshot"]==snap
    client.request_cancel(); assert wait_for(server.poll_request)=="cancel_request"
    client.close(); assert wait_for(server.poll_request)=="close_notice"
    port=server.port; server.close()
    probe=socket.socket(); probe.bind(("127.0.0.1",port)); probe.close()

def test_wrong_token_never_becomes_request():
    server=LocalSocketServer(); client=LocalSocketClient(server.port,"wrong")
    with pytest.raises(PermissionError): client.receive(.3)
    client.request_cancel(); time.sleep(.1)
    assert server.poll_request() is None
    client.close(); server.close()

def test_non_localhost_server_rejected():
    with pytest.raises(ValueError): LocalSocketServer("0.0.0.0")

def test_status_publish_is_nonblocking_and_coalesces_before_connect():
    server = LocalSocketServer()
    first = BakeSnapshot(state=BakeState.SIMULATING, progress_current=1)
    latest = BakeSnapshot(state=BakeState.IMPORTING, progress_current=2)

    started = time.perf_counter()
    server.publish(first)
    server.publish(latest)
    assert time.perf_counter() - started < 0.05

    client = LocalSocketClient(server.port, server.token)
    assert client.receive(1)["type"] == "session_hello"
    message = client.receive(1)
    assert message["type"] == "bake_status"
    assert message["snapshot"] == latest
    client.close()
    server.close()
