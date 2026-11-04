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


# ---------- правки, чек-лист, связи: группой ----------

@pytest.fixture
def watched(app, admin, people, notifier):
    """Задача Мамы, исполнитель Петя; Петя действует, Мама (чат 100) получает."""
    p, petya = people["petya"]
    task = make_task(admin, assignee_id=petya["id"], checklist=["Пыль", "Пол", "Окна", "Двери", "Ковёр"])
    deliver(app)
    notifier.sent.clear()
    return p, task


def to_mama(notifier):
    return [text for chat, text in notifier.sent if chat == 100]


def test_one_save_one_message(app, watched, notifier):
    p, task = watched
    p.patch(f"/api/tasks/{task['id']}", json={"due_date": "2026-11-07", "priority": "high"})
    deliver(app, 120)
    [text] = to_mama(notifier)
    assert "7 Nov" in text and "Priority" in text and "Due date" in text


def test_edits_wait_for_window(app, watched, notifier):
    p, task = watched
    p.patch(f"/api/tasks/{task['id']}", json={"due_date": "2026-11-07"})
    deliver(app, 119)
    assert to_mama(notifier) == []
    deliver(app, 120)
    assert len(to_mama(notifier)) == 1


def test_checklist_clicks_grouped(app, watched, notifier):
    p, task = watched
    for item in task["checklist"]:
        p.patch(f"/api/checklist/{item['id']}", json={"done": True})
    deliver(app, 120)
    [text] = to_mama(notifier)
    assert all(word in text for word in ("Пыль", "Пол", "Окна", "Двери", "Ковёр"))


def test_immediate_event_carries_pending_edits(app, watched, notifier):
    p, task = watched
    p.patch(f"/api/tasks/{task['id']}", json={"priority": "high"})
    p.post(f"/api/tasks/{task['id']}/comments", json={"text": "Поднял приоритет"})
    deliver(app)
    [text] = to_mama(notifier)
    assert "Priority" in text and "Поднял приоритет" in text


def test_change_reverted_inside_window_is_omitted(app, watched, notifier, db_path):
    p, task = watched
    p.patch(f"/api/tasks/{task['id']}", json={"priority": "high"})
    p.patch(f"/api/tasks/{task['id']}", json={"priority": "normal"})
    deliver(app, 120)
    assert to_mama(notifier) == []
    assert db.connect(db_path).execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


def test_description_change_has_no_text(app, watched, notifier):
    p, task = watched
    p.patch(f"/api/tasks/{task['id']}", json={"description": "секретный код 1234"})
    deliver(app, 120)
    [text] = to_mama(notifier)
    assert "Description" in text and "1234" not in text


def test_delete_flushes_pending_with_snapshot_title(app, admin, watched, notifier):
    p, task = watched
    admin.post(f"/api/tasks/{task['id']}/watch")
    p.patch(f"/api/tasks/{task['id']}", json={"title": "Генеральная уборка"})
    p.delete(f"/api/tasks/{task['id']}")
    deliver(app)
    [text] = to_mama(notifier)
    assert "Генеральная уборка" in text and "🗑" in text and "Title" in text


def test_subtask_and_link_changes_reach_parent_watchers(app, admin, watched, notifier):
    p, task = watched
    other = make_task(admin, title="Купить краску")
    deliver(app)
    notifier.sent.clear()
    p.post("/api/tasks", json={"title": "Протереть полки", "parent_id": task["id"]})
    p.post(f"/api/tasks/{task['id']}/links", json={"kind": "blocked_by", "task_id": other["id"]})
    deliver(app, 120)
    texts = to_mama(notifier)
    parent_text = next(t for t in texts if "Протереть полки" in t)
    assert "Купить краску" in parent_text


def test_two_actors_in_one_group_are_named(app, admin, people, watched, notifier):
    p, task = watched
    v, _ = people["vasya"]
    p.patch(f"/api/tasks/{task['id']}", json={"priority": "high"})
    v.patch(f"/api/tasks/{task['id']}", json={"due_date": "2026-11-07"})
    deliver(app, 120)
    [text] = to_mama(notifier)
    assert "Petya" in text and "Vasya" in text
