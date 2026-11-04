import ssl

import pytest

from app import db
from app.mailer import Mailer, SmtpConfig
from app.main import create_app

from .helpers import deliver, login, make_task, make_user
from .test_features import link_telegram


class FakeSmtp:
    """Записывает, что почтовая программа делала с сервером."""

    def __init__(self, config, context):
        self.config, self.context, self.calls, self.messages = config, context, [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def starttls(self, context):
        self.calls.append(("starttls", context))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg, to_addrs=None):
        self.messages.append(msg)
        self.calls.append(("to_addrs", to_addrs))


def mailer_with(config):
    made = []

    def factory(cfg, context):
        made.append(FakeSmtp(cfg, context))
        return made[-1]

    return Mailer(config, smtp_factory=factory), made


def test_from_env(monkeypatch):
    for name in ("SMTP_HOST", "SMTP_FROM", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER", "SMTP_PASSWORD", "SMTP_CA_FILE"):
        monkeypatch.delenv(name, raising=False)
    assert SmtpConfig.from_env() is None
    monkeypatch.setenv("SMTP_HOST", "mail.example.org")
    with pytest.raises(ValueError):
        SmtpConfig.from_env()
    monkeypatch.setenv("SMTP_FROM", "board@example.org")
    config = SmtpConfig.from_env()
    assert (config.port, config.security) == (587, "starttls")
    monkeypatch.setenv("SMTP_SECURITY", "tls")
    with pytest.raises(ValueError):
        SmtpConfig.from_env()
    monkeypatch.setenv("SMTP_SECURITY", "ssl")
    assert SmtpConfig.from_env().port == 465


@pytest.mark.parametrize("security", ["starttls", "ssl", "none"])
def test_starttls_ssl_none_paths(security, monkeypatch):
    seen = []
    real = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda **kw: seen.append(kw) or real())
    config = SmtpConfig(host="mail", sender="board@example.org", security=security, user="u", password="p",
                        ca_file="/certs/ca.pem")
    mailer, made = mailer_with(config)
    mailer.send("anna@example.org", "Тема", "Текст")
    [smtp] = made
    starttls = [c for c in smtp.calls if c[0] == "starttls"]
    assert len(starttls) == (1 if security == "starttls" else 0)
    if security != "none":
        context = starttls[0][1] if starttls else smtp.context
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert seen == [{"cafile": "/certs/ca.pem"}]
    assert ("login", "u", "p") in smtp.calls
    assert smtp.messages[0]["To"] == "anna@example.org"


def test_thread_headers():
    mailer, made = mailer_with(SmtpConfig(host="mail", sender="board@example.org"))
    mailer.send("a@example.org", "Задача", "раз", thread="task-7")
    mailer.send("a@example.org", "Задача", "два", thread="task-7")
    first, second = made[0].messages[0], made[1].messages[0]
    assert first["References"] == second["References"] == "<task-7@example.org>"
    assert first["In-Reply-To"] == "<task-7@example.org>"
    assert first["Message-ID"] != second["Message-ID"]


class FakeMailer:
    def __init__(self):
        self.sent, self.fail = [], 0

    def send(self, to, subject, text, html=None, thread=None):
        if self.fail:
            self.fail -= 1
            raise OSError("mail server down")
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html, "thread": thread})


@pytest.fixture
def mail():
    return FakeMailer()


@pytest.fixture
def mail_app(db_path, notifier, clock, mail, monkeypatch):
    monkeypatch.setenv("TODO_PUBLIC_URL", "https://b.example")
    return create_app(db_path, notifier=notifier, clock=clock, mailer=mail)


@pytest.fixture
def mama(mail_app):
    from fastapi.testclient import TestClient
    client = TestClient(mail_app)
    client.post("/api/setup", json={"username": "mama", "display_name": "Мама", "password": "secret"})
    return client


def confirmed_email(db_path, username, address):
    conn = db.connect(db_path)
    conn.execute("UPDATE users SET email = ? WHERE username = ?", (address, username))
    conn.commit()
    conn.close()


def test_subject_single_line_and_html_escaped(mail_app, mama, mail, notifier, db_path):
    petya = make_user(mama, "petya")
    link_telegram(login(mail_app, "petya", "pass1234"), db_path, 200)
    confirmed_email(db_path, "petya", "petya@example.org")
    task = make_task(mama, title="Купить\nкраску <script>", assignee_id=petya["id"])
    deliver(mail_app)
    [letter] = mail.sent
    assert "\n" not in letter["subject"] and "<script>" not in letter["html"]
    assert "&lt;script&gt;" in letter["html"] and letter["thread"] == f"task-{task['id']}"
    assert f"https://b.example/#task={task['id']}" in letter["html"]
    assert "https://b.example/#profile" in letter["html"]
    assert "&lt;script&gt;" in notifier.sent[-1][1]


def test_email_failure_does_not_resend_telegram(mail_app, mama, mail, notifier, db_path):
    petya = make_user(mama, "petya")
    link_telegram(login(mail_app, "petya", "pass1234"), db_path, 200)
    confirmed_email(db_path, "petya", "petya@example.org")
    mail.fail = 1
    make_task(mama, assignee_id=petya["id"])
    deliver(mail_app)
    assert len(notifier.sent) == 1 and mail.sent == []
    deliver(mail_app, 30)
    assert len(notifier.sent) == 1 and len(mail.sent) == 1


def test_only_the_given_recipient_gets_the_letter():
    mailer, made = mailer_with(SmtpConfig(host="mail", sender="board@example.org"))
    mailer.send("anna@example.org", "Тема", "Текст")
    assert ("to_addrs", ["anna@example.org"]) in made[0].calls


def test_failing_mail_channel_does_not_hold_up_others(mail_app, mama, mail, notifier, db_path):
    attempts = []

    def down(to, *args, **kwargs):
        attempts.append(to)
        raise OSError("mail server down")

    mail.send = down
    for name in ("petya", "vasya"):
        make_user(mama, name)
        link_telegram(login(mail_app, name, "pass1234"), db_path, {"petya": 200, "vasya": 300}[name])
        confirmed_email(db_path, name, f"{name}@example.org")
    task = make_task(mama, title="Общая")
    for name in ("petya", "vasya"):
        login(mail_app, name, "pass1234").post(f"/api/tasks/{task['id']}/watch")
    deliver(mail_app)
    notifier.sent.clear()
    mama.post(f"/api/tasks/{task['id']}/comments", json={"text": "Привет"})
    deliver(mail_app)
    assert len(attempts) == 1, "после первой неудачи почта в этом проходе больше не пробуется"
    assert sorted(chat for chat, _ in notifier.sent) == [200, 300]
    first = attempts[0]
    mama.post(f"/api/tasks/{task['id']}/comments", json={"text": "Ещё"})
    deliver(mail_app, 1)
    assert attempts.count(first) == 1, "новое событие не отменяет паузу между попытками"
