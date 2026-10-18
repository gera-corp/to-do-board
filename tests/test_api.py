from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.recurrence import next_due

from .helpers import board, login, make_task, make_user


# ---------- настройка и вход ----------

def test_setup_creates_admin_once(anon):
    assert anon.get("/api/setup").json()["needs_setup"] is True
    r = anon.post("/api/setup", json={"username": "mama", "display_name": "Мама", "password": "secret"})
    assert r.json()["is_admin"] is True
    assert anon.get("/api/me").json()["username"] == "mama"
    assert anon.get("/api/setup").json()["needs_setup"] is False
    again = anon.post("/api/setup", json={"username": "x", "display_name": "X", "password": "secret"})
    assert again.status_code == 409


def test_api_requires_login(anon):
    assert anon.get("/api/tasks").status_code == 401


def test_login_logout(app, admin):
    assert TestClient(app).post("/api/login", json={"username": "mama", "password": "wrong"}).status_code == 401
    client = login(app, "MAMA", "secret")  # логин без учёта регистра
    assert client.get("/api/me").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


# ---------- пользователи ----------

def test_admin_manages_users(app, admin):
    petya = make_user(admin)
    assert petya["is_admin"] is False
    assert admin.post("/api/users", json={"username": "petya", "display_name": "P", "password": "pass1234"}).status_code == 409

    petya_client = login(app, "petya", "pass1234")
    assert petya_client.post("/api/users", json={"username": "z", "display_name": "Z", "password": "pass1234"}).status_code == 403
    assert petya_client.delete(f"/api/users/{petya['id']}").status_code == 403

    assert admin.delete(f"/api/users/{petya['id']}").status_code == 204
    assert len(admin.get("/api/users").json()) == 1


def test_user_edits_only_own_profile(app, admin):
    petya = make_user(admin)
    me = admin.get("/api/me").json()
    client = login(app, "petya", "pass1234")
    assert client.patch(f"/api/users/{petya['id']}", json={"color": "#FF0000", "password": "newpass"}).status_code == 200
    assert client.patch(f"/api/users/{me['id']}", json={"display_name": "Хак"}).status_code == 403
    assert client.patch(f"/api/users/{petya['id']}", json={"is_admin": True}).status_code == 403
    login(app, "petya", "newpass")


def test_admin_cannot_remove_self(admin):
    me = admin.get("/api/me").json()
    assert admin.delete(f"/api/users/{me['id']}").status_code == 400
    assert admin.patch(f"/api/users/{me['id']}", json={"is_admin": False}).status_code == 400


def test_deleting_user_unassigns_tasks(admin):
    petya = make_user(admin)
    task = make_task(admin, assignee_id=petya["id"])
    admin.delete(f"/api/users/{petya['id']}")
    assert admin.get(f"/api/tasks/{task['id']}").json()["assignee_id"] is None


# ---------- задачи ----------

def test_task_crud(admin):
    petya = make_user(admin)
    task = make_task(admin, assignee_id=petya["id"], priority="high", category="Кухня", due_date="2026-11-01")
    assert task["status"] == "todo"
    assert task["creator_id"] == admin.get("/api/me").json()["id"]

    r = admin.patch(f"/api/tasks/{task['id']}", json={"title": "Помыть посуду", "assignee_id": None, "due_date": None})
    body = r.json()
    assert (body["title"], body["assignee_id"], body["due_date"]) == ("Помыть посуду", None, None)
    assert body["category"] == "Кухня"

    assert admin.get("/api/categories").json() == ["Кухня"]
    assert admin.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert admin.get(f"/api/tasks/{task['id']}").status_code == 404


def test_task_validation(admin):
    assert admin.post("/api/tasks", json={"title": ""}).status_code == 422
    assert admin.post("/api/tasks", json={"title": "x", "status": "later"}).status_code == 422
    assert admin.post("/api/tasks", json={"title": "x", "assignee_id": 999}).status_code == 400
    task = make_task(admin)
    assert admin.patch(f"/api/tasks/{task['id']}", json={"title": None}).status_code == 422


