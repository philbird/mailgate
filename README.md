# MailGate

An **air-gapped secure email intermediary for Gmail**. MailGate exposes a
small local REST API that gives an AI agent (or any program) full email
capabilities — read, search, reply, send, trash — while **strictly isolating
credentials** and **redacting all OTP / 2FA / password-reset / security
verification messages** so they can never leak into an agent's context.

Built for [Hermes Agent](https://hermes-agent.nousresearch.com/docs) but
usable by any program that can speak HTTP.

## Why

Giving an LLM agent raw access to a Gmail inbox is dangerous: one-time
passwords, 2FA codes, and password-reset links are exactly the kind of
high-value secrets that should never enter a model's context window. MailGate
sits between the agent and Gmail and enforces a hard policy:

1. **Zero credential exposure** — the Gmail App Password and the local API
   token live only in the macOS Keychain (or a `0600` file on non-macOS / SSH
   sessions). They are never placed in `.env` files and never returned by any
   API response.
2. **Deterministic OTP airgap** — messages matching security rules are
   intercepted *before* the agent sees them: excluded from list/search views
   (or returned masked as `[REDACTED]`), and hard-blocked on read.
3. **Protected mutations** — the agent cannot reply to, delete, or trash a
   flagged security/OTP message.

## Architecture

```
Agent ──HTTP──▶ MailGate (FastAPI, 127.0.0.1:8765) ──IMAP/SMTP──▶ Gmail
                    │
                    ├─ macOS Keychain (or 0600 file) — App Password + API token
                    └─ Sensitive-email classifier (regex + sender domains)
```

- **Runtime:** Python 3.11+, FastAPI, Uvicorn
- **Email:** IMAP (`imapclient`) for read/search/trash, SMTP (`smtplib`) for
  send/reply, over TLS with a Gmail **App Password**
- **Credentials:** macOS Keychain via the `security` CLI, with a portable
  `0600` file fallback (`~/.hermes/mailgate/credentials.json`)
- **Service:** `launchd` plist for background execution

## Install

```bash
git clone https://github.com/philbird/mailgate.git
cd mailgate
uv sync --extra dev        # or: python -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## Provision credentials

Create a Gmail **App Password** (not your account password) at
<https://myaccount.google.com/apppasswords>, then run:

```bash
mailgate-provision --email you@gmail.com
```

It prompts for the App Password (hidden input), stores the address, App
Password, and a fresh API token, then prints the token. Credentials go to the
macOS Keychain when writable, or a `0600` file otherwise (SSH sessions, Linux).
Force a backend with `--backend keychain|file|auto` (default `auto`). **The
token is never returned by the API** — copy it into your agent's secure config.

To rotate the token or change the account, just re-run the command.

## Run

```bash
mailgate                 # serves on 127.0.0.1:8765
```

Or install the launchd service (edit the paths in the plist first):

```bash
cp launchd/com.hermes.mailgate.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hermes.mailgate.plist
```

## Client CLI

`mailgate-api` is the agent-safe client: it reads the API token from the
credential store at call time and attaches it automatically, so the token
never lands in `.env`, memory, or an agent's working context.

```bash
mailgate-api list                     # latest messages (readable table)
mailgate-api list --unread --limit 10
mailgate-api list --json              # machine-readable JSON
mailgate-api search "from:foo@bar.com"
mailgate-api read <message_id>        # full body (403 if sensitive)
mailgate-api send --to a@b.c --subject "Hi" --body "Hello"
mailgate-api send --to a@b.c --subject "Hi" --body-file notes.md
mailgate-api reply <message_id> --body "Thanks" [--reply-all]
mailgate-api trash <message_id>       # move to Trash (403 if sensitive)
mailgate-api healthz
mailgate-api token                    # print the token (rarely needed)
```

`send`/`reply` accept `--body`, `--body-file`, or `--body -` (stdin).
`list`/`read`/`search` accept `--json` for machine-readable output.

## API

All endpoints (except `/healthz`) require an `Authorization: Bearer <token>` token.

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/messages` | Search/list. Params: `query` (Gmail syntax), `unread_only`, `limit`, `offset`. Sensitive messages are returned masked (`is_redacted: true`). |
| `GET` | `/v1/messages/{id}` | Full message (HTML/plain → Markdown). `403` if sensitive. |
| `POST` | `/v1/messages/{id}/reply` | Reply to a thread. Body `{"body": "...", "reply_all": false}`. `403` if the thread is sensitive. |
| `POST` | `/v1/messages/send` | Send. Body `{"to": [...], "subject": "...", "body": "..."}`. |
| `DELETE` | `/v1/messages/{id}` | Move to Trash. `403` if sensitive. |
| `GET` | `/healthz` | Liveness check (no auth). |

### Example

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8765/v1/messages?unread_only=true&limit=10"
```

## Sensitive-email classification

A message is `SENSITIVE_LOCKED` if it matches **any** rule:

**A. Subject/body regex (case-insensitive)**
- `otp`, `2fa`, `mfa`, `verification code`, `security code`, `one-time passcode`, `passcode`
- `password reset`, `reset your password`, `confirm your email`, `verify your account`
- `sign-in attempt`, `new login from`, `authorization code`, `login verification`
- standalone 4–8 digit codes: `your code is 123456`, `code: 482910`

**B. High-risk sender domains** (always blocked)
- `accounts.google.com`, `appleid.apple.com`, `auth0.com`, `okta.com`,
  `paypal.com`, `stripe.com` — extend the list in
  `mailgate/classifier.py` for banks/exchanges.

> `github.com` is intentionally **not** always-blocked (it sends lots of
> non-security mail). GitHub *auth* alerts are still caught by the wording
> regexes ("New sign-in to your account", etc.).

The classifier is deliberately conservative — over-blocking is safer than
leaking a one-time password. Rules are plain data at the top of
`mailgate/classifier.py`, trivial to audit and extend.

## Security notes

- The service binds to `127.0.0.1` only. Do not expose it publicly.
- Credentials are stored in the macOS Keychain under service `mailgate`
  (accounts `gmail-address`, `gmail-app-password`, `api-token`).
- List-view classification uses the subject + first ~4 KB of the text body;
  the read endpoint re-classifies on the **full** body as a backstop, so a
  code buried deep in a message is still blocked on read.
- Token comparison is constant-time (`hmac.compare_digest`).

## Development

```bash
uv run pytest
```

## Hermes skill

A ready-to-install [Hermes Agent](https://hermes-agent.nousresearch.com/docs)
skill lives at [`skills/mailgate/SKILL.md`](skills/mailgate/SKILL.md) — see
[`skills/README.md`](skills/README.md) for one-command install.

## License

MIT
