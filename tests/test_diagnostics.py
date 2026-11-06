# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in diagnostics upload: privacy filter, buffer, package, sending rules."""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from cloth_next.diagnostics import DiagnosticsLogHandler, DiagnosticsService, crashes, payload, store
from cloth_next.diagnostics.sanitize import scrub
from cloth_next.diagnostics.transport import Outcome, api_origin, classify, post_package
from cloth_next.telemetry.snapshot import GpuTelemetry

ROOT = Path(__file__).resolve().parents[1]


# --- privacy filter ---------------------------------------------------------------

@pytest.mark.parametrize("text, forbidden", [
    (r"File C:\Users\maria.k\Documents\shots\hero_cloth.blend failed", ["maria.k", "hero_cloth"]),
    ("open /home/jdoe/scenes/a.blend1", ["jdoe", "a.blend1"]),
    ("Authorization: Bearer abc.def-ghijklmnop", ["abc.def-ghijklmnop"]),
    ('{"token": "s3cr3t-value"}', ["s3cr3t-value"]),
    ("password=hunter22&x=1", ["hunter22"]),
    ("https://cdn.example/x.zip?sig=abcdef123&e=1", ["abcdef123"]),
    ("mail me: someone@example.com", ["someone@example.com"]),
    ("opaque " + "Ab9_" * 12, ["Ab9_Ab9_Ab9_"]),
    (r"\\nas01\projects\x", ["nas01"]),
])
def test_scrub_removes_personal_data(text, forbidden):
    cleaned = scrub(text, 1000)
    for word in forbidden:
        assert word not in cleaned


def test_scrub_replaces_own_home_and_keeps_useful_text(monkeypatch, tmp_path):
    home = str(tmp_path / "SomeUser")
    monkeypatch.setattr("os.path.expanduser", lambda _p: home)
    cleaned = scrub(f"CNX-E412 in {home}\\cloth_next\\bake.py line 3", 500)
    assert "SomeUser" not in cleaned and "CNX-E412" in cleaned and "bake.py line 3" in cleaned


def test_scrub_limits_length_and_control_characters():
    cleaned = scrub("a\x00b\x07c\n" + "word " * 200, 100)
    assert len(cleaned) == 100 and "\x00" not in cleaned and "\x07" not in cleaned


# --- buffer -----------------------------------------------------------------------

def test_queue_peek_drop_and_age_limit(tmp_path):
    now = [1000.0]
    queue = store.EventQueue(tmp_path / "q.jsonl", max_age_seconds=100, clock=lambda: now[0])
    queue.append([{"type": "perf", "n": 1}, {"type": "perf", "n": 2}])
    now[0] = 1050.0
    queue.append([{"type": "perf", "n": 3}])
    assert [e["n"] for e in queue.peek(10)] == [1, 2, 3]
    queue.drop(1)
    assert [e["n"] for e in queue.peek(10)] == [2, 3]
    now[0] = 1120.0  # events 1-2 older than 100 s
    assert [e["n"] for e in queue.peek(10)] == [3]


def test_queue_drops_oldest_when_too_big(tmp_path):
    queue = store.EventQueue(tmp_path / "q.jsonl", max_bytes=400)
    for n in range(30):
        queue.append([{"type": "perf", "n": n}])
    kept = [e["n"] for e in queue.peek(100)]
    assert kept and kept[-1] == 29 and kept[0] > 0
    assert (tmp_path / "q.jsonl").stat().st_size <= 400


def test_hardware_id_is_a_salted_hash_of_the_machine_id(tmp_path):
    hashed = store.hardware_id(tmp_path, machine_guid=lambda: "4c4c4544-0000-1111")
    assert hashed == store.hardware_id(tmp_path, machine_guid=lambda: "4c4c4544-0000-1111")
    assert len(hashed) == 64 and "4c4c4544" not in hashed
    assert hashed != store.hardware_id(tmp_path, machine_guid=lambda: "other")


