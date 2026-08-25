"""Credential storage for MailGate.

MailGate never reads credentials from files that an agent could see casually,
environment variables, or API responses. Secrets live in one of two backends:

    * ``keychain`` — the macOS login Keychain via the ``security`` CLI
      (preferred on a local Mac, where hardware-backed storage is available).
    * ``file``     — a ``0600`` JSON file at ``~/.hermes/mailgate/credentials.json``
      (fallback when the Keychain is unavailable — e.g. over SSH — or on non-macOS).

Layout (Keychain: ``service = MAILGATE_KEYCHAIN_SERVICE``, default ``mailgate``):

    account "gmail-address"      -> the Gmail address
    account "gmail-app-password" -> the Gmail App Password (16 chars, revocable)
    account "api-token"          -> the local Bearer token for the REST API

The file backend mirrors this as JSON: ``{"<service>": {"gmail-address": ...}}``.

The App Password is a Gmail *App Password*, never the account password.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path

ACCOUNT_ADDRESS = "gmail-address"
ACCOUNT_APP_PASSWORD = "gmail-app-password"
ACCOUNT_API_TOKEN = "api-token"

BACKEND_KEYCHAIN = "keychain"
BACKEND_FILE = "file"
BACKEND_AUTO = "auto"

_DEFAULT_FILE_PATH = "~/.hermes/mailgate/credentials.json"


class CredentialError(RuntimeError):
    """Raised when a required credential is missing or unreadable."""


# ---------------------------------------------------------------------------
# Keychain backend (macOS only)
# ---------------------------------------------------------------------------

def _security(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["security", *args], capture_output=True, text=True)


def _keychain_set(service: str, account: str, value: str) -> None:
    _security("delete-generic-password", "-a", account, "-s", service)
    result = _security(
        "add-generic-password",
        "-a", account,
        "-s", service,
        "-w", value,
        "-U",
    )
    if result.returncode != 0:
        raise CredentialError(
            "keychain write failed: "
            f"{result.stderr.strip()}\n\n"
            "This usually means the login keychain is locked or the session "
            "(e.g. SSH) lacks GUI access to the keychain. Either run locally "
            "and unlock it with:\n"
            "  security unlock-keychain ~/Library/Keychains/login.keychain-db\n"
            "or use the file backend: --backend file"
        )


def _keychain_get(service: str, account: str) -> str | None:
    result = _security("find-generic-password", "-a", account, "-s", service, "-w")
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def keychain_writable() -> bool:
    """Return True if the login keychain accepts writes from this session."""
    probe = "mailgate-probe"
    try:
        _keychain_set(probe, probe, "probe")
        return True
    except CredentialError:
        return False
    finally:
        _security("delete-generic-password", "-a", probe, "-s", probe)


# ---------------------------------------------------------------------------
# File backend (portable fallback)
# ---------------------------------------------------------------------------

def _file_path() -> Path:
    env = os.environ.get("MAILGATE_CREDENTIALS_FILE")
    if env:
        return Path(env).expanduser()
    return Path(_DEFAULT_FILE_PATH).expanduser()


def _file_read() -> dict:
    path = _file_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise CredentialError(f"credentials file unreadable: {path} ({exc})") from exc
    return data if isinstance(data, dict) else {}


def _file_write(data: dict) -> None:
    path = _file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open with 0600 from the start, then force it (covers pre-existing files).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(path, 0o600)


def _file_set(service: str, account: str, value: str) -> None:
    data = _file_read()
    data.setdefault(service, {})[account] = value
    _file_write(data)


def _file_get(service: str, account: str) -> str | None:
    data = _file_read()
    return data.get(service, {}).get(account)


# ---------------------------------------------------------------------------
# Backend resolution + unified interface
# ---------------------------------------------------------------------------

def resolve_backend(requested: str = BACKEND_AUTO) -> str:
    """Resolve the backend to use for writes.

    ``auto`` prefers the Keychain when writable and falls back to the file
    backend otherwise (SSH sessions, Linux, locked keychain, …).
    """
    if requested == BACKEND_AUTO:
        return BACKEND_KEYCHAIN if keychain_writable() else BACKEND_FILE
    if requested in (BACKEND_KEYCHAIN, BACKEND_FILE):
        return requested
    raise CredentialError(f"unknown backend: {requested!r}")


def set_secret(service: str, account: str, value: str, backend: str = BACKEND_AUTO) -> None:
    """Store (or overwrite) a secret in the chosen backend."""
    backend = resolve_backend(backend)
    if backend == BACKEND_KEYCHAIN:
        _keychain_set(service, account, value)
    else:
        _file_set(service, account, value)


def get_secret(service: str, account: str) -> str | None:
    """Read a secret, trying the Keychain first then the file backend.

    This lets the runtime read credentials regardless of which backend was
    used at provision time, and lets ``auto`` migrations work transparently.
    """
    value = _keychain_get(service, account)
    if value:
        return value
    return _file_get(service, account)


def generate_api_token() -> str:
    """Generate a cryptographically random URL-safe API token."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def get_address(service: str) -> str:
    value = get_secret(service, ACCOUNT_ADDRESS)
    if not value:
        raise CredentialError(
            "Gmail address not provisioned. Run `mailgate-provision` first."
        )
    return value


def get_app_password(service: str) -> str:
    value = get_secret(service, ACCOUNT_APP_PASSWORD)
    if not value:
        raise CredentialError(
            "Gmail App Password not provisioned. Run `mailgate-provision` first."
        )
    return value


def get_api_token(service: str) -> str:
    value = get_secret(service, ACCOUNT_API_TOKEN)
    if not value:
        raise CredentialError(
            "API token not provisioned. Run `mailgate-provision` first."
        )
    return value