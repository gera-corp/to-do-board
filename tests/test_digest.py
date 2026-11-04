from fastapi.testclient import TestClient

from app import db
from app.main import create_app

from .helpers import deliver, make_task
from .test_features import link_telegram
from .test_mailer import FakeMailer


def digest_app(db_path, notifier, clock, monkeypatch, digest=True):
    monkeypatch.setenv("DIGEST_HOUR", str(clock().astimezone().hour))
    mail = FakeMailer()
    app = create_app(db_path, notifier=notifier, clock=clock, mailer=mail)
    mama = TestClient(app)
    mama.post("/api/setup", json={"username": "mama", "display_name": "Мама", "password": "secret"})
    link_telegram(mama, db_path, 100)
    conn = db.connect(db_path)
    conn.execute("UPDATE users SET email = 'mama@example.org', email_digest = ?", (int(digest),))
    conn.commit()
    conn.close()
    me = mama.get("/api/me").json()
    make_task(mama, title="Сегодняшняя", assignee_id=me["id"], due_date=clock().astimezone().date().isoformat())
    deliver(app)
    notifier.sent.clear()
    return app, mail


def test_digest_once_per_day_both_channels(db_path, notifier, clock, monkeypatch):
    app, mail = digest_app(db_path, notifier, clock, monkeypatch)
    app.state.dispatcher.run_digest()
    deliver(app)
    assert [chat for chat, _ in notifier.sent] == [100] and "Сегодняшняя" in notifier.sent[0][1]
    assert len(mail.sent) == 1 and "Сегодняшняя" in mail.sent[0]["text"]
    app.state.dispatcher.run_digest()
    deliver(app)
    assert len(notifier.sent) == 1 and len(mail.sent) == 1


def test_email_digest_off_by_default(db_path, notifier, clock, monkeypatch):
    app, mail = digest_app(db_path, notifier, clock, monkeypatch, digest=False)
    app.state.dispatcher.run_digest()
    deliver(app)
    assert len(notifier.sent) == 1 and mail.sent == []


def test_no_digest_outside_the_hour(db_path, notifier, clock, monkeypatch):
    app, mail = digest_app(db_path, notifier, clock, monkeypatch)
    app.state.dispatcher.digest_hour = (clock().astimezone().hour + 5) % 24
    app.state.dispatcher.run_digest()
    deliver(app)
    assert notifier.sent == [] and mail.sent == []


def test_digest_hour_env_fallback(db_path, notifier, clock, monkeypatch):
    monkeypatch.delenv("DIGEST_HOUR", raising=False)
    monkeypatch.delenv("TELEGRAM_DIGEST_HOUR", raising=False)
    assert create_app(db_path, notifier=notifier, clock=clock).state.dispatcher.digest_hour == 9
    monkeypatch.setenv("TELEGRAM_DIGEST_HOUR", "7")
    assert create_app(db_path, notifier=notifier, clock=clock).state.dispatcher.digest_hour == 7
    monkeypatch.setenv("DIGEST_HOUR", "6")
    assert create_app(db_path, notifier=notifier, clock=clock).state.dispatcher.digest_hour == 6


def test_email_digest_sent_when_notifications_off(db_path, notifier, clock, monkeypatch):
    app, mail = digest_app(db_path, notifier, clock, monkeypatch)
    conn = db.connect(db_path)
    conn.execute("UPDATE users SET email_notify = 0")
    conn.commit()
    conn.close()
    app.state.dispatcher.run_digest()
    deliver(app)
    assert len(mail.sent) == 1


def test_telegram_digest_links_to_board(db_path, notifier, clock, monkeypatch):
    monkeypatch.setenv("TODO_PUBLIC_URL", "https://b.example")
    app, _ = digest_app(db_path, notifier, clock, monkeypatch)
    app.state.dispatcher.run_digest()
    deliver(app)
    assert notifier.sent[-1][1].endswith("\n\nhttps://b.example")