def test_hardware_id_falls_back_to_a_stored_random_id(tmp_path):
    first = store.hardware_id(tmp_path, machine_guid=lambda: "")
    assert first == store.hardware_id(tmp_path, machine_guid=lambda: "")
    store.forget_fallback_id(tmp_path)
    assert store.hardware_id(tmp_path, machine_guid=lambda: "") != first


def test_real_machine_id_is_readable():
    assert store.hardware_id(Path("."))  # never raises, always 64 hex characters


def test_session_marker_detects_unclean_exit(tmp_path):
    marker = store.SessionMarker(tmp_path)
    assert marker.begin("a") is None
    assert marker.begin("b")["session_id"] == "a"  # "a" never ended
    marker.end("b")
    assert marker.begin("c") is None


# --- crash files ------------------------------------------------------------------

def test_python_crash_trace_needs_a_stack(tmp_path):
    path = tmp_path / "trace.txt"
    path.write_text("Cloth NeXt Python crash trace, Python 3.13\n", encoding="utf-8")
    assert crashes.previous_python_crash(path) == ""
    path.write_text("Cloth NeXt Python crash trace\nWindows fatal exception: access violation\n\n"
                    "Current thread 0x1 (most recent call first):\n  File \"x.py\", line 3\n",
                    encoding="utf-8")
    assert "access violation" in crashes.previous_python_crash(path)


def test_companion_crashes_only_new_crashed_sections(tmp_path):
    path = tmp_path / "companion-crash.log"
    path.write_text("--- 2026-11-06 10:00:00 pid=1 mode=bake\n", encoding="utf-8")
    found, offset = crashes.new_companion_crashes(path, 0)
    assert found == []
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("--- 2026-11-06 11:00:00 pid=2 mode=account\nFatal Python error: Aborted\n"
                     "Current thread 0x2:\n  File \"tk.py\"\n--- 2026-11-06 12:00:00 pid=3 mode=bake\n")
    found, new_offset = crashes.new_companion_crashes(path, offset)
    assert [c["mode"] for c in found] == ["account"] and new_offset == path.stat().st_size
    assert crashes.new_companion_crashes(path, new_offset)[0] == []


# --- package ----------------------------------------------------------------------

IDENTITY = {"hardware_id": "a" * 64,
            "session_id": "22222222-2222-4222-8222-222222222222",
            "addon_version": "2.9.65", "blender_version": "5.2.3", "operating_system": "Windows 11",
            "gpu": ""}


def test_package_merges_errors_and_strips_unknown_fields():
    events = [{"type": "error", "at": "t", "code": "CNX-E1", "message": "m", "secret": "x"},
              {"type": "error", "at": "t2", "code": "CNX-E1", "message": "m"},
              {"type": "nonsense"},
              {"type": "perf", "at": "t", "cpu_percent": 3.0, "token": "x"}]
    package, consumed = payload.build_package(IDENTITY, events)
    assert consumed == 4 and "gpu" not in package
    assert package["events"] == [
        {"type": "error", "at": "t", "code": "CNX-E1", "message": "m", "count": 2},
        {"type": "perf", "at": "t", "cpu_percent": 3.0}]


def test_package_respects_event_and_size_limits():
    events = [{"type": "perf", "at": "t", "cpu_percent": float(n)} for n in range(500)]
    package, consumed = payload.build_package(IDENTITY, events)
    assert len(package["events"]) == payload.MAX_EVENTS == consumed
    big = [{"type": "crash", "at": "t", "trace": "x" * 20000} for _ in range(30)]
    package, consumed = payload.build_package(IDENTITY, big)
    assert len(json.dumps(package)) <= payload.MAX_BYTES and 0 < consumed < 30


# --- transport --------------------------------------------------------------------

@pytest.mark.parametrize("status, kind", [(202, "sent"), (201, "sent"), (422, "drop"), (413, "drop"),
                                          (429, "retry"), (404, "retry"), (401, "retry"),
                                          (503, "retry")])
def test_status_classification(status, kind):
    assert classify(status).kind == kind


