from app import db

from .helpers import board, make_task, make_user
from .test_features import link_telegram


def task_by_id(client, task_id):
    return client.get(f"/api/tasks/{task_id}").json()


def link(client, task_id, kind, other_id):
    return client.post(f"/api/tasks/{task_id}/links", json={"kind": kind, "task_id": other_id})


# ---------- подзадачи ----------

def test_subtask_is_a_full_task_with_parent(admin):
    petya = make_user(admin)
    parent = make_task(admin, title="Ремонт в детской")
    sub = make_task(admin, title="Покрасить стены", parent_id=parent["id"], assignee_id=petya["id"], points=5)
    assert sub["parent_id"] == parent["id"]
    assert sub["assignee_id"] == petya["id"] and sub["status"] == "todo"
    # подзадача — обычная карточка на доске
    assert {t["title"] for t in board(admin)["todo"]} == {"Ремонт в детской", "Покрасить стены"}


def test_only_one_level_of_subtasks(admin):
    parent = make_task(admin, title="Ремонт")
    sub = make_task(admin, title="Покраска", parent_id=parent["id"])
    other = make_task(admin, title="Другое")

    # у подзадачи не может быть своих подзадач
    assert admin.post("/api/tasks", json={"title": "x", "parent_id": sub["id"]}).status_code == 400
    # задачу с подзадачами нельзя сделать чьей-то подзадачей
    assert admin.patch(f"/api/tasks/{parent['id']}", json={"parent_id": other["id"]}).status_code == 400
    # задача не может быть родителем самой себе
    assert admin.patch(f"/api/tasks/{other['id']}", json={"parent_id": other["id"]}).status_code == 400
    assert admin.post("/api/tasks", json={"title": "x", "parent_id": 999}).status_code == 400


def test_parent_can_be_changed_and_removed(admin):
    a = make_task(admin, title="A")
    b = make_task(admin, title="B")
    sub = make_task(admin, title="Sub", parent_id=a["id"])
    assert admin.patch(f"/api/tasks/{sub['id']}", json={"parent_id": b["id"]}).json()["parent_id"] == b["id"]
    assert admin.patch(f"/api/tasks/{sub['id']}", json={"parent_id": None}).json()["parent_id"] is None
    kinds = [e["kind"] for e in admin.get(f"/api/tasks/{sub['id']}/events").json()]
    assert kinds.count("edited") == 2


def test_deleting_parent_keeps_subtasks(admin):
    parent = make_task(admin, title="Ремонт")
    sub = make_task(admin, title="Покраска", parent_id=parent["id"])
    admin.delete(f"/api/tasks/{parent['id']}")
    assert task_by_id(admin, sub["id"])["parent_id"] is None


def test_recurring_subtask_stays_under_parent(admin):
    parent = make_task(admin, title="Уборка")
    sub = make_task(admin, title="Пылесос", parent_id=parent["id"], recurrence="weekly")
    admin.patch(f"/api/tasks/{sub['id']}", json={"status": "done"})
    nxt = [t for t in board(admin)["todo"] if t["title"] == "Пылесос"][0]
    assert nxt["parent_id"] == parent["id"]


# ---------- связи ----------

def test_blocks_link_is_visible_from_both_sides(admin):
    paint = make_task(admin, title="Покрасить стены")
    buy = make_task(admin, title="Купить краску")
    r = link(admin, paint["id"], "blocked_by", buy["id"])
    assert r.status_code == 201

    paint_links = task_by_id(admin, paint["id"])["links"]
    buy_links = task_by_id(admin, buy["id"])["links"]
    assert [(l["type"], l["task_id"]) for l in paint_links] == [("blocked_by", buy["id"])]
    assert [(l["type"], l["task_id"]) for l in buy_links] == [("blocks", paint["id"])]
    # в списке задач связи тоже есть
    listed = {t["id"]: t for t in admin.get("/api/tasks").json()}
    assert listed[paint["id"]]["links"][0]["type"] == "blocked_by"


def test_relates_link_and_duplicates(admin):
    a = make_task(admin, title="A")
    b = make_task(admin, title="B")
    assert link(admin, a["id"], "relates", b["id"]).status_code == 201
    # та же связь с другой стороны — дубликат
    assert link(admin, b["id"], "relates", a["id"]).status_code == 409
    assert [l["type"] for l in task_by_id(admin, b["id"])["links"]] == ["relates"]


def test_link_validation(admin):
    a = make_task(admin, title="A")
    assert link(admin, a["id"], "blocks", a["id"]).status_code == 400
    assert link(admin, a["id"], "blocks", 999).status_code == 400
    assert link(admin, a["id"], "depends", a["id"]).status_code == 422


def test_blocking_cycles_are_rejected(admin):
    a, b, c = (make_task(admin, title=t) for t in "abc")
    assert link(admin, a["id"], "blocks", b["id"]).status_code == 201
    assert link(admin, b["id"], "blocks", c["id"]).status_code == 201
    r = link(admin, c["id"], "blocks", a["id"])
    assert r.status_code == 400 and "цикл" in r.json()["detail"]


def test_unlink_and_history(admin):
    a = make_task(admin, title="A")
    b = make_task(admin, title="B")
    link_id = link(admin, a["id"], "blocks", b["id"]).json()["links"][0]["id"]
    assert admin.delete(f"/api/links/{link_id}").status_code == 204
    assert task_by_id(admin, a["id"])["links"] == []
    for task in (a, b):
        kinds = [e["kind"] for e in admin.get(f"/api/tasks/{task['id']}/events").json()]
        assert kinds[-2:] == ["linked", "unlinked"]
    assert admin.delete(f"/api/links/{link_id}").status_code == 404


def test_deleting_task_removes_its_links(admin):
    a = make_task(admin, title="A")
    b = make_task(admin, title="B")
    link(admin, a["id"], "blocks", b["id"])
    admin.delete(f"/api/tasks/{a['id']}")
    assert task_by_id(admin, b["id"])["links"] == []


def test_unblocked_task_owner_is_notified(app, admin, db_path, notifier):
    from .helpers import login

    petya = make_user(admin)
    petya_client = login(app, "petya", "pass1234")
    link_telegram(petya_client, db_path, 300)

    buy = make_task(admin, title="Купить краску")
    sand = make_task(admin, title="Купить шкурку")
    paint = make_task(admin, title="Покрасить стены", assignee_id=petya["id"])
    link(admin, paint["id"], "blocked_by", buy["id"])
    link(admin, paint["id"], "blocked_by", sand["id"])
    notifier.sent.clear()

    admin.patch(f"/api/tasks/{buy['id']}", json={"status": "done"})
    assert notifier.sent == []  # ещё ждёт шкурку
    admin.post(f"/api/tasks/{sand['id']}/move", json={"status": "done", "index": 0})
    assert len(notifier.sent) == 1
    chat, text = notifier.sent[0]
    assert chat == 300 and "Можно начинать" in text and "Покрасить стены" in text
