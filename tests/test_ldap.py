import ssl

import pytest
from fastapi.testclient import TestClient
from ldap3 import Connection, MOCK_SYNC, OFFLINE_SLAPD_2_4, Server

from app.ldap_auth import LdapConfig, LdapDirectory, LdapUnavailable, LdapUser
from app.main import create_app

BASE = "dc=example,dc=org"
BIND_DN = f"cn=reader,{BASE}"
GROUP = f"cn=board_users,ou=groups,{BASE}"


def make_mock_directory(**overrides):
    """Настоящий разбор фильтров ldap3 на сервере в памяти."""
    server = Server("mock", get_info=OFFLINE_SLAPD_2_4)
    seed = Connection(server, client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(BIND_DN, {"objectClass": "person", "cn": "reader", "sn": "r", "userPassword": "service"})
    people = [("alice", "Alice A", [GROUP]), ("bob", "Bob B", []), ("twin", "One", [GROUP])]
    for login, name, groups in people:
        seed.strategy.add_entry(f"uid={login},ou=people,{BASE}", {
            "objectClass": "inetOrgPerson", "uid": login, "cn": login, "sn": "x", "displayName": name,
            "userPassword": f"{login}-pass", "memberOf": groups,
        })
    seed.strategy.add_entry(f"uid=twin,ou=other,{BASE}", {
        "objectClass": "inetOrgPerson", "uid": "twin", "cn": "t", "sn": "x", "displayName": "Two", "userPassword": "x",
    })

    def connect(user, password):
        return Connection(server, user=user, password=password, client_strategy=MOCK_SYNC)

    config = LdapConfig(url="ldap://mock", base_dn=BASE, bind_dn=BIND_DN, bind_password="service", **overrides)
    return LdapDirectory(config, connection_factory=connect)


def test_valid_password_returns_directory_identity():
    assert make_mock_directory().authenticate("alice", "alice-pass") == LdapUser("alice", "Alice A")


@pytest.mark.parametrize("username,password", [
    ("alice", "wrong"), ("nobody", "x"), ("alice", ""), ("", "x"), ("alice\x00", "alice-pass"),
])
def test_rejected_logins(username, password):
    assert make_mock_directory().authenticate(username, password) is None


def test_group_filter_limits_access():
    directory = make_mock_directory(user_filter=f"(&(uid={{username}})(memberOf={GROUP}))")
    assert directory.authenticate("alice", "alice-pass") is not None
    assert directory.authenticate("bob", "bob-pass") is None


@pytest.mark.parametrize("injection", ["*", "alice)(uid=*", "alice)(|(uid=*"])
def test_filter_injection_finds_nobody(injection):
    assert make_mock_directory().authenticate(injection, "alice-pass") is None


def test_ambiguous_match_is_refused():
    assert make_mock_directory().authenticate("twin", "twin-pass") is None


def test_wrong_service_password_means_directory_is_down():
    directory = make_mock_directory()
    directory.config.bind_password = "wrong"
    with pytest.raises(LdapUnavailable):
        directory.authenticate("alice", "alice-pass")


def test_filter_must_contain_username():
    with pytest.raises(ValueError):
        LdapConfig(url="ldap://x", base_dn=BASE, bind_dn="", bind_password="", user_filter="(uid=alice)")


def real_directory(**overrides):
    return LdapDirectory(LdapConfig(url="ldaps://dc.example.org", base_dn=BASE, bind_dn="x", bind_password="x", **overrides))


def test_certificate_is_verified_by_default():
    assert real_directory().server.tls.validate == ssl.CERT_REQUIRED


def test_verification_is_off_only_on_request(caplog):
    with caplog.at_level("WARNING", logger="ldap"):
        assert real_directory(tls_insecure=True).server.tls.validate == ssl.CERT_NONE
    assert "LDAP_TLS_INSECURE" in caplog.text


class FakeDirectory:
    def __init__(self, users=None, link_existing=False):
        self.users = users or {}
        self.down = False
        self.config = LdapConfig(url="ldap://x", base_dn=BASE, bind_dn="", bind_password="", link_existing=link_existing)

    def authenticate(self, username, password):
        if self.down:
            raise LdapUnavailable("нет связи")
        user = self.users.get(username.lower())
        return LdapUser(*user[1:]) if user and user[0] == password else None


@pytest.fixture
def directory():
    return FakeDirectory({"ivan": ("ldap-pass", "Ivan", "Иван Иванов"), "root": ("ldap-pass", "root", "Чужой")})


@pytest.fixture
def lapp(db_path, notifier, directory):
    app = create_app(db_path, notifier=notifier, directory=directory)
    TestClient(app).post("/api/setup", json={"username": "root", "display_name": "Админ", "password": "local-pass"})
    return app


def login(app, username, password):
    client = TestClient(app)
    return client, client.post("/api/login", json={"username": username, "password": password})


def test_first_login_creates_regular_user(lapp):
    client, r = login(lapp, "ivan", "ldap-pass")
    assert r.status_code == 200
    me = client.get("/api/me").json()
    assert (me["username"], me["display_name"], me["is_admin"], me["auth_source"]) == ("Ivan", "Иван Иванов", False, "ldap")
    # второй вход — тот же человек, логин без учёта регистра
    _, again = login(lapp, "IVAN", "ldap-pass")
    assert again.json()["id"] == me["id"]


def test_directory_name_change_is_picked_up(lapp, directory):
    login(lapp, "ivan", "ldap-pass")
    directory.users["ivan"] = ("ldap-pass", "Ivan", "Иван Петров")
    client, _ = login(lapp, "ivan", "ldap-pass")
    assert client.get("/api/me").json()["display_name"] == "Иван Петров"


def test_directory_user_cannot_take_over_local_admin(lapp):
    """В каталоге есть свой root: это не повод войти в локального администратора."""
    client, r = login(lapp, "root", "ldap-pass")
    assert r.status_code == 401
    assert client.get("/api/me").status_code == 401
    _, r = login(lapp, "root", "local-pass")
    assert r.status_code == 200 and r.json()["is_admin"] is True and r.json()["auth_source"] == "local"


def test_link_existing_is_explicit(db_path, notifier):
    directory = FakeDirectory({"root": ("ldap-pass", "root", "Каталог")}, link_existing=True)
    app = create_app(db_path, notifier=notifier, directory=directory)
    TestClient(app).post("/api/setup", json={"username": "root", "display_name": "Админ", "password": "local-pass"})
    _, r = login(app, "root", "ldap-pass")
    assert r.status_code == 200 and r.json()["auth_source"] == "ldap" and r.json()["is_admin"] is True
    # после привязки старый локальный пароль больше не действует
    _, r = login(app, "root", "local-pass")
    assert r.status_code == 401


def test_removed_from_directory_cannot_use_stored_credentials(lapp, directory):
    login(lapp, "ivan", "ldap-pass")
    directory.users.clear()
    _, r = login(lapp, "ivan", "ldap-pass")
    assert r.status_code == 401


def test_local_admin_works_while_directory_is_down(lapp, directory):
    directory.down = True
    _, r = login(lapp, "root", "local-pass")
    assert r.status_code == 200
    _, r = login(lapp, "ivan", "ldap-pass")
    assert r.status_code == 503


def test_local_password_never_reaches_the_directory(lapp, directory):
    seen = []
    original = directory.authenticate
    directory.authenticate = lambda u, p: seen.append((u, p)) or original(u, p)
    assert login(lapp, "root", "local-pass")[1].status_code == 200
    assert seen == []


def test_ldap_user_has_no_local_password(lapp):
    client, r = login(lapp, "ivan", "ldap-pass")
    uid = r.json()["id"]
    assert client.patch(f"/api/users/{uid}", json={"password": "newpass"}).status_code == 400
    assert client.patch(f"/api/users/{uid}", json={"color": "#FF0000"}).status_code == 200


def test_failed_ldap_logins_are_rate_limited(lapp):
    client = TestClient(lapp)
    for _ in range(10):
        assert client.post("/api/login", json={"username": "ivan", "password": "x"}).status_code == 401
    assert client.post("/api/login", json={"username": "ivan", "password": "ldap-pass"}).status_code == 429


def test_setup_reports_ldap(lapp, anon):
    assert TestClient(lapp).get("/api/setup").json()["ldap"] is True
    assert anon.get("/api/setup").json()["ldap"] is False


def test_ldap_form_works_with_local_passwords_off(db_path, notifier, directory, monkeypatch):
    monkeypatch.setenv("TODO_PASSWORD_LOGIN", "false")
    app = create_app(db_path, notifier=notifier, directory=directory)
    TestClient(app).post("/api/setup", json={"username": "boss", "display_name": "Б", "password": "local-pass"})
    assert login(app, "ivan", "ldap-pass")[1].status_code == 200
    assert login(app, "boss", "local-pass")[1].status_code == 401


def test_no_login_through_directory_before_setup(db_path, notifier, directory):
    app = create_app(db_path, notifier=notifier, directory=directory)
    assert login(app, "ivan", "ldap-pass")[1].status_code == 401
    assert TestClient(app).get("/api/setup").json()["needs_setup"] is True