def test_origin_requires_https_except_loopback():
    assert api_origin({}) == "https://api.cloth-next.de"
    assert api_origin({"CLOTH_NEXT_POLYWORKS_API_URL": "http://127.0.0.1:9"}) == "http://127.0.0.1:9"
    with pytest.raises(ValueError):
        api_origin({"CLOTH_NEXT_POLYWORKS_API_URL": "http://example.com"})


class _Recorder(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append((self.path, dict(self.headers), json.loads(body)))
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"accepted":1}')

    def log_message(self, *_args):
        pass


@pytest.fixture
def server():
    _Recorder.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", _Recorder.seen
    httpd.shutdown()


def test_post_sends_token_only_in_header(server):
    origin, seen = server
    package, _ = payload.build_package(IDENTITY, [{"type": "perf", "at": "t"}])
    assert post_package(origin, package, token="tok-123").kind == "sent"
    assert post_package(origin, package).kind == "sent"
    (path, headers, body), (_p, anon_headers, _b) = seen
    assert path == "/v1/telemetry" and headers["Authorization"] == "Bearer tok-123"
    assert "tok-123" not in json.dumps(body) and "Authorization" not in anon_headers


def test_post_offline_is_retry():
    package, _ = payload.build_package(IDENTITY, [{"type": "perf", "at": "t"}])
    assert post_package("http://127.0.0.1:9", package).kind == "retry"


# --- service ----------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeSystem:
    def sample(self):
        return 12.34, 3 * 1024 * 1024 * 1024, 16 * 1024 * 1024 * 1024


def _gpu():
    return (GpuTelemetry(0, "NVIDIA RTX Test", 55.0, 2 << 30, 8 << 30, 60.0, 100.0),)


def make_service(tmp_path, *, bake=lambda: False, outcomes=None, token=None):
    sent = []
    outcomes = list(outcomes or [])

    def sender(origin, package, token=None):
        sent.append((package, token))
        return outcomes.pop(0) if outcomes else Outcome("sent", 202)

    clock = Clock()
    service = DiagnosticsService(tmp_path, environment=IDENTITY, origin="https://api.test",
                                 sender=sender, token_provider=token, system_provider=FakeSystem(),
                                 gpu_provider=_gpu, bake_active=bake, clock=clock,
                                 first_flush_seconds=120)
    service.set_online(True)
    return service, clock, sent


def test_without_consent_nothing_is_collected_or_sent(tmp_path):
    service, clock, sent = make_service(tmp_path)
    service.record_error("cloth_next.bake", "ERROR", "CNX-E101 broken")
    clock.now = 10_000
    service.step()
    assert sent == [] and not (tmp_path / "queue.jsonl").exists()


def test_samples_and_sends_with_consent_and_account_token(tmp_path):
    service, clock, sent = make_service(tmp_path, token=lambda: "tok")
    service.set_consent(True)
    service.record_error("bl_ext.user_default.cloth_next.bake", "ERROR",
                         r"CNX-E101 in C:\Users\maria\x.blend")
    service.step()  # first sample, not yet time to send
    assert sent == []
    clock.now = 121
    service.step()
    (package, token), = sent
    assert token == "tok" and package["gpu"] == "NVIDIA RTX Test"
    kinds = {event["type"]: event for event in package["events"]}
    assert kinds["perf"]["ram_used_mb"] == 3072 and kinds["perf"]["gpu_percent"] == 55.0
    assert kinds["error"]["code"] == "CNX-E101" and kinds["error"]["logger"] == "cloth_next.bake"
    assert "maria" not in kinds["error"]["message"]
    assert len(service.queue) == 0


def test_no_sending_during_a_bake(tmp_path):
    active = [True]
    service, clock, sent = make_service(tmp_path, bake=lambda: active[0])
    service.set_consent(True)
    clock.now = 500
    service.step()
    assert sent == [] and len(service.queue) == 1
    active[0] = False
    service.step()
    assert len(sent) == 1


