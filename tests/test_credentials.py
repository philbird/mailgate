"""Tests for the credential backends (Keychain + file fallback)."""

from __future__ import annotations

import json
import os

import pytest

from mailgate import credentials as creds


@pytest.fixture()
def file_backend(monkeypatch, tmp_path):
    """Point the file backend at a temp path and isolate the real keychain."""
    monkeypatch.setenv("MAILGATE_CREDENTIALS_FILE", str(tmp_path / "creds.json"))
    monkeypatch.setattr(creds, "_keychain_get", lambda service, account: None)
    monkeypatch.setattr(creds, "_keychain_delete", lambda service, account: None)
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


def test_security_cli_missing_is_graceful(monkeypatch):
    """Non-macOS has no `security` binary; the keychain backend must degrade
    to "unavailable" instead of crashing every credential read."""
    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("security")

    monkeypatch.setattr(creds.subprocess, "run", raise_fnf)
    assert creds.keychain_writable() is False
    assert creds._keychain_get("s", "k") is None
    assert creds.resolve_backend(creds.BACKEND_AUTO) == creds.BACKEND_FILE


def test_file_provision_clears_stale_keychain(monkeypatch, tmp_path):
    """Re-provisioning to the file backend must remove the old Keychain entry,
    otherwise get_secret (Keychain-first) keeps serving the stale token."""
    monkeypatch.setenv("MAILGATE_CREDENTIALS_FILE", str(tmp_path / "creds.json"))
    keychain = {("s", "api-token"): "old-token"}
    monkeypatch.setattr(creds, "_keychain_get", lambda s, a: keychain.get((s, a)))
    monkeypatch.setattr(creds, "_keychain_delete", lambda s, a: keychain.pop((s, a), None))

    creds.set_secret("s", "api-token", "new-token", creds.BACKEND_FILE)
    assert keychain == {}
    assert creds.get_secret("s", "api-token") == "new-token"


def test_keychain_provision_clears_stale_file(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILGATE_CREDENTIALS_FILE", str(tmp_path / "creds.json"))
    keychain = {}
    monkeypatch.setattr(creds, "_keychain_get", lambda s, a: keychain.get((s, a)))
    monkeypatch.setattr(creds, "_keychain_set", lambda s, a, v: keychain.__setitem__((s, a), v))
    monkeypatch.setattr(creds, "_keychain_delete", lambda s, a: keychain.pop((s, a), None))

    creds.set_secret("s", "k", "file-value", creds.BACKEND_FILE)
    creds.set_secret("s", "k", "keychain-value", creds.BACKEND_KEYCHAIN)
    assert creds._file_get("s", "k") is None
    assert creds.get_secret("s", "k") == "keychain-value"