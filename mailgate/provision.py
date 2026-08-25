"""Provisioning CLI — store Gmail credentials + API token in the Keychain.

Run once per machine/account:

    mailgate-provision --email you@gmail.com

It prompts for the Gmail App Password (hidden input), stores the address and
App Password in the macOS Keychain, generates a fresh API token, stores that
too, and prints the token so the agent can be configured to use it.

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
        help="Keychain service name (default: mailgate)",
    )
    parser.add_argument(
        "--app-password",
        help="Gmail App Password (prompted securely if omitted)",
    )
    args = parser.parse_args()

    email = args.email or _prompt_email()

    # Pre-flight: fail fast (before prompting for the App Password) if the
    # keychain isn't writable from this session.
    try:
        credentials.ensure_keychain_writable()
    except credentials.CredentialError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    app_password = args.app_password or getpass.getpass("Gmail App Password: ")

    if not app_password:
        print("No App Password provided.", file=sys.stderr)
        sys.exit(1)

    credentials.set_secret(args.service, credentials.ACCOUNT_ADDRESS, email)
    credentials.set_secret(args.service, credentials.ACCOUNT_APP_PASSWORD, app_password)

    token = credentials.generate_api_token()
    credentials.set_secret(args.service, credentials.ACCOUNT_API_TOKEN, token)

    print("\nMailGate provisioned successfully.")
    print(f"  Keychain service : {args.service}")
    print(f"  Gmail address    : {email}")
    print(f"  API token        : {token}")
    print("\nStore this token in the agent's secure config. It is NOT written to disk.")
    print("Use it as:  Authorization: Bearer <token>")


if __name__ == "__main__":
    main()
