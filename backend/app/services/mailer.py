"""Small standard-SMTP mailer used by account verification and recovery."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from ..config import (
    MAIL_MODE,
    PUBLIC_BASE_URL,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_TLS,
    SMTP_USERNAME,
)
from ..models import User

logger = logging.getLogger(__name__)


class MailDeliveryError(RuntimeError):
    """Raised when an account email could not be handed to SMTP."""


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Deliver one message via configured SMTP or local Mailpit/console mode."""

    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    if MAIL_MODE in {"console", "log", "disabled"}:
        # Never print one-time verification/reset links: their raw tokens are
        # intentionally absent from the database and must also stay out of
        # application logs.  Use Mailpit when local delivery needs inspection.
        logger.info("account email suppressed mode=%s to=%s subject=%s", MAIL_MODE, to, subject)
        return
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as client:
            client.ehlo()
            if SMTP_USE_TLS:
                client.starttls()
                client.ehlo()
            if SMTP_USERNAME:
                client.login(SMTP_USERNAME, SMTP_PASSWORD)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailDeliveryError("邮件发送失败，请稍后重试") from exc


def send_verification_email(user: User, token: str) -> None:
    # URL fragments never leave the browser in the HTTP request, so Uvicorn,
    # reverse proxies, access logs, and Referer headers cannot capture tokens.
    url = f"{PUBLIC_BASE_URL}/verify-email#token={quote(token, safe='')}"
    send_email(
        user.email,
        "验证你的小说工作台邮箱",
        f"你好，{user.display_name or '作者'}！\n\n请打开以下链接验证邮箱（24 小时内有效）：\n{url}\n\n如果这不是你的操作，请忽略此邮件。",
    )


def send_password_reset_email(user: User, token: str) -> None:
    url = f"{PUBLIC_BASE_URL}/reset-password#token={quote(token, safe='')}"
    send_email(
        user.email,
        "重置小说工作台密码",
        f"你好，{user.display_name or '作者'}！\n\n请打开以下链接设置新密码（30 分钟内有效）：\n{url}\n\n如果这不是你的操作，请忽略此邮件。",
    )


__all__ = [
    "MailDeliveryError",
    "send_email",
    "send_password_reset_email",
    "send_verification_email",
]
