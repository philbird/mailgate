"""Gmail connector — IMAP for read/search/trash, SMTP for send/reply.

Uses an App Password (not the account password) and connects over TLS. All
message IDs exposed to the API are Gmail IMAP UIDs (stable per mailbox).
"""

from __future__ import annotations

import smtplib
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses

import html2text
from imapclient import IMAPClient

# Bytes of the text body fetched for list-view classification. Keeps list
# queries cheap (no attachments) while still catching OTP codes that appear
# near the top of a message. The read endpoint re-classifies on the full body.
_PREVIEW_BYTES = 4000

# Full headers (not HEADER.FIELDS) so the preview can be MIME-decoded: the
# top-level Content-Type/Content-Transfer-Encoding (and multipart boundary)
# are required to turn BODY[TEXT] bytes into classifiable text.
_HEADER_REQUEST = b"BODY.PEEK[HEADER]"
_HEADER_RESPONSE = b"BODY[HEADER]"
# RFC 3501: partial-fetch responses drop PEEK and the length — a request for
# BODY.PEEK[TEXT]<0.4000> comes back keyed as BODY[TEXT]<0>.
_TEXT_PREVIEW_REQUEST = b"BODY.PEEK[TEXT]<0.%d>" % _PREVIEW_BYTES
_TEXT_PREVIEW_RESPONSE = b"BODY[TEXT]<0>"


class GmailClient:
    def __init__(
        self,
        address: str,
        app_password: str,
        imap_host: str = "imap.gmail.com",
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 465,
    ) -> None:
        self.address = address
        self.app_password = app_password
        self.imap_host = imap_host
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port

    # --- IMAP -------------------------------------------------------------

    def _connect_imap(self) -> IMAPClient:
        client = IMAPClient(self.imap_host, ssl=True)
        client.login(self.address, self.app_password)
        client.select_folder("INBOX")
        return client

    def search(
        self,
        query: str | None = None,
        unread_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Return ``{"total": int, "messages": [meta, ...]}`` newest-first.

        ``query`` is Gmail's native search syntax (e.g. ``from:foo@bar.com``).
        """
        client = self._connect_imap()
        try:
            if query:
                gmail_query = query
                if unread_only:
                    gmail_query = f"({query}) is:unread"
                uids = client.search(["X-GM-RAW", gmail_query])
            else:
                criteria = ["UNSEEN"] if unread_only else ["ALL"]
                uids = client.search(criteria)

            uids = list(reversed(uids))  # newest first
            total = len(uids)
            page = uids[offset : offset + limit]

            messages = [self._fetch_meta(client, uid) for uid in page]
            return {"total": total, "messages": messages}
        finally:
            client.logout()

    def _fetch_meta(self, client: IMAPClient, uid: int) -> dict:
        data = client.fetch([uid], [_HEADER_REQUEST, _TEXT_PREVIEW_REQUEST])
        raw = data.get(uid, {})
        headers = raw.get(_HEADER_RESPONSE, b"")
        text = raw.get(_TEXT_PREVIEW_RESPONSE, b"")

        msg, preview = _decode_preview(headers, text)
        return {
            "id": str(uid),
            "subject": msg.get("Subject", "") or "",
            "from": msg.get("From", "") or "",
            "date": msg.get("Date", "") or "",
            "snippet": preview[:200],
            # Internal — used for classification, stripped before response.
            "_preview": preview,
        }

    def fetch(self, message_id: str) -> dict:
        """Fetch a full message by UID, returning a normalized dict."""
        uid = int(message_id)
        client = self._connect_imap()
        try:
            data = client.fetch([uid], [b"BODY.PEEK[]"])
            raw = data[uid][b"BODY[]"]
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            return self._message_to_dict(uid, msg)
        finally:
            client.logout()

    def trash(self, message_id: str) -> None:
        """Move a message to Trash (Gmail maps ``\\Deleted`` to Trash)."""
        uid = int(message_id)
        client = self._connect_imap()
        try:
            client.delete_messages([uid])
        finally:
            client.logout()

    # --- SMTP -------------------------------------------------------------

    def send(
        self,
        to: list[str],
        subject: str,
        body: str,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> None:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
        msg.set_content(body)

        with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as smtp:
            smtp.login(self.address, self.app_password)
            smtp.send_message(msg)

    # --- Helpers ----------------------------------------------------------

    def _message_to_dict(self, uid: int, msg: Message) -> dict:
        body, content_type = _extract_body(msg)
        return {
            "id": str(uid),
            "subject": msg.get("Subject", "") or "",
            "from": msg.get("From", "") or "",
            "to": msg.get("To", "") or "",
            "cc": msg.get("Cc", "") or "",
            "date": msg.get("Date", "") or "",
            "message_id": msg.get("Message-ID", "") or "",
            "references": msg.get("References", "") or "",
            "body": body,
            "content_type": content_type,
        }

    def reply_recipients(self, message_id: str, reply_all: bool) -> list[str]:
        """Compute reply recipients from the parent message."""
        parent = self.fetch(message_id)
        from_addr = _addresses(parent["from"])
        to_addr = _addresses(parent["to"])
        cc_addr = _addresses(parent["cc"])

        recipients = from_addr
        if reply_all:
            recipients = _dedupe(from_addr + to_addr + cc_addr)
        return [a for a in recipients if a.lower() != self.address.lower()]


def _decode_preview(headers: bytes, text: bytes) -> tuple[Message, str]:
    """Parse full headers + a truncated ``BODY[TEXT]`` into ``(msg, preview)``.

    Reassembling the message lets the email package apply the declared
    Content-Transfer-Encoding and charset, so base64/quoted-printable bodies
    are classified (and snippeted) on their decoded text — raw MIME bytes
    would let an encoded OTP slip past the wording regexes. Truncation can
    corrupt the tail of an encoded part; decoding is best-effort with a
    fallback to the raw text.
    """
    combined = headers.rstrip(b"\r\n") + b"\r\n\r\n" + text
    msg = BytesParser(policy=policy.default).parsebytes(combined)
    if not text:
        return msg, ""
    try:
        preview, _ = _extract_body(msg)
    except Exception:
        preview = text.decode("utf-8", "replace")
    return msg, preview


def _extract_body(msg: Message) -> tuple[str, str]:
    """Return ``(body_text, content_type)`` preferring text/plain.

    HTML parts are converted to Markdown via html2text.
    """
    if msg.is_multipart():
        plain: str | None = None
        html: str | None = None
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = part.get_content()
            elif ctype == "text/html" and html is None:
                html = part.get_content()
        if plain is not None:
            return plain, "text"
        if html is not None:
            return html2text.html2text(html), "html"
        return "", "empty"

    ctype = msg.get_content_type()
    if ctype == "text/html":
        return html2text.html2text(msg.get_content()), "html"
    return msg.get_content(), "text"


def _addresses(header: str) -> list[str]:
    return [addr for _, addr in getaddresses([header]) if addr]


def _dedupe(addresses: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for addr in addresses:
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            out.append(addr)
    return out
