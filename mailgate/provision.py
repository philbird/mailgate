"""Provisioning CLI — store Gmail credentials + API token.

Run once per machine/account:

    mailgate-provision --email you@gmail.com

It prompts for the Gmail App Password (hidden input), stores the address,
App Password, and a freshly generated API token, then prints the token so the
agent can be configured to use it.

Credentials go to the macOS Keychain when available, or a ``0600`` file at
``~/.hermes/mailgate/credentials.json`` otherwise (SSH sessions, Linux). Pass
``--backend keychain|file|auto`` to override (default: ``auto``).

The App Password is a Gmail *App Password* (16 chars), created at
https://myaccount.google.com/apppasswords — never the account password.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import credentials


def _prompt_email() -> str:
    while True:
        value = input("Gmail address: ").strip()
        if "@" in value:
            return value
        print("Please enter a valid email address.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision MailGate credentials.")
    parser.add_argument("--email", help="Gmail address (prompted if omitted)")
    parser.add_argument(
        "--service",
        default="mailgate",
        help="Keychain service name / file namespace (default: mailgate)",
    )
    parser.add_argument(
        "--app-password",
        help="Gmail App Password (prompted securely if omitted)",
    )
    parser.add_argument(
        "--backend",
        choices=[credentials.BACKEND_AUTO, credentials.BACKEND_KEYCHAIN, credentials.BACKEND_FILE],
        default=credentials.BACKEND_AUTO,
        help="Credential backend (default: auto — Keychain if writable, else file)",
    )
    args = parser.parse_args()

    # Resolve the backend BEFORE prompting for any secret, so the user does
    # not type a password only to fail on a locked/unreachable keychain.
    backend = credentials.resolve_backend(args.backend)

    email = args.email or _prompt_email()
    app_password = args.app_password or getpass.getpass("Gmail App Password: ")

    if not app_password:
        print("No App Password provided.", file=sys.stderr)
        sys.exit(1)

    credentials.set_secret(args.service, credentials.ACCOUNT_ADDRESS, email, backend)
    credentials.set_secret(args.service, credentials.ACCOUNT_APP_PASSWORD, app_password, backend)

    token = credentials.generate_api_token()
    credentials.set_secret(args.service, credentials.ACCOUNT_API_TOKEN, token, backend)

    print("\nMailGate provisioned successfully.")
    print(f"  Backend          : {backend}")
    if backend == credentials.BACKEND_FILE:
        print(f"  Credentials file : {credentials._file_path()}")
    print(f"  Service          : {args.service}")
    print(f"  Gmail address    : {email}")
    print(f"  API token        : {token}")
    print("\nStore this token in the agent's secure config. It is NOT written to disk.")
    print("Send it as the Bearer token on every request.")


if __name__ == "__main__":
    main()