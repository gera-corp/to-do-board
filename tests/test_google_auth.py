import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import google
from app.main import create_app

from .helpers import login, make_user

CLIENT_ID = "cid.apps.googleusercontent.com"
PUBLIC_URL = "https://todo.example.com"


def fake_id_token(**claims) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.signature"


@pytest.fixture
def gapp(db_path, notifier, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TODO_PUBLIC_URL", PUBLIC_URL)
    return create_app(db_path, notifier=notifier)


@pytest.fixture
def gadmin(gapp):
    client = TestClient(gapp)
    r = client.post("/api/setup", json={"username": "mama", "display_name": "Мама", "password": "secret"})
    assert r.status_code == 201
    return client


def start(client):
    r = client.get("/api/auth/google/start", follow_redirects=False)
    assert r.status_code == 303
    return r, {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}


def google_login(client, monkeypatch, state=None, **claims):
    """Проходит вход через Google; возвращает ответ callback и то, что ушло в обмен кода."""
    _, params = start(client)
    payload = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": time.time() + 300,
        "nonce": params["nonce"], "sub": "g-1001", "email": "mama@gmail.com", "email_verified": True,
    }
    payload.update(claims)
    captured = {}

    def fake_exchange(code, verifier, redirect_uri, client_id, client_secret):
        captured.update(code=code, verifier=verifier, redirect_uri=redirect_uri, client_secret=client_secret)
        return {"id_token": fake_id_token(**payload)}

    monkeypatch.setattr(google, "exchange_code", fake_exchange)
    r = client.get(
        f"/api/auth/google/callback?code=auth-code&state={state or params['state']}", follow_redirects=False
    )
    return r, params, captured


def set_google_email(admin, user_id, email):
    return admin.patch(f"/api/users/{user_id}", json={"google_email": email})


# ---------- настройки входа ----------

def test_setup_reports_login_options(anon, gapp):
    # anon создаётся раньше gapp, до того как в окружении появились настройки Google
    assert anon.get("/api/setup").json() == {"needs_setup": True, "google": False, "password_login": True, "site_title": ""}
    assert TestClient(gapp).get("/api/setup").json() == {"needs_setup": True, "google": True, "password_login": True, "site_title": ""}


def test_google_disabled_without_client_id(anon):
    assert anon.get("/api/auth/google/start", follow_redirects=False).status_code == 404


def test_start_redirects_to_google_with_pkce(gapp):
    client = TestClient(gapp)
    r, params = start(client)
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == f"{PUBLIC_URL}/api/auth/google/callback"
    assert params["response_type"] == "code"
    assert set(params["scope"].split()) == {"openid", "email", "profile"}
    assert params["code_challenge_method"] == "S256"
    assert params["state"] and params["nonce"] and params["code_challenge"]
    assert "httponly" in r.headers["set-cookie"].lower()


# ---------- вход ----------

