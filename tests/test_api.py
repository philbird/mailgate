"""API tests using a fake Gmail client and monkeypatched keychain access."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import mailgate.api as api


class FakeGmailClient:
    def __init__(self, *args, **kwargs):
        self.messages = {
            "1": {
                "id": "1",
                "subject": "Meeting tomorrow",
                "from": "bob@example.com",
                "to": "me@gmail.com",
                "cc": "",
                "date": "Mon, 1 Jan 2024 10:00:00 +0000",
                "message_id": "<meeting@example.com>",
                "references": "",
                "body": "Let's sync at 10am.",
                "content_type": "text",
            },
            "2": {
                "id": "2",
                "subject": "Your verification code",
                "from": "no-reply@accounts.google.com",
                "to": "me@gmail.com",
                "cc": "",
                "date": "Mon, 1 Jan 2024 11:00:00 +0000",
                "message_id": "<otp@google.com>",
                "references": "",
                "body": "Your code is 123456",
                "content_type": "text",
            },
        }
        self.trashed = []
        self.sent = []

    def search(self, query=None, unread_only=False, limit=20, offset=0):
        metas = [
            {
                "id": mid,
                "subject": m["subject"],
                "from": m["from"],
                "date": m["date"],
                "snippet": m["body"][:50],
                "_preview": m["body"],
            }
            for mid, m in self.messages.items()
        ]
        return {"total": len(metas), "messages": metas}

    def fetch(self, message_id):
        return self.messages[message_id]

    def trash(self, message_id):
        self.trashed.append(message_id)

    def send(self, to, subject, body, in_reply_to=None, references=None):
        self.sent.append({"to": to, "subject": subject, "body": body})

    def reply_recipients(self, message_id, reply_all):
        return ["bob@example.com"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api, "get_address", lambda service: "me@gmail.com")
    monkeypatch.setattr(api, "get_app_password", lambda service: "app-password")
    monkeypatch.setattr(api, "get_api_token", lambda service: "test-token")
    monkeypatch.setattr(api, "GmailClient", FakeGmailClient)
    return TestClient(api.app)


AUTH = {"Authorization": "Bearer test-token"}


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_missing_token(client):
    assert client.get("/v1/messages").status_code == 401


def test_invalid_token(client):
    r = client.get("/v1/messages", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


def test_list_redacts_sensitive(client):
    r = client.get("/v1/messages", headers=AUTH)
    assert r.status_code == 200
    messages = r.json()["messages"]
    by_id = {m["id"]: m for m in messages}
    assert by_id["1"]["is_redacted"] is False
    assert by_id["2"]["is_redacted"] is True
    assert by_id["2"]["subject"] == "[REDACTED]"


def test_read_sensitive_blocked(client):
    r = client.get("/v1/messages/2", headers=AUTH)
    assert r.status_code == 403


def test_read_normal_ok(client):
    r = client.get("/v1/messages/1", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["subject"] == "Meeting tomorrow"


def test_reply_sensitive_blocked(client):
    r = client.post("/v1/messages/2/reply", json={"body": "hi"}, headers=AUTH)
    assert r.status_code == 403


def test_reply_normal_ok(client):
    r = client.post("/v1/messages/1/reply", json={"body": "hi"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "sent"


def test_delete_sensitive_blocked(client):
    r = client.delete("/v1/messages/2", headers=AUTH)
    assert r.status_code == 403


def test_delete_normal_ok(client):
    r = client.delete("/v1/messages/1", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "trashed"


def test_send(client):
    r = client.post(
        "/v1/messages/send",
        json={"to": ["bob@example.com"], "subject": "Hi", "body": "Hello"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
