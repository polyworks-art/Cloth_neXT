# SPDX-License-Identifier: GPL-3.0-or-later
"""Report a Bug: field rules, POST /v1/bug-reports outcomes and the Blender worker."""
from __future__ import annotations

import json
import logging
import sys
import time

import pytest

from cloth_next.polyworks import bug_reports
from cloth_next.polyworks.api import ApiError, Endpoints, PolyWorksApi
from cloth_next.polyworks.service import AccountService
from cloth_next.polyworks.storage import LocalStore

TOKEN = "secret-session-token-for-tests"


def valid(**changes):
    fields = {"title": "Cloth explodes", "description": "Baking the dress makes it explode.",
              "steps": "", "addon_version": "2.9.65", "blender_version": "5.2.3",
              "operating_system": "Windows 11"}
    fields.update(changes)
    return fields


# Field rules -----------------------------------------------------------------------
def test_body_has_exactly_the_allowed_fields_and_drops_empty_optionals():
    body = bug_reports.build_report(valid())
    assert set(body) == {"title", "description", "addon_version", "blender_version",
                         "operating_system"}
    assert set(bug_reports.build_report(valid(steps="1. Bake"))) == set(bug_reports.FIELDS)


@pytest.mark.parametrize("field,value", [
    ("title", "ab"), ("title", "x" * 161), ("title", "   "),
    ("description", "too short"), ("description", "d" * 10001),
    ("steps", "s" * 5001),
])
def test_user_fields_outside_the_limits_are_refused(field, value):
    with pytest.raises(bug_reports.BugReportInvalid) as caught:
        bug_reports.build_report(valid(**{field: value}))
    assert caught.value.field == field


def test_limits_are_inclusive():
    body = bug_reports.build_report(valid(title="abc", description="d" * 10000, steps="s" * 5000))
    assert len(body["title"]) == 3 and len(body["description"]) == 10000


def test_automatic_fields_are_shortened_not_refused():
    body = bug_reports.build_report(valid(addon_version="v" * 100, operating_system="o" * 300))
    assert len(body["addon_version"]) == 64 and len(body["operating_system"]) == 128


def test_unknown_fields_are_refused():
    with pytest.raises(bug_reports.BugReportInvalid):
        bug_reports.build_report(valid(email="me@example.com"))


def test_control_characters_are_removed_but_newline_and_tab_stay():
    text = bug_reports.clean_text("a\r\nb\rc\x00d\x1be\tf\x7fg\x85h\n")
    assert text == "a\nb\ncd" + "e\tfgh"
    # Counted after cleaning: 9 visible characters plus control chars are still too short.
    with pytest.raises(bug_reports.BugReportInvalid):
        bug_reports.build_report(valid(description="123456789\x00\x01\x02"))


def test_environment_fields_fit_the_limits():
    fields = bug_reports.environment_fields("2.9.65", "5.2.3 LTS")
    assert fields["addon_version"] == "2.9.65" and fields["blender_version"] == "5.2.3 LTS"
    assert 0 < len(fields["operating_system"]) <= 128
    assert bug_reports.field_problem("operating_system", fields["operating_system"]) == ""


# Service outcomes ------------------------------------------------------------------
class Transport:
    def __init__(self, status, body=None, retry_after=None):
        self.status, self.body, self.retry_after = status, body, retry_after
        self.calls = []

    def request(self, method, path, body=None, token=None):
        self.calls.append((method, path, body, token))
        raw = json.dumps(self.body).encode() if self.body is not None else b""
        return self.status, raw, self.retry_after


class Vault:
    kind = "test"

    def __init__(self, token=TOKEN):
        self.session = {"token": token} if token else None

    def load(self):
        return self.session

    def clear(self):
        self.session = None


def service_with(tmp_path, transport, vault=None):
    endpoints = Endpoints("https://api.cloth-next.de", "https://account.cloth-next.de")
    store = LocalStore(tmp_path / "account")
    store.write_json("state.json", {"account": {"signed_in": True, "session": "active"}})
    return AccountService(store, endpoints=endpoints, api=PolyWorksApi(endpoints, transport),
                          vault=vault or Vault(), trust=object())


def test_201_posts_the_report_with_the_bearer_token(tmp_path):
    transport = Transport(201, {"id": 42, "submitted_at": "2026-11-06T17:00:00Z", "extra": 1})
    receipt = service_with(tmp_path, transport).submit_bug_report(valid())
    assert receipt == {"id": "42", "submitted_at": "2026-11-06T17:00:00Z"}
    method, path, body, token = transport.calls[0]
    assert (method, path, token) == ("POST", "/v1/bug-reports", TOKEN)
    assert body == bug_reports.build_report(valid())
    assert "42" in bug_reports.success_message(receipt)


def test_invalid_fields_never_reach_the_network(tmp_path):
    transport = Transport(201, {"id": "x"})
    with pytest.raises(bug_reports.BugReportInvalid):
        service_with(tmp_path, transport).submit_bug_report(valid(title="x"))
    assert transport.calls == []


def test_without_a_session_nothing_is_sent(tmp_path):
    transport = Transport(201, {"id": "x"})
    with pytest.raises(bug_reports.BugReportError) as caught:
        service_with(tmp_path, transport, Vault(token=None)).submit_bug_report(valid())
    assert caught.value.code == "sign_in_required" and transport.calls == []
    assert "Connect your PolyWorks account" in caught.value.message


