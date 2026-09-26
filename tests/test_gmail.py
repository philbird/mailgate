"""Tests for GmailClient preview decoding, fetching, and read status."""

from __future__ import annotations

import base64

import pytest
from imapclient import SEEN

from mailgate import classifier
from mailgate.gmail import (
    GmailClient,
    MessageNotFoundError,
    _HEADER_REQUEST,
    _TEXT_PREVIEW_REQUEST,
    _decode_preview,
)


def _split(raw: bytes) -> tuple[bytes, bytes]:
    """Split a raw RFC822 message the way IMAP does: HEADER vs TEXT."""
    headers, _, text = raw.partition(b"\r\n\r\n")
    return headers + b"\r\n", text


PLAIN = (
    b"From: Bob <bob@example.com>\r\n"
    b"Subject: Lunch\r\n"
    b"Date: Mon, 25 Aug 2025 10:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"See you at noon."
)


def _b64_message(body: str) -> bytes:
    return (
        b"From: Acme <no-reply@acme.example>\r\n"
        b"Subject: Hello\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n" + base64.b64encode(body.encode())
    )


def test_decode_preview_plain():
    msg, preview = _decode_preview(*_split(PLAIN))
    assert msg["Subject"] == "Lunch"
    assert "See you at noon." in preview


def test_decode_preview_base64_otp_is_classifiable():
    raw = _b64_message("Your verification code is 123456")
    headers, text = _split(raw)
    assert b"verification" not in text  # encoded on the wire
    msg, preview = _decode_preview(headers, text)
    assert "verification code" in preview
    assert classifier.is_sensitive(msg["Subject"], preview, msg["From"])


def test_decode_preview_quoted_printable_otp_is_classifiable():
    raw = (
        b"From: Acme <no-reply@acme.example>\r\n"
        b"Subject: Hello\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n"
        b"\r\n"
        b"Your verification co=\r\nde is 123456"  # soft break splits the word
    )
    msg, preview = _decode_preview(*_split(raw))
    assert "verification code" in preview
    assert classifier.is_sensitive(msg["Subject"], preview, msg["From"])


def test_decode_preview_truncated_base64_still_decodes():
    raw = _b64_message("Your verification code is 123456. " + "x" * 200)
    headers, text = _split(raw)
    _, preview = _decode_preview(headers, text[:-10])  # partial BODY[TEXT] fetch
    assert "verification code" in preview


def test_decode_preview_multipart_base64_html():
    html = base64.b64encode(b"<p>Your one-time passcode: <b>987654</b></p>")
    raw = (
        b"From: Acme <no-reply@acme.example>\r\n"
        b"Subject: Hello\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/alternative; boundary="XYZ"\r\n'
        b"\r\n"
        b"--XYZ\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n" + html + b"\r\n"
        b"--XYZ--\r\n"
    )
    msg, preview = _decode_preview(*_split(raw))
    assert "987654" in preview
    assert classifier.is_sensitive(msg["Subject"], preview, msg["From"])


class _FakeIMAP:
    """Answers fetch() keyed the way a real server responds (RFC 3501):
    PEEK stripped, and partial ranges keyed by origin octet only."""

    def __init__(self, raw: bytes, flags: tuple[bytes, ...] = ()):
        headers, text = _split(raw)
        self._data = {
            b"BODY[HEADER]": headers,
            b"BODY[TEXT]<0>": text,
            b"BODY[]": raw,
            b"FLAGS": flags,
            b"SEQ": 1,
        }
        self.requested: list[bytes] | None = None
        self.logged_out = False

    def fetch(self, uids, fields):
        self.requested = list(fields)
        return {uids[0]: dict(self._data)}

    def logout(self):
        self.logged_out = True


def test_fetch_meta_reads_server_response_keys():
    """Regression: the response key differs from the request string; looking
    the preview up by the request string silently yields an empty preview."""
    fake = _FakeIMAP(_b64_message("Your verification code is 123456"))
    client = GmailClient(address="me@gmail.com", app_password="x")
    meta = client._fetch_meta(fake, 42)
    assert fake.requested == [_HEADER_REQUEST, _TEXT_PREVIEW_REQUEST, b"FLAGS"]
    assert meta["subject"] == "Hello"
    assert "verification code" in meta["_preview"]
    assert meta["snippet"].startswith("Your verification code")


