"""FastAPI application exposing the MailGate REST API.

Security model:
  * Every request requires ``Authorization: Bearer <token>`` where the token
    is read from the macOS Keychain (never from a file or env var).
  * Sensitive messages (OTP/2FA/verification) are excluded from list/search
    results and hard-blocked (403) on read/update/reply/delete.
  * Credentials never appear in any response body.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from pydantic import BaseModel, Field, StrictBool

from . import classifier
from .config import Settings, load_settings
from .credentials import CredentialError, get_address, get_api_token, get_app_password
from .gmail import GmailClient, MessageNotFoundError

app = FastAPI(
    title="MailGate",
    description="Air-gapped secure email intermediary for Gmail.",
    version="0.1.0",
)

_settings: Settings = load_settings()


# --- Auth ----------------------------------------------------------------

def _require_token(authorization: str | None = Header(default=None)) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Malformed Authorization header")
    try:
        expected = get_api_token(_settings.keychain_service)
    except CredentialError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not _secrets_equal(token, expected):
        raise HTTPException(status_code=403, detail="Invalid token")


def _secrets_equal(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode(), b.encode())


def _client() -> GmailClient:
    try:
        address = get_address(_settings.keychain_service)
        app_password = get_app_password(_settings.keychain_service)
    except CredentialError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return GmailClient(
        address=address,
        app_password=app_password,
        imap_host=_settings.imap_host,
        smtp_host=_settings.smtp_host,
        smtp_port=_settings.smtp_port,
    )


# --- Request/response models --------------------------------------------

class SendRequest(BaseModel):
    to: list[str] = Field(..., min_length=1)
    subject: str
    body: str


class ReplyRequest(BaseModel):
    body: str
    reply_all: bool = False


class UpdateMessageRequest(BaseModel):
    is_read: StrictBool


# --- Endpoints -----------------------------------------------------------

@app.get("/v1/messages")
def list_messages(
    query: str | None = Query(default=None),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(_require_token),
) -> dict:
    client = _client()
    result = client.search(query=query, unread_only=unread_only, limit=limit, offset=offset)

    messages = []
    for meta in result["messages"]:
        preview = meta.pop("_preview", "")
        if classifier.is_sensitive(meta["subject"], preview, meta["from"]):
            messages.append(
                {
                    "id": meta["id"],
                    "is_redacted": True,
                    "subject": "[REDACTED]",
                    "from": "[REDACTED]",
                    "date": meta["date"],
                    "snippet": "",
                    "is_read": meta["is_read"],
                }
            )
        else:
            meta["is_redacted"] = False
            messages.append(meta)

    return {"total": result["total"], "messages": messages}


@app.get("/v1/messages/{message_id}")
def get_message(message_id: str, _: None = Depends(_require_token)) -> dict:
    client = _client()
    msg = client.fetch(message_id)
    if classifier.is_sensitive(msg["subject"], msg["body"], msg["from"]):
        raise HTTPException(
            status_code=403,
            detail="Access denied: Message contains sensitive security/OTP contents",
        )
    return msg


@app.patch("/v1/messages/{message_id}")
def update_message(
    req: UpdateMessageRequest,
    message_id: str = Path(pattern=r"^[1-9][0-9]*$"),
    _: None = Depends(_require_token),
) -> dict:
    client = _client()
    try:
        msg = client.fetch(message_id)
        if classifier.is_sensitive(msg["subject"], msg["body"], msg["from"]):
            raise HTTPException(
                status_code=403,
                detail="Access denied: Cannot update a sensitive security/OTP message",
            )
        client.set_read(message_id, req.is_read)
    except MessageNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Message not found") from exc

    return {"status": "updated", "id": message_id, "is_read": req.is_read}


@app.post("/v1/messages/{message_id}/reply")
def reply_message(
    message_id: str,
    req: ReplyRequest,
    _: None = Depends(_require_token),
) -> dict:
    client = _client()
    parent = client.fetch(message_id)
    if classifier.is_sensitive(parent["subject"], parent["body"], parent["from"]):
        raise HTTPException(
            status_code=403,
            detail="Access denied: Cannot reply to a sensitive security/OTP thread",
        )

    recipients = client.reply_recipients(message_id, req.reply_all)
    if not recipients:
        raise HTTPException(status_code=400, detail="No reply recipients resolved")

    subject = parent["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    client.send(
        to=recipients,
        subject=subject,
        body=req.body,
        in_reply_to=parent["message_id"] or None,
        references=parent["references"] or None,
    )
    return {"status": "sent", "to": recipients, "subject": subject}


@app.post("/v1/messages/send")
def send_message(req: SendRequest, _: None = Depends(_require_token)) -> dict:
    client = _client()
    client.send(to=req.to, subject=req.subject, body=req.body)
    return {"status": "sent", "to": req.to, "subject": req.subject}


@app.delete("/v1/messages/{message_id}")
def delete_message(message_id: str, _: None = Depends(_require_token)) -> dict:
    client = _client()
    msg = client.fetch(message_id)
    if classifier.is_sensitive(msg["subject"], msg["body"], msg["from"]):
        raise HTTPException(
            status_code=403,
            detail="Access denied: Cannot delete a sensitive security/OTP message",
        )
    client.trash(message_id)
    return {"status": "trashed", "id": message_id}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
