from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from app.telegram import build_digest, handle_update

from .helpers import board, login, make_task, make_user


def link_telegram(client, db_path, chat_id):
    url = client.post("/api/me/telegram").json()["url"]
    assert url.startswith("https://t.me/home_test_bot?start=")
    code = url.split("start=")[1]
    conn = db.connect(db_path)
    try:
        return handle_update(conn, {"message": {"chat": {"id": chat_id}, "text": f"/start {code}"}})
    finally:
        conn.close()


def bot(db_path, chat_id, text):
    conn = db.connect(db_path)
    try:
        return handle_update(conn, {"message": {"chat": {"id": chat_id}, "text": text}})
    finally:
        conn.close()


# ---------- миграции ----------

def test_migrations_upgrade_v1_database(tmp_path):
    """База, созданная первой версией приложения, обновляется без потери данных."""
    from app.db import MIGRATIONS, connect
    path = str(tmp_path / "old.db")
    conn = connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.execute("INSERT INTO users (username, display_name, password_hash) VALUES ('a', 'A', 'x')")
    conn.execute("INSERT INTO tasks (title) VALUES ('старая задача')")
    conn.commit()
    conn.close()

    client = TestClient(create_app(path))
    assert client.get("/api/setup").json()["needs_setup"] is False
    conn = connect(path)
    assert conn.execute("SELECT points FROM tasks").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    conn.close()


# ---------- чек-листы ----------

def test_checklist_lifecycle(admin):
    task = make_task(admin, title="Купить продукты", checklist=["Молоко", "Хлеб"])
    assert [i["text"] for i in task["checklist"]] == ["Молоко", "Хлеб"]

    item = admin.post(f"/api/tasks/{task['id']}/checklist", json={"text": "Яйца"}).json()
    assert item["done"] is False
    admin.patch(f"/api/checklist/{item['id']}", json={"done": True})
    admin.patch(f"/api/checklist/{task['checklist'][0]['id']}", json={"text": "Молоко 2,5%"})
    admin.delete(f"/api/checklist/{task['checklist'][1]['id']}")

    [listed] = admin.get("/api/tasks").json()
    assert [(i["text"], i["done"]) for i in listed["checklist"]] == [("Молоко 2,5%", False), ("Яйца", True)]
    assert admin.post(f"/api/tasks/{task['id']}/checklist", json={"text": ""}).status_code == 422


def test_recurring_task_copies_checklist_unchecked(admin):
    task = make_task(admin, recurrence="weekly", checklist=["Пыль", "Пол"])
    admin.patch(f"/api/checklist/{task['checklist'][0]['id']}", json={"done": True})
    admin.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    [nxt] = board(admin)["todo"]
    assert [(i["text"], i["done"]) for i in nxt["checklist"]] == [("Пыль", False), ("Пол", False)]


# ---------- история и комментарии ----------

def test_history_records_changes(admin):
    petya = make_user(admin)
    task = make_task(admin)
    admin.patch(f"/api/tasks/{task['id']}", json={"assignee_id": petya["id"], "priority": "high", "title": "Мусор!"})
    admin.post(f"/api/tasks/{task['id']}/move", json={"status": "in_progress", "index": 0})
    admin.post(f"/api/tasks/{task['id']}/move", json={"status": "in_progress", "index": 0})  # просто сортировка

    events = admin.get(f"/api/tasks/{task['id']}/events").json()
    kinds = [e["kind"] for e in events]
    assert kinds == ["created", "assigned", "edited", "status"]
    assert events[1]["payload"] == {"from": None, "to": petya["id"]}
    assert sorted(events[2]["payload"]["fields"]) == ["priority", "title"]
    assert events[3]["payload"] == {"from": "todo", "to": "in_progress"}
    assert events[0]["user_id"] == admin.get("/api/me").json()["id"]


def test_unchanged_fields_are_not_logged(admin):
    task = make_task(admin, priority="high")
    admin.patch(f"/api/tasks/{task['id']}", json={"priority": "high", "title": task["title"]})
    assert [e["kind"] for e in admin.get(f"/api/tasks/{task['id']}/events").json()] == ["created"]


