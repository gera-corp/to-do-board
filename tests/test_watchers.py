from app import db
from app.db import MIGRATIONS
from app.main import create_app

from .helpers import login, make_task, make_user


def task_of(client, task_id):
    return client.get(f"/api/tasks/{task_id}").json()


def test_creator_watches_new_task(admin):
    me = admin.get("/api/me").json()
    task = make_task(admin)
    assert task["watchers"] == [me["id"]]


def test_assignee_starts_watching_and_previous_stays(admin):
    me = admin.get("/api/me").json()
    petya, vasya = make_user(admin, "petya"), make_user(admin, "vasya")
    task = make_task(admin, assignee_id=petya["id"])
    admin.patch(f"/api/tasks/{task['id']}", json={"assignee_id": vasya["id"]})
    assert task_of(admin, task["id"])["watchers"] == sorted([me["id"], petya["id"], vasya["id"]])


def test_commenter_starts_watching(app, admin):
    petya = make_user(admin, "petya")
    task = make_task(admin)
    login(app, "petya", "pass1234").post(f"/api/tasks/{task['id']}/comments", json={"text": "Займусь"})
    assert petya["id"] in task_of(admin, task["id"])["watchers"]


def test_eye_toggles_only_self(app, admin):
    petya = make_user(admin, "petya")
    me = admin.get("/api/me").json()
    task = make_task(admin)
    client = login(app, "petya", "pass1234")
    assert client.post(f"/api/tasks/{task['id']}/watch").json()["watchers"] == [me["id"], petya["id"]]
    assert client.post(f"/api/tasks/{task['id']}/watch").json()["watchers"] == [me["id"], petya["id"]]
    assert client.delete(f"/api/tasks/{task['id']}/watch").json()["watchers"] == [me["id"]]
    assert client.post("/api/tasks/999/watch").status_code == 404


def test_unwatched_returns_only_on_next_trigger(admin):
    me = admin.get("/api/me").json()
    task = make_task(admin)
    admin.delete(f"/api/tasks/{task['id']}/watch")
    admin.patch(f"/api/tasks/{task['id']}", json={"priority": "high"})
    assert task_of(admin, task["id"])["watchers"] == []
    admin.post(f"/api/tasks/{task['id']}/comments", json={"text": "Вернулся"})
    assert task_of(admin, task["id"])["watchers"] == [me["id"]]


def test_repeat_copy_inherits_watchers(app, admin):
    petya = make_user(admin, "petya")
    task = make_task(admin, recurrence="weekly")
    login(app, "petya", "pass1234").post(f"/api/tasks/{task['id']}/watch")
    admin.post(f"/api/tasks/{task['id']}/move", json={"status": "done", "index": 0})
    copy = next(t for t in admin.get("/api/tasks").json() if t["id"] != task["id"])
    assert copy["watchers"] == task_of(admin, task["id"])["watchers"]
    assert petya["id"] in copy["watchers"]


def test_deleted_user_leaves_watchers(admin):
    me = admin.get("/api/me").json()
    petya = make_user(admin, "petya")
    task = make_task(admin, assignee_id=petya["id"])
    assert admin.delete(f"/api/users/{petya['id']}").status_code == 204
    assert task_of(admin, task["id"])["watchers"] == [me["id"]]


def test_migration_backfills_watchers(tmp_path):
    path = str(tmp_path / "old.db")
    conn = db.connect(path)
    for number, script in enumerate(MIGRATIONS[:8], start=1):
        conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
    for name in ("a", "b", "c"):
        conn.execute("INSERT INTO users (username, display_name, password_hash) VALUES (?, ?, 'x')", (name, name))
    conn.execute("INSERT INTO tasks (title, creator_id, assignee_id, board_id, column_id) VALUES ('t', 1, 2, 1, 1)")
    conn.execute("INSERT INTO tasks (title, board_id, column_id) VALUES ('nobody', 1, 1)")
    conn.execute("INSERT INTO task_events (task_id, user_id, kind, payload) VALUES (1, 3, 'comment', '{}')")
    conn.execute("INSERT INTO task_events (task_id, user_id, kind, payload) VALUES (1, 3, 'comment', '{}')")
    conn.commit()
    conn.close()

    create_app(path)
    create_app(path)

    conn = db.connect(path)
    rows = conn.execute("SELECT task_id, user_id FROM task_watchers ORDER BY task_id, user_id").fetchall()
    assert [tuple(r) for r in rows] == [(1, 1), (1, 2), (1, 3)]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
