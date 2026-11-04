"""Отправка писем через SMTP. Сертификат сервера проверяется всегда."""

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Callable, Optional

SECURITY_MODES = ("starttls", "ssl", "none")
TIMEOUT_SECONDS = 15


@dataclass
class SmtpConfig:
    host: str
    sender: str
    port: int = 587
    security: str = "starttls"
    user: Optional[str] = None
    password: Optional[str] = None
    ca_file: Optional[str] = None

    @classmethod
    def from_env(cls) -> Optional["SmtpConfig"]:
        """Без SMTP_HOST почта выключена. Ошибку в остальных настройках лучше показать при запуске."""
        env = os.environ.get
        host = env("SMTP_HOST")
        if not host:
            return None
        sender = env("SMTP_FROM")
        if not sender or "@" not in sender:
            raise ValueError("SMTP_FROM обязателен вместе с SMTP_HOST и должен быть адресом почты")
        security = (env("SMTP_SECURITY") or "starttls").lower()
        if security not in SECURITY_MODES:
            raise ValueError(f"SMTP_SECURITY должен быть одним из {', '.join(SECURITY_MODES)}")
        port = int(env("SMTP_PORT") or (465 if security == "ssl" else 587))
        return cls(host=host, sender=sender, port=port, security=security, user=env("SMTP_USER") or None,
                   password=env("SMTP_PASSWORD") or None, ca_file=env("SMTP_CA_FILE") or None)


class Mailer:
    def __init__(self, config: SmtpConfig, smtp_factory: Optional[Callable] = None):
        self.config = config
        self.smtp_factory = smtp_factory
        self.domain = config.sender.rsplit("@", 1)[1]

    def _context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=self.config.ca_file)

    def _connect(self, context: Optional[ssl.SSLContext]):
        if self.smtp_factory:
            return self.smtp_factory(self.config, context)
        if self.config.security == "ssl":
            return smtplib.SMTP_SSL(self.config.host, self.config.port, context=context, timeout=TIMEOUT_SECONDS)
        return smtplib.SMTP(self.config.host, self.config.port, timeout=TIMEOUT_SECONDS)

    def send(self, to: str, subject: str, text: str, html: Optional[str] = None,
             thread: Optional[str] = None) -> None:
        msg = EmailMessage()
        msg["From"] = self.config.sender
        msg["To"] = to
        # Перевод строки в теме из данных задачи не должен стать новым заголовком.
        msg["Subject"] = " ".join(subject.split())
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.domain)
        if thread:
            # Все письма по задаче ссылаются на один корень и складываются в цепочку.
            root = f"<{thread}@{self.domain}>"
            msg["In-Reply-To"] = root
            msg["References"] = root
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")

        context = self._context() if self.config.security != "none" else None
        with self._connect(context if self.config.security == "ssl" else None) as smtp:
            if self.config.security == "starttls":
                smtp.starttls(context=context)
            if self.config.user:
                smtp.login(self.config.user, self.config.password or "")
            smtp.send_message(msg, to_addrs=[to])
