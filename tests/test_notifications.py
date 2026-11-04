import logging

import pytest

from app import db
from app.main import create_app

from .helpers import deliver, login, make_task, make_user
from .test_features import link_telegram


@pytest.fixture
def people(app, admin, db_path):
    """Мама (чат 100), Петя (200) и Вася (300), у всех привязан Telegram."""
    link_telegram(admin, db_path, 100)
    petya, vasya = make_user(admin, "petya"), make_user(admin, "vasya")
    p, v = login(app, "petya", "pass1234"), login(app, "vasya", "pass1234")
    link_telegram(p, db_path, 200)
    link_telegram(v, db_path, 300)
    return {"petya": (p, petya), "vasya": (v, vasya)}


def chats(notifier):
    return sorted(chat for chat, _ in notifier.sent)


def test_watchers_get_comment_except_author(app, admin, people, notifier):
    (v, _), (_, petya) = people["vasya"], people["petya"]
    task = make_task(admin, assignee_id=petya["id"])
    v.post(f"/api/tasks/{task['id']}/watch")
    deliver(app)
    notifier.sent.clear()
    v.post(f"/api/tasks/{task['id']}/comments", json={"text": "Как дела?"})
    deliver(app)
    assert chats(notifier) == [100, 200]
    assert all("Как дела?" in text for _, text in notifier.sent)


def test_nothing_sent_before_deliver(app, admin, people, notifier):
    _, petya = people["petya"]
    make_task(admin, assignee_id=petya["id"])
    assert notifier.sent == []
    deliver(app)
    assert chats(notifier) == [200]


def test_move_reaches_all_watchers(app, admin, people, notifier):
    (p, petya), (v, _) = people["petya"], people["vasya"]
    task = make_task(admin, assignee_id=petya["id"])
    v.post(f"/api/tasks/{task['id']}/watch")
    deliver(app)
    notifier.sent.clear()
    p.post(f"/api/tasks/{task['id']}/move", json={"status": "in_progress", "index": 0})
    deliver(app)
    assert chats(notifier) == [100, 300]
    assert all("To Do" in text and "In Progress" in text for _, text in notifier.sent)


def test_review_text_unchanged(app, admin, people, notifier):
    p, petya = people["petya"]
    task = make_task(admin, assignee_id=petya["id"])
    deliver(app)
    notifier.sent.clear()
    p.post(f"/api/tasks/{task['id']}/move", json={"status": "review", "index": 0})
    deliver(app)
    assert notifier.sent == [(100, "👀 Petya asks you to review: «Вынести мусор»")]


def test_unblocked_goes_to_watchers_of_waiting_task(app, admin, people, notifier):
    (_, petya), (v, _) = people["petya"], people["vasya"]
    paint = make_task(admin, title="Покрасить", assignee_id=petya["id"])
    sand = make_task(admin, title="Зашкурить")
    admin.post(f"/api/tasks/{paint['id']}/links", json={"kind": "blocked_by", "task_id": sand["id"]})
    v.post(f"/api/tasks/{paint['id']}/watch")
    deliver(app)
    notifier.sent.clear()
    admin.post(f"/api/tasks/{sand['id']}/move", json={"status": "done", "index": 0})
    deliver(app)
    unblocked = [chat for chat, text in notifier.sent if text.startswith("🔓")]
    assert sorted(unblocked) == [200, 300]


def test_failed_telegram_is_retried_with_backoff(app, admin, people, notifier, monkeypatch):
    _, petya = people["petya"]
    real, calls = notifier.send_now, []

    def flaky(chat_id, text):
        calls.append(chat_id)
        if len(calls) == 1:
            raise OSError("network down")
        real(chat_id, text)

    monkeypatch.setattr(notifier, "send_now", flaky)
    make_task(admin, assignee_id=petya["id"])
    deliver(app)
    deliver(app, 29)
    assert notifier.sent == []
    deliver(app, 30)
    assert chats(notifier) == [200]


def test_row_older_than_day_is_dropped(app, admin, people, notifier, monkeypatch, db_path, caplog):
    _, petya = people["petya"]

    def down(chat_id, text):
        raise OSError("network down")

    monkeypatch.setattr(notifier, "send_now", down)
    make_task(admin, assignee_id=petya["id"])
    deliver(app)
    with caplog.at_level(logging.WARNING):
        deliver(app, 24 * 3600 + 1)
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    assert any("24" in r.getMessage() for r in caplog.records)


def test_link_added_when_public_url_set(db_path, notifier, clock, monkeypatch):
    monkeypatch.setenv("TODO_PUBLIC_URL", "https://b.example/")
    app = create_app(db_path, notifier=notifier, clock=clock)
    from fastapi.testclient import TestClient
    admin = TestClient(app)
    admin.post("/api/setup", json={"username": "mama", "display_name": "Мама", "password": "secret"})
    petya = make_user(admin, "petya")
    link_telegram(login(app, "petya", "pass1234"), db_path, 200)
    task = make_task(admin, assignee_id=petya["id"])
    deliver(app)
    assert notifier.sent[-1][1].endswith(f"\n\nhttps://b.example/#task={task['id']}")
