"""Tests for the credential backends (Keychain + file fallback)."""

from __future__ import annotations

import json
import os

import pytest

from mailgate import credentials as creds


@pytest.fixture()
def file_backend(monkeypatch, tmp_path):
    """Point the file backend at a temp path and clear any keychain fallback."""
    monkeypatch.setenv("MAILGATE_CREDENTIALS_FILE", str(tmp_path / "creds.json"))
    monkeypatch.setattr(creds, "_keychain_get", lambda service, account: None)
    return tmp_path / "creds.json"


def test_file_set_get_roundtrip(file_backend):
    creds.set_secret("mailgate", creds.ACCOUNT_ADDRESS, "me@gmail.com", creds.BACKEND_FILE)
    creds.set_secret("mailgate", creds.ACCOUNT_APP_PASSWORD, "abcdabcdabcdabcd", creds.BACKEND_FILE)
    assert creds.get_secret("mailgate", creds.ACCOUNT_ADDRESS) == "me@gmail.com"
    assert creds.get_secret("mailgate", creds.ACCOUNT_APP_PASSWORD) == "abcdabcdabcdabcd"


def test_file_permissions_0600(file_backend):
    creds.set_secret("mailgate", creds.ACCOUNT_ADDRESS, "me@gmail.com", creds.BACKEND_FILE)
    mode = os.stat(file_backend).st_mode & 0o777
    assert mode == 0o600


def test_file_overwrite_idempotent(file_backend):
    creds.set_secret("s", "k", "first", creds.BACKEND_FILE)
    creds.set_secret("s", "k", "second", creds.BACKEND_FILE)
    assert creds.get_secret("s", "k") == "second"


def test_file_service_namespacing(file_backend):
    creds.set_secret("svc-a", "k", "value-a", creds.BACKEND_FILE)
    creds.set_secret("svc-b", "k", "value-b", creds.BACKEND_FILE)
    assert creds.get_secret("svc-a", "k") == "value-a"
    assert creds.get_secret("svc-b", "k") == "value-b"


def test_get_secret_prefers_keychain(monkeypatch, file_backend):
    creds.set_secret("s", "k", "file-value", creds.BACKEND_FILE)
    monkeypatch.setattr(creds, "_keychain_get", lambda service, account: "keychain-value")
    assert creds.get_secret("s", "k") == "keychain-value"


def test_get_secret_falls_back_to_file(file_backend):
    creds.set_secret("s", "k", "file-value", creds.BACKEND_FILE)
    # keychain already patched to return None by the fixture
    assert creds.get_secret("s", "k") == "file-value"


def test_resolve_backend_forced(monkeypatch):
    monkeypatch.setattr(creds, "keychain_writable", lambda: False)
    assert creds.resolve_backend(creds.BACKEND_FILE) == creds.BACKEND_FILE
    assert creds.resolve_backend(creds.BACKEND_KEYCHAIN) == creds.BACKEND_KEYCHAIN


def test_resolve_backend_auto_falls_back_when_keychain_unwritable(monkeypatch):
    monkeypatch.setattr(creds, "keychain_writable", lambda: False)
    assert creds.resolve_backend(creds.BACKEND_AUTO) == creds.BACKEND_FILE


def test_resolve_backend_auto_uses_keychain_when_writable(monkeypatch):
    monkeypatch.setattr(creds, "keychain_writable", lambda: True)
    assert creds.resolve_backend(creds.BACKEND_AUTO) == creds.BACKEND_KEYCHAIN


def test_resolve_backend_rejects_unknown():
    with pytest.raises(creds.CredentialError):
        creds.resolve_backend("bogus")


def test_missing_secret_raises(file_backend):
    with pytest.raises(creds.CredentialError):
        creds.get_address("mailgate")