def test_fetch_meta_missing_uid_yields_empty_meta():
    fake = _FakeIMAP(PLAIN)
    fake.fetch = lambda uids, fields: {}
    client = GmailClient(address="me@gmail.com", app_password="x")
    meta = client._fetch_meta(fake, 99)
    assert meta["subject"] == ""
    assert meta["_preview"] == ""


@pytest.mark.parametrize(
    "flags, is_read", [((), False), ((b"\\Flagged",), False), ((SEEN, b"\\Answered"), True)]
)
def test_fetch_meta_read_status(flags, is_read):
    fake = _FakeIMAP(PLAIN, flags)
    client = GmailClient(address="me@gmail.com", app_password="x")
    assert client._fetch_meta(fake, 42)["is_read"] is is_read


@pytest.mark.parametrize(
    "flags, is_read", [((), False), ((b"\\Flagged",), False), ((SEEN, b"\\Answered"), True)]
)
def test_fetch_read_status_without_marking_read(monkeypatch, flags, is_read):
    fake = _FakeIMAP(PLAIN, flags)
    client = GmailClient(address="me@gmail.com", app_password="x")
    monkeypatch.setattr(client, "_connect_imap", lambda: fake)

    message = client.fetch("42")

    assert message["is_read"] is is_read
    assert message["subject"] == "Lunch"
    assert "See you at noon." in message["body"]
    assert fake.requested == [b"BODY.PEEK[]", b"FLAGS"]
    assert fake.logged_out


def test_fetch_missing_message_logs_out(monkeypatch):
    fake = _FakeIMAP(PLAIN)
    fake.fetch = lambda uids, fields: {}
    client = GmailClient(address="me@gmail.com", app_password="x")
    monkeypatch.setattr(client, "_connect_imap", lambda: fake)

    with pytest.raises(MessageNotFoundError):
        client.fetch("42")

    assert fake.logged_out


class _FlagIMAP:
    def __init__(
        self, flags: tuple[bytes, ...], *, missing: bool = False, fail: bool = False
    ):
        self.flags = set(flags)
        self.missing = missing
        self.fail = fail
        self.calls = []
        self.logged_out = False

    def add_flags(self, uids, flags):
        return self._update("add", uids, flags)

    def remove_flags(self, uids, flags):
        return self._update("remove", uids, flags)

    def _update(self, operation, uids, flags):
        self.calls.append((operation, uids, flags))
        if self.fail:
            raise RuntimeError("IMAP store failed")
        if self.missing:
            return {}
        if operation == "add":
            self.flags.update(flags)
        else:
            self.flags.difference_update(flags)
        return {uids[0]: tuple(self.flags)}

    def logout(self):
        self.logged_out = True


@pytest.mark.parametrize("is_read", [True, False])
@pytest.mark.parametrize("already_read", [True, False])
def test_set_read_preserves_other_flags_and_is_idempotent(monkeypatch, is_read, already_read):
    other_flags = (b"\\Flagged", b"\\Answered", b"custom-label")
    initial_flags = other_flags + ((SEEN,) if already_read else ())
    fake = _FlagIMAP(initial_flags)
    client = GmailClient(address="me@gmail.com", app_password="x")
    monkeypatch.setattr(client, "_connect_imap", lambda: fake)

    client.set_read("42", is_read)

    assert (SEEN in fake.flags) is is_read
    assert fake.flags - {SEEN} == set(other_flags)
    assert fake.calls == [("add" if is_read else "remove", [42], [SEEN])]
    assert fake.logged_out


@pytest.mark.parametrize("is_read", [True, False])
def test_set_read_missing_message_logs_out(monkeypatch, is_read):
    fake = _FlagIMAP((), missing=True)
    client = GmailClient(address="me@gmail.com", app_password="x")
    monkeypatch.setattr(client, "_connect_imap", lambda: fake)

    with pytest.raises(MessageNotFoundError):
        client.set_read("42", is_read)

    assert fake.logged_out


@pytest.mark.parametrize("is_read", [True, False])
def test_set_read_failure_logs_out(monkeypatch, is_read):
    fake = _FlagIMAP((), fail=True)
    client = GmailClient(address="me@gmail.com", app_password="x")
    monkeypatch.setattr(client, "_connect_imap", lambda: fake)

    with pytest.raises(RuntimeError, match="IMAP store failed"):
        client.set_read("42", is_read)

    assert fake.logged_out