def test_comments(app, admin):
    make_user(admin)
    task = make_task(admin)
    c = admin.post(f"/api/tasks/{task['id']}/comments", json={"text": "Пакеты под раковиной"}).json()
    assert c["kind"] == "comment" and c["payload"] == {"text": "Пакеты под раковиной"}
    assert admin.post(f"/api/tasks/{task['id']}/comments", json={"text": " "}).status_code == 422

    petya = login(app, "petya", "pass1234")
    assert petya.delete(f"/api/events/{c['id']}").status_code == 403
    own = petya.post(f"/api/tasks/{task['id']}/comments", json={"text": "ок"}).json()
    assert petya.delete(f"/api/events/{own['id']}").status_code == 204
    assert admin.delete(f"/api/events/{c['id']}").status_code == 204  # админ может удалить любой

    created = admin.get(f"/api/tasks/{task['id']}/events").json()[0]
    assert admin.delete(f"/api/events/{created['id']}").status_code == 400  # системные записи не удаляются


# ---------- очки и рейтинг ----------

def test_leaderboard_counts_points_of_done_tasks(admin):
    me = admin.get("/api/me").json()
    petya = make_user(admin)
    for pts, who in ((3, petya["id"]), (5, petya["id"]), (2, me["id"])):
        t = make_task(admin, points=pts, assignee_id=who)
        admin.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
    make_task(admin, points=8, assignee_id=me["id"])  # не сделана — не считается

    rows = admin.get("/api/leaderboard?period=week").json()
    assert [(r["user_id"], r["points"], r["done"]) for r in rows] == [(petya["id"], 8, 2), (me["id"], 2, 1)]
    assert admin.get("/api/leaderboard?period=month").json() == rows
    assert admin.get("/api/leaderboard?period=all").json() == rows
    assert admin.get("/api/leaderboard?period=year").status_code == 422


def test_reopened_task_loses_points(admin):
    me = admin.get("/api/me").json()
    t = make_task(admin, points=5, assignee_id=me["id"])
    admin.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
    admin.patch(f"/api/tasks/{t['id']}", json={"status": "todo"})
    assert admin.get("/api/leaderboard").json()[0]["points"] == 0


def test_points_validation(admin):
    assert admin.post("/api/tasks", json={"title": "x", "points": -1}).status_code == 422
    assert admin.post("/api/tasks", json={"title": "x", "points": 101}).status_code == 422
    assert make_task(admin)["points"] == 1


# ---------- Telegram ----------

def test_config_reports_telegram(admin):
    assert admin.get("/api/config").json() == {"telegram": True, "bot_username": "home_test_bot"}


