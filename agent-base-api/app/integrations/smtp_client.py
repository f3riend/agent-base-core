import os
import smtplib
import ssl
from email.message import EmailMessage


def smtp_configured() -> bool:
    return bool((os.getenv("RESET_EMAIL_FROM") or "").strip() and (os.getenv("SMTP_HOST") or "").strip())


def send_email(to_email: str, subject: str, body: str) -> None:
    sender = (os.getenv("RESET_EMAIL_FROM") or "").strip()
    host = (os.getenv("SMTP_HOST") or "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = (os.getenv("SMTP_USER") or "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    if not sender or not host:
        raise RuntimeError("SMTP ayarlari eksik.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls(context=ssl.create_default_context())
        if user:
            server.login(user, password)
        server.send_message(msg)