def test_401_ends_the_session_and_asks_to_sign_in_again(tmp_path):
    vault = Vault()
    transport = Transport(401, {"error": {"code": "not_authenticated"}})
    with pytest.raises(bug_reports.BugReportError) as caught:
        service_with(tmp_path, transport, vault).submit_bug_report(valid())
    assert caught.value.code == "not_authenticated"
    assert "session has expired" in caught.value.message and "text is kept" in caught.value.message
    assert vault.session is None


def test_422_names_the_rejected_field(tmp_path):
    transport = Transport(422, {"error": {"code": "validation_error",
                                          "details": [{"loc": ["body", "title"], "msg": "x"}]}})
    with pytest.raises(bug_reports.BugReportError) as caught:
        service_with(tmp_path, transport).submit_bug_report(valid())
    assert caught.value.code == "rejected"
    assert "did not accept" in caught.value.message and "Title" in caught.value.message


def test_429_reports_the_hourly_limit_without_touching_the_licence_schedule(tmp_path):
    transport = Transport(429, {"error": {"code": "rate_limited"}}, retry_after="1500")
    service = service_with(tmp_path, transport)
    before = service.store.read_json("state.json")
    with pytest.raises(bug_reports.BugReportError) as caught:
        service.submit_bug_report(valid())
    assert caught.value.code == "rate_limited" and caught.value.retry_after == 1500
    assert "10 bug reports" in caught.value.message and "25 minutes" in caught.value.message
    assert service.store.read_json("state.json") == before


def test_network_failure_keeps_the_draft_message(tmp_path):
    class Offline(Transport):
        def request(self, *args, **kwargs):
            raise ApiError("network_unavailable")
    with pytest.raises(bug_reports.BugReportError) as caught:
        service_with(tmp_path, Offline(0)).submit_bug_report(valid())
    assert "could not be reached" in caught.value.message


def test_token_never_appears_in_errors_or_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    for transport in (Transport(401, {}), Transport(500, {}), Transport(429, {}),
                      Transport(422, {"error": {"code": "validation_error"}})):
        with pytest.raises(bug_reports.BugReportError) as caught:
            service_with(tmp_path, transport).submit_bug_report(valid())
        assert TOKEN not in caught.value.message
    assert TOKEN not in caplog.text


# Blender worker --------------------------------------------------------------------
def wait_until_sent(module):
    deadline = time.time() + 5
    while module.busy() and time.time() < deadline:
        time.sleep(0.01)
    assert not module.busy()


@pytest.fixture
def bug_module(blender_env, monkeypatch):
    module = sys.modules["cloth_next.blender.bug_report"]
    module.register()
    monkeypatch.setattr(module, "_bake_active", lambda: False)
    monkeypatch.setattr(module, "_client_name", lambda: "Cloth NeXt test")
    yield module
    module.unregister()


def test_worker_sends_in_the_background_and_clears_the_draft(bug_module, monkeypatch):
    sent = []

    class FakeService:
        def submit_bug_report(self, fields):
            sent.append(fields)
            return {"id": "r1", "submitted_at": ""}
    monkeypatch.setattr(bug_module, "_service", lambda _name: FakeService())
    assert bug_module.send(valid())
    wait_until_sent(bug_module)
    state = bug_module.snapshot()
    assert state.ok and "r1" in state.message and state.draft == {} and not state.sending
    assert sent == [valid()]


def test_worker_failure_keeps_the_draft(bug_module, monkeypatch):
    class FakeService:
        def submit_bug_report(self, fields):
            raise bug_reports.BugReportError("rate_limited", retry_after=60)
    monkeypatch.setattr(bug_module, "_service", lambda _name: FakeService())
    assert bug_module.send(valid(steps="1. Bake"))
    wait_until_sent(bug_module)
    state = bug_module.snapshot()
    assert state.ok is False and "about 1 minute" in state.message
    assert state.draft["steps"] == "1. Bake" and state.draft["title"] == "Cloth explodes"


def test_invalid_report_starts_no_worker(bug_module, monkeypatch):
    monkeypatch.setattr(bug_module, "_service", lambda _name: pytest.fail("must not send"))
    assert not bug_module.send(valid(description="short"))
    assert "at least 10" in bug_module.snapshot().message and not bug_module.busy()


def test_nothing_is_sent_during_a_bake(bug_module, monkeypatch):
    monkeypatch.setattr(bug_module, "_bake_active", lambda: True)
    monkeypatch.setattr(bug_module, "_service", lambda _name: pytest.fail("must not send"))
    assert not bug_module.send(valid())
    assert "Bake" in bug_module.snapshot().message


def test_help_menu_has_a_cloth_next_section(bug_module):
    from types import SimpleNamespace

    class Layout:
        def __init__(self):
            self.operators, self.labels = [], []

        def separator(self):
            pass

        def label(self, text="", **_kwargs):
            self.labels.append(text)

        def operator(self, idname, **kwargs):
            self.operators.append((idname, kwargs.get("text")))
            return SimpleNamespace()
    layout = Layout()
    bug_module.draw_help_menu(SimpleNamespace(layout=layout), None)
    assert layout.labels == ["Cloth NeXt"]
    assert layout.operators[0] == ("clothnext.report_bug", "Report a Bug…")
    assert [name for name, _ in layout.operators] == [
        "clothnext.report_bug", "clothnext.open_whats_new", "wm.url_open"]
