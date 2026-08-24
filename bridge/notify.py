"""Send email — or, in file mode, write it to outbox/ so nothing leaves the
machine until Henry has real SMTP settings.

File mode is the default and what tonight's offline run uses: each message
becomes one readable .txt file (headers + body). SMTP mode uses Gmail with an
app password over STARTTLS. Attachments (failure screenshots) ride along in
SMTP mode and are listed by path in file mode.
"""

from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

from bridge.config import Config
from bridge.message import EmailPayload


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit] or "message"


def send_email(
    cfg: Config,
    to: list[str],
    payload: EmailPayload,
    tag: str,
    now_utc,
) -> str:
    """Send (or file) one email. Returns a plain-English description of what
    happened, for the run log. Raises on SMTP failure — the caller decides how
    loud to be (the status email failing is exit-nonzero loud)."""
    if cfg.notify_mode == "file":
        return _write_to_outbox(cfg, to, payload, tag, now_utc)
    return _send_smtp(cfg, to, payload)


def _write_to_outbox(cfg: Config, to: list[str], payload: EmailPayload, tag, now_utc) -> str:
    cfg.outbox_dir.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp(now_utc).tz_convert(cfg.tz).strftime("%Y-%m-%dT%H%M")
    path = cfg.outbox_dir / f"{stamp}-{_slug(tag)}.txt"
    lines = [
        f"To: {', '.join(to) if to else '(no recipient configured — REPLACE_ME in .env)'}",
        f"Subject: {payload.subject}",
    ]
    if payload.attachments:
        lines.append("Attachments: " + ", ".join(str(a) for a in payload.attachments))
    lines += ["", payload.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return f"written to {path.relative_to(cfg.root)}"


def _send_smtp(cfg: Config, to: list[str], payload: EmailPayload) -> str:
    if not to:
        raise RuntimeError("no recipient address configured (check .env)")
    message = EmailMessage()
    message["From"] = cfg.smtp_user
    message["To"] = ", ".join(to)
    message["Subject"] = payload.subject
    message.set_content(payload.text)
    if payload.html:
        message.add_alternative(payload.html, subtype="html")
    for attachment in payload.attachments:
        path = Path(attachment)
        if not path.exists():
            continue
        message.add_attachment(
            path.read_bytes(),
            maintype="image" if path.suffix == ".png" else "application",
            subtype=path.suffix.lstrip(".") or "octet-stream",
            filename=path.name,
        )
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(message)
    return f"sent to {', '.join(to)}"
