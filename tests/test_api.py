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
                "is_read": False,
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
                "is_read": False,
            },
        }
        self.trashed = []
        self.sent = []
        self.read_updates = []

    def search(self, query=None, unread_only=False, limit=20, offset=0):
        metas = [
            {
                "id": mid,
                "subject": m["subject"],
                "from": m["from"],
                "date": m["date"],
                "snippet": m["body"][:50],
                "_preview": m["body"][:4000],
                "is_read": m["is_read"],
            }
            for mid, m in self.messages.items()
            if not unread_only or not m["is_read"]
        ]
        return {"total": len(metas), "messages": metas[offset : offset + limit]}

    def fetch(self, message_id):
        if message_id not in self.messages:
            raise api.MessageNotFoundError(message_id)
        return self.messages[message_id].copy()

    def set_read(self, message_id, is_read):
        if message_id not in self.messages:
            raise api.MessageNotFoundError(message_id)
        self.messages[message_id]["is_read"] = is_read
        self.read_updates.append((message_id, is_read))

    def trash(self, message_id):
        self.trashed.append(message_id)

    def send(self, to, subject, body, in_reply_to=None, references=None):
        self.sent.append({"to": to, "subject": subject, "body": body})

    def reply_recipients(self, message_id, reply_all):
        return ["bob@example.com"]


@pytest.fixture()
def gmail_client():
    return FakeGmailClient()


@pytest.fixture()
def client(monkeypatch, gmail_client):
    monkeypatch.setattr(api, "get_address", lambda service: "me@gmail.com")
    monkeypatch.setattr(api, "get_app_password", lambda service: "app-password")
    monkeypatch.setattr(api, "get_api_token", lambda service: "test-token")
    monkeypatch.setattr(api, "GmailClient", lambda **kwargs: gmail_client)
    return TestClient(api.app)


AUTH = {"Authorization": "Bearer test-token"}


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_missing_token(client):
    assert client.get("/v1/messages").status_code == 401


def test_invalid_token(client):
    r = client.get("/v1/messages", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


@pytest.mark.parametrize("is_read", [True, False])
def test_list_redacts_sensitive(client, gmail_client, is_read):
    gmail_client.messages["2"]["is_read"] = is_read
    r = client.get("/v1/messages", headers=AUTH)
    assert r.status_code == 200
    messages = r.json()["messages"]
    by_id = {m["id"]: m for m in messages}
    assert by_id["1"]["is_redacted"] is False
    assert by_id["2"]["is_redacted"] is True
    assert by_id["2"]["subject"] == "[REDACTED]"
    assert by_id["1"]["is_read"] is False
    assert by_id["2"]["is_read"] is is_read


def test_read_sensitive_blocked(client):
    r = client.get("/v1/messages/2", headers=AUTH)
    assert r.status_code == 403


def test_read_normal_ok(client):
    r = client.get("/v1/messages/1", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["subject"] == "Meeting tomorrow"
    assert r.json()["is_read"] is False


@pytest.mark.parametrize("is_read", [True, False])
def test_update_read_state(client, gmail_client, is_read):
    gmail_client.messages["1"]["is_read"] = not is_read

    r = client.patch("/v1/messages/1", json={"is_read": is_read}, headers=AUTH)

    assert r.status_code == 200
    assert r.json() == {"status": "updated", "id": "1", "is_read": is_read}
    assert gmail_client.messages["1"]["is_read"] is is_read
    assert gmail_client.read_updates == [("1", is_read)]


def test_update_read_state_is_visible_in_reads_and_unread_filter(client, gmail_client):
    for is_read in (True, False):
        updated = client.patch("/v1/messages/1", json={"is_read": is_read}, headers=AUTH)
        assert updated.status_code == 200

        fetched = client.get("/v1/messages/1", headers=AUTH)
        assert fetched.status_code == 200
        assert fetched.json()["is_read"] is is_read

        listed = client.get("/v1/messages", headers=AUTH).json()
        assert listed["total"] == 2
        by_id = {msg["id"]: msg for msg in listed["messages"]}
        assert by_id["1"]["is_read"] is is_read

        unread = client.get("/v1/messages?unread_only=true", headers=AUTH).json()
        expected_ids = {"2"} if is_read else {"1", "2"}
        assert {msg["id"] for msg in unread["messages"]} == expected_ids
        assert unread["total"] == len(expected_ids)
        assert gmail_client.messages["1"]["is_read"] is is_read

    assert gmail_client.read_updates == [("1", True), ("1", False)]


@pytest.mark.parametrize("is_read", [True, False])
def test_update_read_state_is_idempotent(client, gmail_client, is_read):
    gmail_client.messages["1"]["is_read"] = is_read
    for _ in range(2):
        r = client.patch("/v1/messages/1", json={"is_read": is_read}, headers=AUTH)
        assert r.status_code == 200
        assert gmail_client.messages["1"]["is_read"] is is_read


@pytest.mark.parametrize(
    ("headers", "status_code"),
    [({}, 401), ({"Authorization": "Basic test-token"}, 401),
     ({"Authorization": "Bearer wrong"}, 403)],
)
def test_update_read_state_requires_auth(client, gmail_client, headers, status_code):
    r = client.patch("/v1/messages/1", json={"is_read": True}, headers=headers)
    assert r.status_code == status_code
    assert gmail_client.read_updates == []
    assert gmail_client.messages["1"]["is_read"] is False


@pytest.mark.parametrize("is_read", [True, False])
def test_update_sensitive_read_state_blocked(client, gmail_client, is_read):
    gmail_client.messages["2"]["is_read"] = not is_read
    r = client.patch("/v1/messages/2", json={"is_read": is_read}, headers=AUTH)
    assert r.status_code == 403
    assert gmail_client.read_updates == []
    assert gmail_client.messages["2"]["is_read"] is not is_read


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject", "Your verification code"),
        ("from", "security@accounts.google.com"),
        ("body", "Meeting notes. " * 400 + "Your verification code is 123456"),
    ],
)
def test_update_classifies_full_message(client, gmail_client, field, value):
    gmail_client.messages["1"][field] = value
    if field == "body":
        # List classification cannot see the code beyond its preview window.
        listed = client.get("/v1/messages", headers=AUTH).json()["messages"]
        assert next(msg for msg in listed if msg["id"] == "1")["is_redacted"] is False

    r = client.patch("/v1/messages/1", json={"is_read": True}, headers=AUTH)
    assert r.status_code == 403
    assert gmail_client.read_updates == []
    assert gmail_client.messages["1"]["is_read"] is False


