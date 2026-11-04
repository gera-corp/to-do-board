from datetime import timedelta

from fastapi.testclient import TestClient


def login(app, username, password):
    client = TestClient(app)
    assert client.post("/api/login", json={"username": username, "password": password}).status_code == 200
    return client


def make_user(admin, username="petya", **extra):
    body = {"username": username, "display_name": username.title(), "password": "pass1234", **extra}
    r = admin.post("/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def make_task(client, **fields):
    r = client.post("/api/tasks", json={"title": "Вынести мусор", **fields})
    assert r.status_code == 201, r.text
    return r.json()


def board(client):
    cols = {"todo": [], "in_progress": [], "review": [], "done": []}
    for t in client.get("/api/tasks").json():
        cols[t["status"]].append(t)
    return cols


def deliver(app, seconds=0):
    """Отправляет всё, что созрело в очереди уведомлений через seconds секунд."""
    dispatcher = app.state.dispatcher
    return dispatcher.deliver_due(dispatcher.clock() + timedelta(seconds=seconds))
