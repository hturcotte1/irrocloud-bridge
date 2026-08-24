"""Notify: file mode writes readable outbox files; SMTP mode builds real mail."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import bridge.notify as notify
from bridge.config import load_config
from bridge.message import EmailPayload

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 8, 24, 12, 30, tzinfo=UTC)


def make_cfg(tmp_path, **env):
    (tmp_path / "fields.json").write_text((REPO_ROOT / "fields.json").read_text())
    return load_config(root=tmp_path, environ=env)


def test_file_mode_writes_outbox(tmp_path):
    cfg = make_cfg(tmp_path)
    payload = EmailPayload(subject="Bridge OK — test", text="hello Henry")
    result = notify.send_email(cfg, ["henry@example.com"], payload, "status-email", NOW)
    assert result.startswith("written to outbox/")
    files = list((tmp_path / "outbox").glob("*.txt"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "To: henry@example.com" in content
    assert "Subject: Bridge OK — test" in content
    assert "hello Henry" in content
    assert files[0].name == "2026-08-24T0630-status-email.txt"  # Boise local time


def test_file_mode_lists_attachments(tmp_path):
    cfg = make_cfg(tmp_path)
    payload = EmailPayload(
        subject="s", text="t", attachments=["logs/screenshots/login.png"]
    )
    notify.send_email(cfg, ["h@x.com"], payload, "alert", NOW)
    content = next((tmp_path / "outbox").glob("*.txt")).read_text()
    assert "Attachments: logs/screenshots/login.png" in content


def test_smtp_mode_sends_multipart(tmp_path, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    cfg = make_cfg(
        tmp_path,
        NOTIFY_MODE="smtp",
        SMTP_USER="bridge@gmail.com",
        SMTP_PASSWORD="app-password",
        HENRY_EMAIL="henry@example.com",
    )
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG fake")
    payload = EmailPayload(
        subject="Bridge FAIL — test", text="body", html="<pre>body</pre>",
        attachments=[str(png)],
    )
    result = notify.send_email(cfg, ["henry@example.com"], payload, "status", NOW)
    assert result == "sent to henry@example.com"
    assert sent["host"] == "smtp.gmail.com" and sent["port"] == 587
    assert sent["tls"] and sent["login"] == ("bridge@gmail.com", "app-password")
    message = sent["message"]
    assert message["Subject"] == "Bridge FAIL — test"
    assert message.is_multipart()
    filenames = [p.get_filename() for p in message.iter_attachments()]
    assert "shot.png" in filenames


def test_smtp_mode_without_recipient_raises(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, NOTIFY_MODE="smtp", SMTP_USER="u", SMTP_PASSWORD="p")
    with pytest.raises(RuntimeError):
        notify.send_email(cfg, [], EmailPayload(subject="s", text="t"), "x", NOW)
