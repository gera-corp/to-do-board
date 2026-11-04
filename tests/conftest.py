from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


class FakeNotifier:
    enabled = True
    bot_username = "home_test_bot"

    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))

    def send_now(self, chat_id, text):
        self.sent.append((chat_id, text))

    def start(self, db_path):
        pass

    def stop(self):
        pass


class FakeClock:
    """Время, которое двигает сам тест."""

    def __init__(self):
        self.now = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def app(db_path, notifier, clock):
    return create_app(db_path, notifier=notifier, clock=clock)


@pytest.fixture
def anon(app):
    return TestClient(app)


@pytest.fixture
def admin(app):
    client = TestClient(app)
    r = client.post("/api/setup", json={"username": "mama", "display_name": "Мама", "password": "secret"})
    assert r.status_code == 201
    return client


@pytest.fixture(autouse=True)
def no_real_env_file(tmp_path, monkeypatch):
    """Тесты не должны подхватывать настоящий .env с токеном."""
    monkeypatch.setenv("TODO_ENV_FILE", str(tmp_path / "absent.env"))
