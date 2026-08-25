"""Command-line client for the MailGate REST API.

This is the *safe entrypoint* for agents and humans alike: it reads the API
token from the credential store (Keychain or ``0600`` file) at call time and
attaches it as ``Authorization: Bearer …`` on every request. The token is
therefore never committed to a repo, placed in a ``.env``, or echoed into an
agent's working context.

Usage::

    mailgate-api list [--query Q] [--unread] [--limit N] [--offset N]
    mailgate-api read <message_id>
    mailgate-api search <query>
    mailgate-api send --to A [--to B] --subject S --body B | --body-file F
    mailgate-api reply <message_id> --body B [--reply-all]
    mailgate-api trash <message_id>
    mailgate-api healthz
    mailgate-api token

The service is expected to be running on ``127.0.0.1:8765`` (configurable via
``MAILGATE_BIND_HOST`` / ``MAILGATE_BIND_PORT``).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

from .config import Settings, load_settings
from .credentials import CredentialError, get_api_token

_HTTP_METHODS = {"get", "post", "delete"}


class ClientError(RuntimeError):
    """Raised when the MailGate API returns an error."""


def _base_url(settings: Settings) -> str:
    return f"http://{settings.bind_host}:{settings.bind_port}"


def request(settings: Settings, method: str, path: str, body: dict | None = None) -> dict:
    """Perform a single authenticated request against the MailGate API."""
    token = get_api_token(settings.keychain_service)
    url = f"{_base_url(settings)}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except json.JSONDecodeError:
            pass
        raise ClientError(f"{exc.code} {exc.reason}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ClientError(
            f"cannot reach MailGate at {_base_url(settings)} — is it running? "
            f"({exc.reason})"
        ) from exc


def _emit(obj: dict, pretty: bool) -> None:
    if pretty:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(obj, ensure_ascii=False))


def _fmt_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value


def cmd_list(args: argparse.Namespace, settings: Settings) -> int:
    params = {"limit": str(args.limit), "offset": str(args.offset)}
    if args.query:
        params["query"] = args.query
    if args.unread:
        params["unread_only"] = "true"
    qs = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in params.items())
    result = request(settings, "get", f"/v1/messages?{qs}")
    if args.json:
        _emit(result, pretty=False)
        return 0
    if not result.get("messages"):
        print("No messages.")
        return 0
    for m in result["messages"]:
        if m.get("is_redacted"):
            line = f"{m['id']}  [REDACTED]  {_fmt_date(m.get('date'))}"
        else:
            line = (
                f"{m['id']}  {_fmt_date(m.get('date'))}  "
                f"{(m.get('from') or '').split('<')[0].strip():<20}  {m.get('subject') or ''}"
            )
        print(line)
    print(f"\n{result.get('total', len(result['messages']))} total.")
    return 0


def cmd_read(args: argparse.Namespace, settings: Settings) -> int:
    result = request(settings, "get", f"/v1/messages/{args.message_id}")
    if args.json:
        _emit(result, pretty=True)
        return 0
    print(f"From:    {result.get('from')}")
    print(f"Subject: {result.get('subject')}")
    print(f"Date:    {result.get('date')}")
    print("-" * 60)
    print(result.get("body") or result.get("snippet") or "")
    return 0


def _read_body(args: argparse.Namespace) -> str:
    if args.body is not None:
        return args.body if args.body != "-" else sys.stdin.read()
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as fh:
            return fh.read()
    raise SystemExit("provide --body, --body-file, or --body - (stdin)")


def cmd_send(args: argparse.Namespace, settings: Settings) -> int:
    body = _read_body(args)
    result = request(
        settings,
        "post",
        "/v1/messages/send",
        {"to": args.to, "subject": args.subject, "body": body},
    )
    _emit(result, pretty=args.json)
    return 0


def cmd_reply(args: argparse.Namespace, settings: Settings) -> int:
    body = _read_body(args)
    result = request(
        settings,
        "post",
        f"/v1/messages/{args.message_id}/reply",
        {"body": body, "reply_all": bool(args.reply_all)},
    )
    _emit(result, pretty=args.json)
    return 0


def cmd_trash(args: argparse.Namespace, settings: Settings) -> int:
    result = request(settings, "delete", f"/v1/messages/{args.message_id}")
    _emit(result, pretty=args.json)
    return 0


def cmd_healthz(args: argparse.Namespace, settings: Settings) -> int:
    result = request(settings, "get", "/healthz")
    print(result.get("status", "unknown"))
    return 0


def cmd_token(args: argparse.Namespace, settings: Settings) -> int:
    print(get_api_token(settings.keychain_service))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailgate-api",
        description="Client for the air-gapped MailGate secure email intermediary.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list/search messages")
    p_list.add_argument("--query")
    p_list.add_argument("--unread", action="store_true")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_read = sub.add_parser("read", help="read a full message")
    p_read.add_argument("message_id")
    p_read.add_argument("--json", action="store_true")
    p_read.set_defaults(func=cmd_read)

    p_search = sub.add_parser("search", help="Gmail search query")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--json", action="store_true")
    p_search.set_defaults(func=lambda a, s: cmd_list(
        argparse.Namespace(query=a.query, unread=False, limit=a.limit, offset=0, json=a.json), s
    ))

    p_send = sub.add_parser("send", help="send an outbound email")
    p_send.add_argument("--to", action="append", required=True)
    p_send.add_argument("--subject", required=True)
    p_send.add_argument("--body")
    p_send.add_argument("--body-file")
    p_send.add_argument("--json", action="store_true")
    p_send.set_defaults(func=cmd_send)

    p_reply = sub.add_parser("reply", help="reply to an existing thread")
    p_reply.add_argument("message_id")
    p_reply.add_argument("--body")
    p_reply.add_argument("--body-file")
    p_reply.add_argument("--reply-all", action="store_true")
    p_reply.add_argument("--json", action="store_true")
    p_reply.set_defaults(func=cmd_reply)

    p_trash = sub.add_parser("trash", help="move a message to Trash")
    p_trash.add_argument("message_id")
    p_trash.add_argument("--json", action="store_true")
    p_trash.set_defaults(func=cmd_trash)

    p_health = sub.add_parser("healthz", help="check service health")
    p_health.set_defaults(func=cmd_healthz)

    p_token = sub.add_parser("token", help="print the API token")
    p_token.set_defaults(func=cmd_token)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        return args.func(args, settings)
    except CredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())