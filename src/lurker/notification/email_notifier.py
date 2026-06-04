from __future__ import annotations

from collections.abc import Sequence
from email.message import EmailMessage
import smtplib
from typing import Any

from markdown_it import MarkdownIt


def build_email_message(
    *,
    subject: str,
    markdown_content: str,
    sender: str,
    recipients: Sequence[str],
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(markdown_content)
    html = MarkdownIt().render(markdown_content)
    message.add_alternative(html, subtype="html")
    return message


class EmailNotifier:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        recipients: Sequence[str],
        use_tls: bool = True,
        use_ssl: bool = False,
        smtp_class: Any | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = [recipient for recipient in recipients if recipient]
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.smtp_class = smtp_class

    def send(self, title: str, markdown_content: str) -> None:
        if not self.recipients:
            return
        message = build_email_message(
            subject=title,
            markdown_content=markdown_content,
            sender=self.sender,
            recipients=self.recipients,
        )
        smtp_class = self.smtp_class or (smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP)
        with smtp_class(self.host, self.port, timeout=20) as smtp:
            if self.use_tls and not self.use_ssl:
                smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
