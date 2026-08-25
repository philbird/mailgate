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