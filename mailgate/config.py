"""Runtime configuration for MailGate.

All values are read from environment variables with safe defaults. No
credentials live here — they are stored exclusively in the macOS Keychain
(see ``mailgate.credentials``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # Gmail connection
    imap_host: str = _env("MAILGATE_IMAP_HOST", "imap.gmail.com")
    smtp_host: str = _env("MAILGATE_SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(_env("MAILGATE_SMTP_PORT", "465"))

    # HTTP service binding (loopback only — never expose publicly)
    bind_host: str = _env("MAILGATE_BIND_HOST", "127.0.0.1")
    bind_port: int = int(_env("MAILGATE_BIND_PORT", "8765"))

    # Keychain service name used to store credentials
    keychain_service: str = _env("MAILGATE_KEYCHAIN_SERVICE", "mailgate")

    # Default list page size
    default_limit: int = int(_env("MAILGATE_DEFAULT_LIMIT", "20"))
    max_limit: int = int(_env("MAILGATE_MAX_LIMIT", "100"))


def load_settings() -> Settings:
    return Settings()
