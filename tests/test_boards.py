from fastapi.testclient import TestClient

from app import db
from app.db import MIGRATIONS
from app.main import create_app

from .helpers import login, make_task, make_user
from .test_features import link_telegram


def boards(client):
    return client.get("/api/boards").json()


def make_board(client, title="Работа", **extra):
    r = client.post("/api/boards", json={"title": title, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def column(board, role):
    return next(c for c in board["columns"] if c["role"] == role)


def add_column(client, board, title, role):
    r = client.post(f"/api/boards/{board['id']}/columns", json={"title": title, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def task_rows(client, board):
    return client.get(f"/api/tasks?board_id={board['id']}").json()


# ---------- миграция ----------

def test_migration_keeps_tasks_and_everything_attached_to_them(tmp_path):
    """Обновление существующей базы: задачи и всё связанное с ними остаётся на месте."""
    path = str(tmp_path / "old.db")
    conn = db.connect(path)
    for number, script in enumerate(MIGRATIONS[:6], start=1):
        conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
    conn.execute("INSERT INTO users (username, display_name, password_hash) VALUES ('a', 'A', 'x')")
    for title, status in (("t", "todo"), ("p", "in_progress"), ("r", "review"), ("d", "done")):
        conn.execute("INSERT INTO tasks (title, status, position) VALUES (?, ?, 5)", (title, status))
    conn.execute("INSERT INTO checklist_items (task_id, text) VALUES (1, 'пункт')")
    conn.execute("INSERT INTO task_events (task_id, kind) VALUES (1, 'created')")
    conn.execute("INSERT INTO task_links (kind, from_id, to_id) VALUES ('blocks', 1, 2)")
    conn.execute("UPDATE tasks SET parent_id = 1 WHERE id = 3")
    conn.commit()
    conn.close()

    create_app(path)

    conn = db.connect(path)
    rows = conn.execute(
        """SELECT t.title, t.status, t.position, c.title AS col, c.role, b.title AS board, t.parent_id
           FROM tasks t JOIN board_columns c ON c.id = t.column_id JOIN boards b ON b.id = t.board_id
           ORDER BY t.id"""
    ).fetchall()
    assert [tuple(r)[:5] for r in rows] == [
        ("t", "todo", 5, "To Do", "todo"), ("p", "in_progress", 5, "In Progress", "in_progress"),
        ("r", "review", 5, "Review", "review"), ("d", "done", 5, "Done", "done"),
    ]
    assert rows[0]["board"] == "Дела по дому" and rows[2]["parent_id"] == 1
    assert conn.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM settings WHERE key = 'site_title'").fetchone()[0] == "Наш дом"
    conn.close()


def test_fresh_install_has_neutral_board(anon):
    assert anon.get("/api/setup").json()["site_title"] == ""


def test_new_install_starts_with_one_board(admin):
    [board] = boards(admin)
    assert board["title"] == "" and board["points_enabled"] is True
    assert [(c["title"], c["role"]) for c in board["columns"]] == [
        ("To Do", "todo"), ("In Progress", "in_progress"), ("Review", "review"), ("Done", "done"),
    ]


# ---------- название ----------

def test_site_title_is_set_by_admin_only(app, admin):
    make_user(admin)
    assert login(app, "petya", "pass1234").put("/api/settings", json={"site_title": "X"}).status_code == 403
    assert admin.put("/api/settings", json={"site_title": "  Отдел разработки "}).status_code == 200
    assert admin.get("/api/config").json()["site_title"] == "Отдел разработки"
    assert TestClient(app).get("/api/setup").json()["site_title"] == "Отдел разработки"
    manifest = TestClient(app).get("/manifest.webmanifest").json()
    assert (manifest["name"], manifest["short_name"]) == ("Отдел разработки", "Отдел разработки")


# ---------- доски ----------

def test_only_admin_manages_boards(app, admin):
    make_user(admin)
    petya = login(app, "petya", "pass1234")
    [board] = boards(admin)
    assert petya.post("/api/boards", json={"title": "x"}).status_code == 403
    assert petya.patch(f"/api/boards/{board['id']}", json={"title": "x"}).status_code == 403
    assert petya.delete(f"/api/boards/{board['id']}").status_code == 403
    assert petya.post(f"/api/boards/{board['id']}/columns", json={"title": "x", "role": "todo"}).status_code == 403
    assert len(boards(petya)) == 1  # видеть доски может каждый


def test_new_board_has_default_columns_and_tasks_stay_apart(admin):
    work = make_board(admin)
    assert [c["role"] for c in work["columns"]] == ["todo", "in_progress", "review", "done"]
    home = boards(admin)[0]
    make_task(admin, title="Дома")
    r = admin.post("/api/tasks", json={"title": "На работе", "board_id": work["id"]})
    assert r.status_code == 201 and r.json()["column_id"] == column(work, "todo")["id"]
    assert [t["title"] for t in task_rows(admin, home)] == ["Дома"]
    assert [t["title"] for t in task_rows(admin, work)] == ["На работе"]


def test_cannot_delete_last_board_and_tasks_need_confirmation(admin):
    [home] = boards(admin)
    assert admin.delete(f"/api/boards/{home['id']}").status_code == 400
    work = make_board(admin)
    make_task(admin, title="x", board_id=work["id"])
    assert admin.delete(f"/api/boards/{work['id']}").status_code == 409
    assert admin.delete(f"/api/boards/{work['id']}?with_tasks=true").status_code == 204
    assert [b["id"] for b in boards(admin)] == [home["id"]]


# ---------- колонки ----------

def test_custom_workflow_columns(admin):
    board = make_board(admin, title="Разработка")
    design = add_column(admin, board, "Дизайн", "in_progress")
    code = add_column(admin, board, "Код", "in_progress")
    order = [column(board, "todo")["id"], design["id"], code["id"], column(board, "in_progress")["id"],
             column(board, "review")["id"], column(board, "done")["id"]]
    assert admin.put(f"/api/boards/{board['id']}/columns/order", json={"ids": order}).status_code == 200
    fresh = next(b for b in boards(admin) if b["id"] == board["id"])
    assert [c["id"] for c in fresh["columns"]] == order

    task = make_task(admin, board_id=board["id"])
    moved = admin.post(f"/api/tasks/{task['id']}/move", json={"column_id": design["id"], "index": 0}).json()
    assert (moved["column_id"], moved["status"]) == (design["id"], "in_progress")
    # между колонками одной роли: задача не закрывается, но перенос виден в истории
    moved = admin.post(f"/api/tasks/{task['id']}/move", json={"column_id": code["id"], "index": 0}).json()
    assert moved["column_id"] == code["id"] and moved["completed_at"] is None
    last = admin.get(f"/api/tasks/{task['id']}/events").json()[-1]
    assert last["kind"] == "status" and (last["payload"]["from_title"], last["payload"]["to_title"]) == ("Дизайн", "Код")


def test_any_column_with_done_role_finishes_the_task(admin):
    board = make_board(admin)
    shipped = add_column(admin, board, "Выкатено", "done")
    me = admin.get("/api/me").json()
    task = make_task(admin, board_id=board["id"], assignee_id=me["id"], points=5)
    done = admin.patch(f"/api/tasks/{task['id']}", json={"column_id": shipped["id"]}).json()
    assert done["status"] == "done" and done["completed_at"]
    assert admin.get(f"/api/leaderboard?board_id={board['id']}").json()[0]["points"] == 5


def test_status_means_first_column_with_that_role(admin):
    board = make_board(admin)
    admin.delete(f"/api/columns/{column(board, 'review')['id']}")
    task = make_task(admin, board_id=board["id"])
    r = admin.patch(f"/api/tasks/{task['id']}", json={"status": "review"})
    assert r.status_code == 400  # на этой доске нет проверки


def test_column_must_belong_to_the_task_board(admin):
    work = make_board(admin)
    task = make_task(admin)
    alien = column(work, "done")["id"]
    assert admin.patch(f"/api/tasks/{task['id']}", json={"column_id": alien}).status_code == 400
    assert admin.post(f"/api/tasks/{task['id']}/move", json={"column_id": alien, "index": 0}).status_code == 400
    assert admin.post("/api/tasks", json={"title": "x", "board_id": boards(admin)[0]["id"], "column_id": alien}).status_code == 400
    assert admin.post("/api/tasks", json={"title": "x", "column_id": 9999}).status_code == 400


def test_board_keeps_start_and_done_columns(admin):
    [board] = boards(admin)
    assert admin.delete(f"/api/columns/{column(board, 'todo')['id']}").status_code == 400
    assert admin.delete(f"/api/columns/{column(board, 'done')['id']}").status_code == 400
    assert admin.patch(f"/api/columns/{column(board, 'done')['id']}", json={"role": "review"}).status_code == 400
    assert admin.delete(f"/api/columns/{column(board, 'review')['id']}").status_code == 204


def test_rename_column_keeps_tasks(admin):
    [board] = boards(admin)
    task = make_task(admin)
    col = column(board, "todo")
    assert admin.patch(f"/api/columns/{col['id']}", json={"title": "Бэклог"}).json()["title"] == "Бэклог"
    assert admin.get(f"/api/tasks/{task['id']}").json()["column_id"] == col["id"]


def test_role_change_updates_tasks_but_never_silently_closes_them(admin):
    [board] = boards(admin)
    review = column(board, "review")
    task = make_task(admin, status="review")
    assert admin.patch(f"/api/columns/{review['id']}", json={"role": "done"}).status_code == 400
    assert admin.patch(f"/api/columns/{review['id']}", json={"role": "in_progress"}).status_code == 200
    assert admin.get(f"/api/tasks/{task['id']}").json()["status"] == "in_progress"


def test_deleting_column_with_tasks_needs_a_target(admin):
    [board] = boards(admin)
    review, progress = column(board, "review"), column(board, "in_progress")
    task = make_task(admin, status="review")
    assert admin.delete(f"/api/columns/{review['id']}").status_code == 400
    assert admin.delete(f"/api/columns/{review['id']}?move_to={column(board, 'done')['id']}").status_code == 400
    other = make_board(admin)
    assert admin.delete(f"/api/columns/{review['id']}?move_to={column(other, 'todo')['id']}").status_code == 400
    assert admin.delete(f"/api/columns/{review['id']}?move_to={progress['id']}").status_code == 204
    moved = admin.get(f"/api/tasks/{task['id']}").json()
    assert (moved["column_id"], moved["status"]) == (progress["id"], "in_progress")


# ---------- логика на своих колонках ----------

def test_recurring_task_returns_to_first_start_column_of_its_board(admin):
    board = make_board(admin)
    backlog = add_column(admin, board, "Бэклог", "todo")
    order = [backlog["id"]] + [c["id"] for c in board["columns"]]
    admin.put(f"/api/boards/{board['id']}/columns/order", json={"ids": order})
    task = make_task(admin, board_id=board["id"], recurrence="weekly")
    admin.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    [next_task] = [t for t in task_rows(admin, board) if t["status"] != "done"]
    assert (next_task["board_id"], next_task["column_id"]) == (board["id"], backlog["id"])


def test_review_role_notifies_author_from_custom_column(app, admin, db_path, notifier):
    board = make_board(admin)
    qa = add_column(admin, board, "QA", "review")
    petya = make_user(admin)
    petya_client = login(app, "petya", "pass1234")
    link_telegram(admin, db_path, 100)
    task = make_task(admin, board_id=board["id"], assignee_id=petya["id"])
    notifier.sent.clear()
    petya_client.post(f"/api/tasks/{task['id']}/move", json={"column_id": qa["id"], "index": 0})
    assert notifier.sent[-1][0] == 100 and "review" in notifier.sent[-1][1]


def test_points_can_be_turned_off_per_board(app, admin, db_path, notifier):
    board = make_board(admin, points_enabled=False)
    assert board["points_enabled"] is False
    petya = make_user(admin)
    link_telegram(login(app, "petya", "pass1234"), db_path, 200)
    task = make_task(admin, board_id=board["id"], assignee_id=petya["id"], points=5)
    admin.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert "+5" not in notifier.sent[-1][1]
    assert admin.get(f"/api/leaderboard?board_id={board['id']}").json()[0]["points"] == 0


def test_leaderboard_counts_only_requested_board(admin):
    me = admin.get("/api/me").json()
    work = make_board(admin)
    a = make_task(admin, assignee_id=me["id"], points=3)
    b = make_task(admin, assignee_id=me["id"], points=5, board_id=work["id"])
    for t in (a, b):
        admin.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
    assert admin.get(f"/api/leaderboard?board_id={work['id']}").json()[0]["points"] == 5
    assert admin.get("/api/leaderboard").json()[0]["points"] == 8


def test_subtasks_and_links_stay_within_one_board(admin):
    work = make_board(admin)
    here = make_task(admin, title="здесь")
    there = make_task(admin, title="там", board_id=work["id"])
    assert admin.post("/api/tasks", json={"title": "x", "board_id": work["id"], "parent_id": here["id"]}).status_code == 400
    assert admin.patch(f"/api/tasks/{there['id']}", json={"parent_id": here["id"]}).status_code == 400
    assert admin.post(f"/api/tasks/{here['id']}/links", json={"kind": "relates", "task_id": there["id"]}).status_code == 400


def test_subtask_defaults_to_parents_board(admin):
    work = make_board(admin)
    parent = make_task(admin, board_id=work["id"])
    sub = make_task(admin, title="sub", parent_id=parent["id"])
    assert sub["board_id"] == work["id"]


def test_bot_lists_tasks_with_custom_column_titles(admin, db_path):
    from .test_features import bot

    link_telegram(admin, db_path, 1)
    me = admin.get("/api/me").json()
    [board] = boards(admin)
    admin.patch(f"/api/columns/{column(board, 'todo')['id']}", json={"title": "Бэклог"})
    make_task(admin, title="Задача", assignee_id=me["id"])
    [(_, text)] = bot(db_path, 1, "/tasks")
    assert "Бэклог" in text and "Задача" in text