def test_config_without_telegram(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    client = TestClient(create_app(str(tmp_path / "x.db")))
    client.post("/api/setup", json={"username": "a", "display_name": "A", "password": "secret"})
    assert client.get("/api/config").json() == {"telegram": False, "bot_username": None}
    assert client.post("/api/me/telegram").status_code == 400


def test_link_and_unlink_telegram(admin, db_path):
    [(chat, text)] = link_telegram(admin, db_path, 555)
    assert chat == 555 and "Мама" in text
    assert admin.get("/api/me").json()["telegram_linked"] is True
    # повторно ссылка не срабатывает
    code_reply = bot(db_path, 777, "/start whatever")
    assert "expired" in code_reply[0][1]

    assert admin.delete("/api/me/telegram").status_code == 204
    assert admin.get("/api/me").json()["telegram_linked"] is False


def test_bot_commands(admin, db_path):
    assert "not linked" in bot(db_path, 1, "/tasks")[0][1]
    link_telegram(admin, db_path, 1)
    me = admin.get("/api/me").json()
    make_task(admin, title="Полить <цветы>", assignee_id=me["id"], due_date="2026-12-01")
    [(_, text)] = bot(db_path, 1, "/tasks")
    assert "Полить &lt;цветы&gt;" in text and "1 Dec" in text

    bot(db_path, 1, "/stop")
    assert admin.get("/api/me").json()["telegram_linked"] is False


def test_notifications(app, admin, db_path, notifier):
    petya = make_user(admin)
    petya_client = login(app, "petya", "pass1234")
    link_telegram(admin, db_path, 100)
    link_telegram(petya_client, db_path, 200)

    task = make_task(admin, title="Вынести мусор", assignee_id=petya["id"])
    assert notifier.sent[-1][0] == 200 and "Вынести мусор" in notifier.sent[-1][1]

    notifier.sent.clear()
    make_task(admin, title="Сам себе", assignee_id=admin.get("/api/me").json()["id"])
    assert notifier.sent == []  # о своих действиях не уведомляем

    petya_client.post(f"/api/tasks/{task['id']}/move", json={"status": "review", "index": 0})
    assert notifier.sent[-1][0] == 100 and "review" in notifier.sent[-1][1]

    admin.post(f"/api/tasks/{task['id']}/move", json={"status": "done", "index": 0})
    assert notifier.sent[-1][0] == 200 and "+1" in notifier.sent[-1][1]

    notifier.sent.clear()
    petya_client.post(f"/api/tasks/{task['id']}/comments", json={"text": "Готово!"})
    assert notifier.sent == [(100, notifier.sent[0][1])] and "Готово!" in notifier.sent[0][1]


def test_morning_digest(admin, db_path):
    link_telegram(admin, db_path, 42)
    me = admin.get("/api/me").json()
    today = date(2026, 10, 16)
    make_task(admin, title="Сегодняшняя", assignee_id=me["id"], due_date=today.isoformat())
    make_task(admin, title="Просроченная", assignee_id=me["id"], due_date=(today - timedelta(days=2)).isoformat())
    make_task(admin, title="Через неделю", assignee_id=me["id"], due_date=(today + timedelta(days=7)).isoformat())
    done = make_task(admin, title="Сделанная", assignee_id=me["id"], due_date=today.isoformat())
    admin.patch(f"/api/tasks/{done['id']}", json={"status": "done"})

    conn = db.connect(db_path)
    [(chat, text)] = build_digest(conn, today)
    conn.close()
    assert chat == 42
    assert "Сегодняшняя" in text and "Просроченная" in text
    assert "Через неделю" not in text and "Сделанная" not in text


def test_bot_poller_links_chat_and_queues_reply(admin, db_path):
    """Сетевой цикл бота: getMe → getUpdates → привязка чата и ответ в очередь отправки."""
    from app.telegram import TelegramBot

    code = admin.post("/api/me/telegram").json()["url"].split("start=")[1]
    tg = TelegramBot("token", public_url="https://home.example/")
    tg.db_path = db_path
    calls = []

    def fake_call(method, **params):
        calls.append(method)
        if method == "getMe":
            return {"username": "real_bot"}
        if method == "getUpdates" and params["offset"] == 0:
            return [{"update_id": 7, "message": {"chat": {"id": 900}, "text": f"/start {code}"}}]
        tg.stopping.set()
        return []

    tg.call = fake_call
    tg._poller()
    assert calls == ["getMe", "getUpdates", "getUpdates"]
    assert tg.bot_username == "real_bot"
    chat_id, text = tg.queue.get_nowait()
    assert chat_id == 900 and "Мама" in text
    assert admin.get("/api/me").json()["telegram_linked"] is True

    tg.send(900, "привет")
    assert tg.queue.get_nowait() == (900, "привет\n\nhttps://home.example")


# ---------- темы ----------

def test_theme_defaults_to_auto_and_is_saved(app, admin):
    me = admin.get("/api/me").json()
    assert me["theme"] == "auto"
    r = admin.patch(f"/api/users/{me['id']}", json={"theme": "mint"})
    assert r.status_code == 200 and r.json()["theme"] == "mint"
    assert admin.get("/api/me").json()["theme"] == "mint"
    # тема личная: у других пользователей она своя
    make_user(admin)
    petya = login(app, "petya", "pass1234")
    assert petya.get("/api/me").json()["theme"] == "auto"


def test_unknown_theme_rejected(admin):
    me = admin.get("/api/me").json()
    assert admin.patch(f"/api/users/{me['id']}", json={"theme": "rainbow"}).status_code == 422