@pytest.mark.parametrize("is_read", ["true", "false", 1, 0, 1.0, None, [], {}])
def test_update_rejects_non_boolean_state(client, gmail_client, is_read):
    r = client.patch("/v1/messages/1", json={"is_read": is_read}, headers=AUTH)
    assert r.status_code == 422
    assert gmail_client.read_updates == []


def test_update_requires_read_state(client, gmail_client):
    r = client.patch("/v1/messages/1", json={}, headers=AUTH)
    assert r.status_code == 422
    assert gmail_client.read_updates == []


@pytest.mark.parametrize("message_id", ["0", "-1", "1.0", "abc", "1,2", "1:2", "*"])
def test_update_rejects_invalid_uid(client, gmail_client, message_id):
    r = client.patch(f"/v1/messages/{message_id}", json={"is_read": True}, headers=AUTH)
    assert r.status_code == 422
    assert gmail_client.read_updates == []


def test_update_missing_message(client, gmail_client):
    r = client.patch("/v1/messages/999", json={"is_read": True}, headers=AUTH)
    assert r.status_code == 404
    assert r.json() == {"detail": "Message not found"}
    assert gmail_client.read_updates == []


def test_update_message_disappears_after_fetch(client, gmail_client, monkeypatch):
    def missing_on_update(message_id, is_read):
        raise api.MessageNotFoundError(message_id)

    monkeypatch.setattr(gmail_client, "set_read", missing_on_update)
    r = client.patch("/v1/messages/1", json={"is_read": True}, headers=AUTH)
    assert r.status_code == 404
    assert r.json() == {"detail": "Message not found"}
    assert gmail_client.messages["1"]["is_read"] is False


def test_listing_and_fetching_do_not_mark_read(client, gmail_client):
    assert client.get("/v1/messages", headers=AUTH).status_code == 200
    for _ in range(2):
        assert client.get("/v1/messages/1", headers=AUTH).json()["is_read"] is False
    assert gmail_client.messages["1"]["is_read"] is False
    assert gmail_client.read_updates == []


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