def test_filters(admin):
    petya = make_user(admin)
    make_task(admin, title="Купить хлеб", category="Покупки", assignee_id=petya["id"])
    make_task(admin, title="Пропылесосить", category="Уборка")
    assert [t["title"] for t in admin.get(f"/api/tasks?assignee_id={petya['id']}").json()] == ["Купить хлеб"]
    assert [t["title"] for t in admin.get("/api/tasks?category=Уборка").json()] == ["Пропылесосить"]
    assert [t["title"] for t in admin.get("/api/tasks?q=хлеб").json()] == ["Купить хлеб"]


def test_move_reorders_columns(admin):
    a, b, c = (make_task(admin, title=t) for t in "abc")
    admin.post(f"/api/tasks/{c['id']}/move", json={"status": "todo", "index": 0})
    assert [t["title"] for t in board(admin)["todo"]] == ["c", "a", "b"]

    admin.post(f"/api/tasks/{a['id']}/move", json={"status": "in_progress", "index": 0})
    admin.post(f"/api/tasks/{b['id']}/move", json={"status": "in_progress", "index": 99})
    cols = board(admin)
    assert [t["title"] for t in cols["todo"]] == ["c"]
    assert [t["title"] for t in cols["in_progress"]] == ["a", "b"]


def test_done_sets_completed_at(admin):
    task = make_task(admin)
    done = admin.post(f"/api/tasks/{task['id']}/move", json={"status": "done", "index": 0}).json()
    assert done["completed_at"] is not None
    back = admin.patch(f"/api/tasks/{task['id']}", json={"status": "review"}).json()
    assert back["completed_at"] is None


def test_recurring_task_spawns_next(admin):
    today = date.today()
    task = make_task(admin, title="Полить цветы", recurrence="weekly", due_date=today.isoformat())
    admin.post(f"/api/tasks/{task['id']}/move", json={"status": "done", "index": 0})

    cols = board(admin)
    assert cols["done"][0]["recurrence"] == "none"
    [nxt] = cols["todo"]
    assert nxt["title"] == "Полить цветы"
    assert nxt["recurrence"] == "weekly"
    assert nxt["due_date"] == (today + timedelta(weeks=1)).isoformat()

    # повторное перемещение в «Готово» не создаёт ещё одну копию
    admin.patch(f"/api/tasks/{task['id']}", json={"status": "todo"})
    admin.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert len(board(admin)["todo"]) == 1


def test_overdue_recurring_task_skips_to_future(admin):
    long_ago = date.today() - timedelta(days=10)
    task = make_task(admin, recurrence="daily", due_date=long_ago.isoformat())
    admin.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert board(admin)["todo"][0]["due_date"] == (date.today() + timedelta(days=1)).isoformat()


def test_stats(admin):
    me = admin.get("/api/me").json()
    make_task(admin, assignee_id=me["id"])
    t = make_task(admin, assignee_id=me["id"])
    admin.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
    [row] = admin.get("/api/stats").json()
    assert row == {"user_id": me["id"], "open": 1, "done_week": 1}


def test_next_due_month_end():
    assert next_due(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    assert next_due(date(2026, 12, 15), "monthly") == date(2027, 1, 15)


def test_health_is_public(anon):
    assert anon.get("/api/health").json() == {"status": "ok"}


def test_secure_cookie_and_journal_mode(tmp_path, monkeypatch):
    from app import db
    from app.main import create_app

    monkeypatch.setenv("TODO_COOKIE_SECURE", "true")
    monkeypatch.setenv("TODO_SQLITE_JOURNAL_MODE", "delete")
    path = str(tmp_path / "x.db")
    client = TestClient(create_app(path), base_url="https://testserver")
    r = client.post("/api/setup", json={"username": "a", "display_name": "A", "password": "secret"})
    assert "secure" in r.headers["set-cookie"].lower()
    assert client.get("/api/me").status_code == 200
    conn = db.connect(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    conn.close()
