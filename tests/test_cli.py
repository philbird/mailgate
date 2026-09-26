"""Tests for the MailGate command-line client (``mailgate-api``)."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from mailgate import cli
from mailgate.config import Settings


def _settings(tmp_keychain_service="mailgate-test") -> Settings:
    return Settings(keychain_service=tmp_keychain_service)


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        pass


def _capture_requests():
    captured = []

    def _fake_urlopen(req, *args, **kwargs):
        captured.append(req)
        return _FakeResponse({"status": "ok"})

    return captured, _fake_urlopen


def test_token_attached_to_every_request(monkeypatch):
    captured, fake = _capture_requests()
    monkeypatch.setattr(cli.urllib.request, "urlopen", fake)
    monkeypatch.setattr(cli, "get_api_token", lambda svc: "secret-token-123")

    cli.request(_settings(), "get", "/v1/messages")

    req = captured[0]
    assert req.get_header("Authorization") == "Bearer secret-token-123"
    assert req.full_url.endswith("/v1/messages")


def test_get_api_token_is_not_cached(monkeypatch):
    """The token must be read fresh on every request (deterministic)."""
    calls = []

    def fake_token(svc):
        calls.append(svc)
        return "tk"

    monkeypatch.setattr(cli, "get_api_token", fake_token)
    monkeypatch.setattr(
        cli.urllib.request, "urlopen", lambda *a, **k: _FakeResponse({"status": "ok"})
    )

    cli.request(_settings(), "get", "/healthz")
    cli.request(_settings(), "get", "/healthz")

    assert len(calls) == 2


def test_send_builds_json_body(monkeypatch):
    captured, fake = _capture_requests()
    monkeypatch.setattr(cli.urllib.request, "urlopen", fake)
    monkeypatch.setattr(cli, "get_api_token", lambda svc: "tk")

    cli.request(
        _settings(),
        "post",
        "/v1/messages/send",
        {"to": ["b@c.d"], "subject": "hello", "body": "hi there"},
    )

    req = captured[0]
    assert req.method == "POST"
    body = json.loads(req.data.decode("utf-8"))
    assert body == {"to": ["b@c.d"], "subject": "hello", "body": "hi there"}


def test_http_error_surfaces_detail(monkeypatch):
    def _raise(req, *a, **k):
        resp = _FakeResponse({"detail": "Access denied: sensitive"}, status=403)
        import urllib.error

        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", None, resp
        )

    monkeypatch.setattr(cli.urllib.request, "urlopen", _raise)
    monkeypatch.setattr(cli, "get_api_token", lambda svc: "tk")

    with pytest.raises(cli.ClientError, match="Access denied: sensitive"):
        cli.request(_settings(), "get", "/v1/messages/some-id")


def test_cmd_list_formats_human_readable(monkeypatch, capsys):
    payload = {
        "total": 2,
        "messages": [
            {
                "id": "1",
                "is_redacted": False,
                "from": "Alice <a@x.com>",
                "subject": "Hello",
                "date": "Mon, 25 Aug 2025 10:00:00 +0000",
            },
            {
                "id": "2",
                "is_redacted": True,
                "from": "[REDACTED]",
                "subject": "[REDACTED]",
                "date": "Mon, 25 Aug 2025 11:00:00 +0000",
            },
        ],
    }
    monkeypatch.setattr(cli, "request", lambda *a, **k: payload)

    from email.utils import parsedate_to_datetime

    assert cli.cmd_list(
        type("A", (), {"query": None, "unread": False, "limit": 20, "offset": 0, "json": False})(),
        _settings(),
    ) == 0
    out = capsys.readouterr().out
    assert "Alice" in out and "Hello" in out
    assert "[REDACTED]" in out
    assert "2 total." in out


@pytest.mark.parametrize("command,is_read", [("mark-read", True), ("mark-unread", False)])
@pytest.mark.parametrize("json_output", [False, True])
def test_mark_read_state_sends_patch(monkeypatch, capsys, command, is_read, json_output):
    captured = []
    payload = {"status": "updated", "id": "42", "is_read": is_read}

    def fake_urlopen(req):
        captured.append(req)
        return _FakeResponse(payload)

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cli, "get_api_token", lambda svc: "tk")
    monkeypatch.setattr(cli, "load_settings", _settings)

    argv = [command, "42"] + (["--json"] if json_output else [])
    assert cli.main(argv) == 0

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "PATCH"
    assert req.full_url.endswith("/v1/messages/42")
    assert req.get_header("Authorization") == "Bearer tk"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"is_read": is_read}
    assert json.loads(capsys.readouterr().out) == payload


@pytest.mark.parametrize("command", ["mark-read", "mark-unread"])
def test_mark_read_state_requires_message_id(command):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args([command])
    assert exc.value.code == 2


@pytest.mark.parametrize("command", ["mark-read", "mark-unread"])
def test_mark_read_state_surfaces_api_errors(monkeypatch, capsys, command):
    monkeypatch.setattr(cli, "load_settings", _settings)
    request = mock.Mock(side_effect=cli.ClientError("404 Not Found: Message not found"))
    monkeypatch.setattr(cli, "request", request)

    assert cli.main([command, "999"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert "error: 404 Not Found: Message not found" in output.err
    request.assert_called_once()


@pytest.mark.parametrize("is_read,status", [(True, "READ"), (False, "UNREAD")])
@pytest.mark.parametrize("is_redacted", [False, True])
def test_list_displays_read_state(monkeypatch, capsys, is_read, status, is_redacted):
    monkeypatch.setattr(cli, "load_settings", _settings)
    monkeypatch.setattr(cli, "request", lambda *a, **k: {
        "total": 1,
        "messages": [{"id": "1", "is_read": is_read, "is_redacted": is_redacted}],
    })

    assert cli.main(["list"]) == 0

    out = capsys.readouterr().out
    assert f"1  [{status}]" in out
    assert ("[REDACTED]" in out) is is_redacted


@pytest.mark.parametrize("is_read,status", [(True, "Read"), (False, "Unread"), (None, None)])
def test_read_displays_state_without_changing_it(monkeypatch, capsys, is_read, status):
    settings = _settings()
    payload = {"id": "1", "from": "Alice", "subject": "Hello", "body": "Message body"}
    if is_read is not None:
        payload["is_read"] = is_read
    request = mock.Mock(return_value=payload)
    monkeypatch.setattr(cli, "request", request)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    assert cli.main(["read", "1"]) == 0

    request.assert_called_once_with(settings, "get", "/v1/messages/1")
    out = capsys.readouterr().out
    assert "Message body" in out
    if status:
        assert f"Status:  {status}" in out
    else:
        assert "Status:" not in out
