---
name: mailgate
description: "Use when reading, searching, or sending Gmail via MailGate."
version: 1.0.0
author: philbird
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [email, gmail, imap, smtp, security, otp, airgap]
    related_skills: [himalaya, email-inbox-triage]
---

# MailGate — Air-Gapped Gmail Intermediary

MailGate is a local FastAPI service that brokers Gmail over IMAP/SMTP while
**redacting all OTP / 2FA / password-reset / security-verification messages**
so they can never leak into an agent's context. Credentials live only in the
macOS Keychain (or a `0600` file on SSH/Linux) — never in `.env` or API
responses.

Repo: https://github.com/philbird/mailgate

## When to use

- The user asks to read, search, reply to, send, or delete Gmail.
- Any task that touches a provisioned Gmail account.
- **Never** use this for OTP/2FA codes — those are deliberately blocked.

## Architecture

```
Agent ──HTTP──▶ MailGate (127.0.0.1:8765) ──IMAP/SMTP──▶ Gmail
                    │
                    ├─ macOS Keychain (App Password + API token)
                    └─ Sensitive-email classifier (regex + sender domains)
```

## Setup (one-time)

```bash
cd ~/SourceCode/mailgate
uv sync --extra dev
.venv/bin/mailgate-provision --email you@gmail.com
```

`mailgate-provision` prompts for the Gmail **App Password** (create at
https://myaccount.google.com/apppasswords), stores address + App Password +
a fresh API token, and prints the token. The backend is auto-detected: the
macOS Keychain (service `mailgate`) when writable, else a `0600` file at
`~/.hermes/mailgate/credentials.json` (SSH sessions, Linux). Override with
`--backend keychain|file|auto`.

The API token is read from the Keychain at runtime by the service. To call the
API yourself, read it with:

```bash
security find-generic-password -a api-token -s mailgate -w
```

## Run the service

```bash
cd ~/SourceCode/mailgate && .venv/bin/mailgate   # 127.0.0.1:8765
```

Or install the launchd agent (edit paths in the plist first):

```bash
cp launchd/com.hermes.mailgate.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hermes.mailgate.plist
```

## API

All endpoints (except `/healthz`) require `Authorization: Bearer <token>`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/v1/messages` | List/search. Params: `query` (Gmail syntax), `unread_only`, `limit` (default 20), `offset`. Sensitive messages returned masked (`is_redacted: true`, subject `[REDACTED]`). |
| `GET` | `/v1/messages/{id}` | Full message (HTML→Markdown). `403` if sensitive. |
| `POST` | `/v1/messages/{id}/reply` | Body `{"body": "...", "reply_all": false}`. `403` if thread sensitive. |
| `POST` | `/v1/messages/send` | Body `{"to": [...], "subject": "...", "body": "..."}`. |
| `DELETE` | `/v1/messages/{id}` | Move to Trash. `403` if sensitive. |
| `GET` | `/healthz` | Liveness (no auth). |

Message IDs are Gmail IMAP UIDs (stable per mailbox).

### Examples

```bash
TOKEN=$(security find-generic-password -a api-token -s mailgate -w)

# Unread inbox
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8765/v1/messages?unread_only=true&limit=10"

# Search (Gmail syntax)
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8765/v1/messages?query=from%3Afoo%40bar.com"

# Read one message
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8765/v1/messages/12345"

# Send
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"to":["bob@example.com"],"subject":"Hi","body":"Hello"}' \
  "http://127.0.0.1:8765/v1/messages/send"
```

## Security model (what the agent can and cannot do)

- **Redacted from list/search:** OTP/2FA/verification/password-reset messages
  appear as `is_redacted: true` with no subject/from/snippet.
- **Hard-blocked (403):** reading, replying to, or deleting a sensitive message.
- **Credentials never exposed:** no endpoint returns the App Password or token.
- **Loopback only:** binds `127.0.0.1`; never expose publicly.

Sensitive = matches any regex (`otp`, `2fa`, `mfa`, `verification code`,
`password reset`, `sign-in attempt`, `new device`, `your code is 123456`, …)
OR sender domain in the high-risk list (`accounts.google.com`,
`appleid.apple.com`, `auth0.com`, `okta.com`, `paypal.com`, `stripe.com`, …).
Rules are plain data at the top of `mailgate/classifier.py`.

## Pitfalls

- **Keychain lock / SSH:** macOS refuses login-keychain writes from SSH
  sessions ("User interaction is not allowed"). `mailgate-provision` auto-falls
  back to the `0600` file backend in that case, so provisioning over SSH just
  works. To force the Keychain, run locally and unlock first:
  `security unlock-keychain ~/Library/Keychains/login.keychain-db`.
- **App Password, not account password:** Gmail App Passwords are 16 chars,
  revocable independently. Never use the account password.
- **`github.com` is not always-blocked** (it sends lots of non-security mail);
  GitHub *auth* alerts are still caught by wording regexes.
- **List vs read classification:** list uses subject + first ~4 KB; read
  re-checks the full body as a backstop, so a code buried deep is still
  blocked on read.
- **Service must be running:** the API is a separate process. If calls
  connection-refuse, start it (or check the launchd agent is loaded).
