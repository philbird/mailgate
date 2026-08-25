"""Sensitive-email classification engine.

A message is classified ``SENSITIVE_LOCKED`` if it matches ANY rule:

  A. Subject/body regex patterns (case-insensitive) — OTP/2FA/verification
     wording, password resets, sign-in alerts, and standalone 4-8 digit codes
     surrounded by verification wording.
  B. High-risk sender domains — identity/cloud/financial senders that are
     inherently security-sensitive.

The classifier is deliberately conservative: over-blocking is safer than
leaking a one-time password to an agent. Rules are plain data at the top of
this module so they are trivial to audit and extend.
"""

from __future__ import annotations

import re

# --- Rule A: subject/body regex patterns (case-insensitive) ---------------

_PATTERNS: list[str] = [
    r"\b(otp|2fa|mfa|verification code|security code|one-time passcode|passcode)\b",
    r"\b(password reset|reset your password|confirm your email|verify your account)\b",
    r"\b(sign-in attempt|new login from|authorization code|login verification|new sign-in|new device|verify your device)\b",
    # Standalone 4-8 digit code surrounded by verification wording.
    r"(your code is|code:?)\s*(\d{4,8})",
]

_COMPILED: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in _PATTERNS
]

# --- Rule B: high-risk sender domains -------------------------------------

# Senders that are ALWAYS blocked, regardless of content. These are identity,
# cloud, and financial senders whose mail is inherently security-sensitive.
HIGH_RISK_DOMAINS: list[str] = [
    "accounts.google.com",
    "appleid.apple.com",
    "auth0.com",
    "okta.com",
    "paypal.com",
    "stripe.com",
    # Add banking / crypto / exchange domains here as needed, e.g.:
    # "chase.com", "barclays.co.uk", "coinbase.com",
]

# NOTE: ``github.com`` is intentionally NOT in the always-block list. GitHub
# sends a high volume of non-security mail (PR/issue notifications) that must
# not be hidden. GitHub *auth* alerts ("New sign-in to your account",
# "Please verify your device") are caught by the wording regexes above.


def _domain_of(sender: str) -> str:
    """Extract the bare domain from a From header value.

    Handles ``Name <addr@example.com>`` and bare ``addr@example.com``.
    """
    if not sender:
        return ""
    match = re.search(r"<([^>]+)>", sender)
    addr = match.group(1) if match else sender
    addr = addr.strip().lower()
    if "@" in addr:
        return addr.rsplit("@", 1)[1]
    return addr


def _matches_domain(domain: str, candidates: list[str]) -> bool:
    for candidate in candidates:
        if domain == candidate or domain.endswith("." + candidate):
            return True
    return False


def is_sensitive(subject: str, body: str, sender: str) -> bool:
    """Return True if the message should be locked/redacted.

    ``subject``, ``body``, and ``sender`` are the raw header/body strings.
    """
    text = f"{subject}\n{body}"

    for pattern in _COMPILED:
        if pattern.search(text):
            return True

    domain = _domain_of(sender)
    if _matches_domain(domain, HIGH_RISK_DOMAINS):
        return True

    return False
