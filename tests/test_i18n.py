import ast
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.i18n import EN, tr
from app.telegram import build_digest, handle_update

from .helpers import login, make_task, make_user
from .test_features import link_telegram

APP_DIR = Path(__file__).parent.parent / "app"
CYRILLIC = re.compile("[а-яА-ЯёЁ]")


def literal_strings(call_names: set[str]) -> set[str]:
    """Строковые литералы, переданные первым текстовым аргументом в tr()/HTTPException()."""
    found = set()
    for path in APP_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            if name not in call_names:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and CYRILLIC.search(arg.value):
                    found.add(arg.value)
    return found


def test_every_server_message_has_english_translation():
    messages = literal_strings({"HTTPException", "tr"})
    assert messages, "не нашёл ни одного сообщения — тест сломан"
    missing = sorted(m for m in messages if m not in EN)
    assert missing == []


def test_translation_placeholders_match():
    for ru, en in EN.items():
        assert set(re.findall(r"\{(\w+)\}", ru)) == set(re.findall(r"\{(\w+)\}", en)), ru


def test_api_errors_follow_request_language(app, admin):
    client = TestClient(app)
    r = client.post("/api/login", json={"username": "mama", "password": "wrong"}, headers={"Accept-Language": "ru-RU"})
    assert r.json()["detail"] == "Неверный логин или пароль"
    r = client.post("/api/login", json={"username": "mama", "password": "wrong"}, headers={"Accept-Language": "en"})
    assert r.json()["detail"] == "Wrong username or password"
    # без заголовка — английский по умолчанию
    assert client.get("/api/tasks/999").json()["detail"] == "Please sign in"
    assert admin.get("/api/tasks/999").json()["detail"] == "Task not found"
    task = make_task(admin)
    r = admin.patch(f"/api/tasks/{task['id']}", json={"title": None}, headers={"Accept-Language": "ru"})
    assert r.json()["detail"] == "Поле title не может быть пустым"


def test_language_choice_is_saved_in_profile(admin):
    me = admin.get("/api/me").json()
    assert me["lang"] == "auto"
    assert admin.patch(f"/api/users/{me['id']}", json={"lang": "ru"}).json()["lang"] == "ru"
    assert admin.patch(f"/api/users/{me['id']}", json={"lang": "de"}).status_code == 422


def test_notifications_use_each_recipients_language(app, admin, db_path, notifier):
    petya = make_user(admin)
    petya_client = login(app, "petya", "pass1234")
    link_telegram(admin, db_path, 100)
    link_telegram(petya_client, db_path, 200)
    # язык для уведомлений — тот, на котором человек пользуется доской
    petya_client.get("/api/me", headers={"Accept-Language": "ru"})
    admin.get("/api/me", headers={"Accept-Language": "en"})

    task = make_task(admin, title="Вынести мусор", assignee_id=petya["id"], points=3)
    assert notifier.sent[-1] == (200, "📌 Мама поручает вам: «Вынести мусор»")
    petya_client.patch(f"/api/tasks/{task['id']}", json={"status": "review"})
    assert notifier.sent[-1] == (100, "👀 Petya asks you to review: «Вынести мусор»")
    admin.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert notifier.sent[-1] == (200, "✅ Сделано: «Вынести мусор», +3 очка\nОтметил(а): Мама")


def test_bot_speaks_users_language(admin, db_path):
    conn = db.connect(db_path)
    try:
        reply = handle_update(conn, {"message": {"chat": {"id": 5}, "text": "/tasks", "from": {"language_code": "ru"}}})
        assert "не привязан" in reply[0][1]
        reply = handle_update(conn, {"message": {"chat": {"id": 5}, "text": "/tasks", "from": {"language_code": "de"}}})
        assert "not linked" in reply[0][1]
    finally:
        conn.close()


def test_digest_in_users_language(admin, db_path):
    from datetime import date

    link_telegram(admin, db_path, 42)
    me = admin.get("/api/me", headers={"Accept-Language": "en"}).json()
    make_task(admin, title="Полить цветы", assignee_id=me["id"], due_date="2026-10-16")
    conn = db.connect(db_path)
    try:
        [(_, text)] = build_digest(conn, date(2026, 10, 17))
    finally:
        conn.close()
    assert text.startswith("☀️ Good morning!") and "Overdue" in text and "due 16 Oct" in text


def test_tr_formats_params():
    assert tr("Срок: {date}", "ru", date="1 дек") == "Срок: 1 дек"
    assert tr("Срок: {date}", "en", date="1 Dec") == "Due: 1 Dec"
