# Hermes Skill

This repo ships a [Hermes Agent](https://hermes-agent.nousresearch.com/docs)
skill at [`skills/mailgate/SKILL.md`](mailgate/SKILL.md) so other Hermes users
can adopt MailGate with a single copy.

## Install

Copy `SKILL.md` into your active profile's skills directory:

```bash
mkdir -p ~/.hermes/skills/email
curl -fsSL \
  "https://raw.githubusercontent.com/philbird/mailgate/main/skills/mailgate/SKILL.md" \
  -o ~/.hermes/skills/email/mailgate.md
```

(If you run a named profile, the directory is
`~/.hermes/profiles/<name>/skills/email/` instead.)

The skill documents setup, the API contract, the security model, and the
agent-usage patterns — everything an agent needs to drive MailGate without
touching a single credential.

## Why ship the skill in the repo?

- **Single source of truth** — the skill evolves with the code it describes,
  and the API contract can't drift out of sync.
- **Quick start** — a new user going from "cloned the repo" to "agent can read
  my email" is two commands (`uv sync` + `mailgate-provision`) plus one copy
  of `SKILL.md`.
- **Installs from GitHub** — the raw-URL path above is the same convention the
  Hermes skills ecosystem uses to index and install community skills.