def test_login_by_email_assigned_by_admin(gapp, gadmin, monkeypatch):
    me = gadmin.get("/api/me").json()
    assert set_google_email(gadmin, me["id"], " Mama@Gmail.com ").json()["google_email"] == "mama@gmail.com"

    client = TestClient(gapp)
    r, params, captured = google_login(client, monkeypatch)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/api/me").json()["username"] == "mama"
    # PKCE: в обмен ушёл verifier, из которого получен challenge в ссылке
    challenge = base64.urlsafe_b64encode(hashlib.sha256(captured["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert challenge == params["code_challenge"]
    assert captured["redirect_uri"] == f"{PUBLIC_URL}/api/auth/google/callback"
    assert captured["client_secret"] == "client-secret"


def test_google_account_is_remembered_by_sub(gapp, gadmin, monkeypatch):
    me = gadmin.get("/api/me").json()
    set_google_email(gadmin, me["id"], "mama@gmail.com")
    google_login(TestClient(gapp), monkeypatch)
    # почту в Google сменили, но аккаунт тот же — вход продолжает работать
    client = TestClient(gapp)
    r, _, _ = google_login(client, monkeypatch, email="new-name@gmail.com")
    assert r.headers["location"] == "/"
    assert client.get("/api/me").json()["username"] == "mama"


def test_unknown_google_account_is_not_let_in(gapp, gadmin, monkeypatch):
    client = TestClient(gapp)
    r, _, _ = google_login(client, monkeypatch, email="stranger@gmail.com", sub="g-999")
    assert r.headers["location"] == "/?login_error=not_allowed"
    assert client.get("/api/me").status_code == 401


def test_state_is_bound_to_browser_and_single_use(gapp, gadmin, monkeypatch):
    me = gadmin.get("/api/me").json()
    set_google_email(gadmin, me["id"], "mama@gmail.com")
    _, foreign = start(TestClient(gapp))

    # state из чужого браузера не подходит
    victim = TestClient(gapp)
    r, _, _ = google_login(victim, monkeypatch, state=foreign["state"])
    assert r.headers["location"] == "/?login_error=state"
    assert victim.get("/api/me").status_code == 401

    # повторно использовать state нельзя
    client = TestClient(gapp)
    r, params, _ = google_login(client, monkeypatch)
    assert r.headers["location"] == "/"
    client.cookies.set("oauth_state", params["state"])
    again = client.get(f"/api/auth/google/callback?code=x&state={params['state']}", follow_redirects=False)
    assert again.headers["location"] == "/?login_error=state"


@pytest.mark.parametrize("claims", [
    {"aud": "someone-else.apps.googleusercontent.com"},
    {"iss": "https://evil.example.com"},
    {"exp": time.time() - 10},
    {"nonce": "wrong"},
    {"email_verified": False},
])
def test_invalid_id_token_is_rejected(gapp, gadmin, monkeypatch, claims):
    me = gadmin.get("/api/me").json()
    set_google_email(gadmin, me["id"], "mama@gmail.com")
    client = TestClient(gapp)
    r, _, _ = google_login(client, monkeypatch, **claims)
    assert r.headers["location"] == "/?login_error=token"
    assert client.get("/api/me").status_code == 401


def test_user_cancelled_at_google(gapp):
    client = TestClient(gapp)
    _, params = start(client)
    r = client.get(f"/api/auth/google/callback?error=access_denied&state={params['state']}", follow_redirects=False)
    assert r.headers["location"] == "/?login_error=cancelled"


# ---------- привязка почты ----------

def test_only_admin_assigns_google_email(gapp, gadmin):
    petya = make_user(gadmin, google_email="petya@gmail.com")
    assert petya["google_email"] == "petya@gmail.com"
    petya_client = login(gapp, "petya", "pass1234")
    assert set_google_email(petya_client, petya["id"], "other@gmail.com").status_code == 403

    me = gadmin.get("/api/me").json()
    assert set_google_email(gadmin, me["id"], "PETYA@gmail.com").status_code == 409
    assert set_google_email(gadmin, me["id"], "not-an-email").status_code == 422
    assert set_google_email(gadmin, petya["id"], "").json()["google_email"] is None


# ---------- вход по паролю ----------

def test_password_login_can_be_disabled(db_path, notifier, monkeypatch):
    monkeypatch.setenv("TODO_PASSWORD_LOGIN", "false")
    app = create_app(db_path, notifier=notifier)
    client = TestClient(app)
    client.post("/api/setup", json={"username": "mama", "display_name": "Мама", "password": "secret"})
    assert client.get("/api/setup").json()["password_login"] is False
    r = TestClient(app).post("/api/login", json={"username": "mama", "password": "secret"})
    assert r.status_code == 403


def test_password_login_is_rate_limited_per_account(app, admin):
    make_user(admin)
    client = TestClient(app)
    for _ in range(10):
        assert client.post("/api/login", json={"username": "mama", "password": "wrong"}).status_code == 401
    # даже верный пароль не пускает, пока действует блокировка
    assert client.post("/api/login", json={"username": "mama", "password": "secret"}).status_code == 429
    # другой человек при этом входит
    assert client.post("/api/login", json={"username": "petya", "password": "pass1234"}).status_code == 200
