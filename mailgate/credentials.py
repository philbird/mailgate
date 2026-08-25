"""Credential storage via the macOS Keychain.

MailGate never reads credentials from files, environment variables, or API
responses. The Gmail App Password and the local API token live only in the
macOS Keychain, accessed through the ``security`` CLI.

Keychain layout (service = ``mailgate`` by default):

    account "gmail-address"      -> the Gmail address (e.g. phil.bird@gmail.com)
    account "gmail-app-password" -> the Gmail App Password
    account "api-token"          -> the local Bearer token for the REST API

The App Password is a Gmail *App Password* (16 chars), not the account
password, and can be revoked independently at any time.
"""

from __future__ import annotations

import secrets
import subprocess

ACCOUNT_ADDRESS = "gmail-address"
ACCOUNT_APP_PASSWORD = "gmail-app-password"
ACCOUNT_API_TOKEN = "api-token"


class CredentialError(RuntimeError):
    """Raised when a required credential is missing or unreadable."""


def _security(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["security", *args],
        capture_output=True,
        text=True,
    )


def set_secret(service: str, account: str, value: str) -> None:
    """Store (or overwrite) a secret in the login keychain."""
    # Remove any existing entry first so re-provisioning is idempotent.
    _security("delete-generic-password", "-a", account, "-s", service)
    result = _security(
        "add-generic-password",
        "-a", account,
        "-s", service,
        "-w", value,
        "-U",  # update if it already exists
    )
    if result.returncode != 0:
        raise CredentialError(f"keychain write failed: {result.stderr.strip()}")


def get_secret(service: str, account: str) -> str | None:
    """Read a secret from the login keychain, or ``None`` if absent."""
    result = _security(
        "find-generic-password",
        "-a", account,
        "-s", service,
        "-w",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def generate_api_token() -> str:
    """Generate a cryptographically random URL-safe API token."""
    return secrets.token_urlsafe(32)


# --- Convenience wrappers -------------------------------------------------

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