def test_retry_keeps_events_and_backs_off(tmp_path):
    service, clock, sent = make_service(tmp_path, outcomes=[Outcome("retry", 404), Outcome("retry", 503)])
    service.set_consent(True)
    clock.now = 200
    service.step()
    assert len(sent) == 1 and len(service.queue) == 1
    clock.now = 200 + 15 * 60 - 1
    service.step()
    assert len(sent) == 1  # still waiting
    clock.now = 200 + 15 * 60 + 1
    service.step()
    assert len(sent) == 2
    clock.now += 30 * 60 - 5  # second failure doubles the wait
    service.step()
    assert len(sent) == 2


def test_rate_limit_retry_after_is_respected(tmp_path):
    service, clock, sent = make_service(tmp_path, outcomes=[Outcome("retry", 429, 3600)])
    service.set_consent(True)
    clock.now = 200
    service.step()
    clock.now = 200 + 3500
    service.step()
    assert len(sent) == 1


def test_refused_package_is_dropped(tmp_path):
    service, clock, sent = make_service(tmp_path, outcomes=[Outcome("drop", 422)])
    service.set_consent(True)
    clock.now = 200
    service.step()
    assert len(sent) == 1 and len(service.queue) == 0


def test_turning_off_deletes_everything(tmp_path):
    service, clock, sent = make_service(tmp_path)
    service.set_consent(True)
    service.step()
    assert len(service.queue) == 1
    service.set_consent(False)
    assert len(service.queue) == 0
    service.record_error("cloth_next.x", "ERROR", "after opt-out")
    assert service.preview()["events"] == []


def test_previous_session_crash_is_reported(tmp_path):
    trace = tmp_path / "trace.txt"
    trace.write_text("header\nFatal Python error: Segmentation fault\nCurrent thread 0x1\n",
                     encoding="utf-8")
    companion = tmp_path / "companion-crash.log"
    companion.write_text("--- old pid=1 mode=bake\nFatal Python error: old\n", encoding="utf-8")
    marker = store.SessionMarker(tmp_path / "m")
    service, clock, sent = make_service(tmp_path)
    service.set_consent(True)
    service.collect_previous_session(marker, trace, companion)
    # First run: the older companion crash predates the consent and stays local.
    events = service.preview()["events"]
    assert [(e["source"], e["kind"]) for e in events] == [("blender", "python_fatal")]
    with open(companion, "a", encoding="utf-8") as handle:
        handle.write("--- new pid=2 mode=account\nFatal Python error: Tcl\nCurrent thread 0x2\n")
    trace.write_text("header\n", encoding="utf-8")
    second, *_ = make_service(tmp_path)
    second.set_consent(True)
    second.collect_previous_session(marker, trace, companion)  # previous session never ended
    assert sorted((e["source"], e["kind"]) for e in second.preview()["events"]
                  if e["type"] == "crash") == [
        ("blender", "unclean_exit"), ("companion", "account")]


def test_log_handler_forwards_warnings_only(tmp_path):
    service, *_ = make_service(tmp_path)
    service.set_consent(True)
    logger = logging.getLogger("cloth_next.test_diagnostics")
    handler = DiagnosticsLogHandler(service)
    logger.addHandler(handler)
    try:
        logger.info("just info")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception("CNX-E205 bake failed")
    finally:
        logger.removeHandler(handler)
    errors = [e for e in service.preview()["events"] if e["type"] == "error"]
    assert len(errors) == 1 and errors[0]["code"] == "CNX-E205"
    assert "RuntimeError: boom" in errors[0]["message"]


def test_thread_starts_and_stops(tmp_path):
    service, *_ = make_service(tmp_path)
    assert service.start() and not service.start()
    assert service.stop(timeout=10)


def test_blender_side_never_handles_the_token_or_network():
    source = (ROOT / "cloth_next" / "blender" / "diagnostics_runtime.py").read_text(encoding="utf-8")
    for forbidden in ("http.client", "urllib", "polyworks.api", "polyworks.service", "vault"):
        assert forbidden not in source
