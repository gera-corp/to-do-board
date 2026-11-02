import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.main import create_app

PROXY = "192.168.33.1"
NETWORKS = "192.168.33.0/24,10.189.0.0/16"


@pytest.fixture
def limited(db_path, notifier, monkeypatch):
    monkeypatch.setenv("TODO_ALLOWED_NETWORKS", NETWORKS)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", PROXY)
    return create_app(db_path, notifier=notifier)


@pytest.fixture
def through_proxy(limited):
    """Те же слои, что и в работе: сначала uvicorn разбирает X-Forwarded-For, потом доска."""
    proxied = ProxyHeadersMiddleware(limited, trusted_hosts=PROXY)
    client = TestClient(proxied, client=(PROXY, 40000))

    def get(forwarded_for=None, path="/api/setup"):
        headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
        return client.get(path, headers=headers)

    return get


def test_without_the_setting_every_address_is_served(app):
    assert TestClient(app, client=("203.0.113.7", 1)).get("/api/setup").status_code == 200


@pytest.mark.parametrize("address", ["10.189.93.6", "192.168.33.8"])
def test_listed_networks_are_served(limited, address):
    client = TestClient(limited, client=(address, 1))
    assert client.get("/").status_code == 200
    assert client.get("/api/setup").status_code == 200


@pytest.mark.parametrize("address", ["203.0.113.7", "10.190.0.1", "192.168.34.1", "2001:db8::1"])
def test_other_addresses_are_refused(limited, address):
    assert TestClient(limited, client=(address, 1)).get("/api/setup").status_code == 403


@pytest.mark.parametrize("path", ["/", "/app.js", "/sw.js", "/manifest.webmanifest", "/api/health/"])
def test_refusal_covers_the_page_and_files(limited, path):
    assert TestClient(limited, client=("203.0.113.7", 1)).get(path).status_code == 403


def test_refused_address_cannot_reach_sign_in(limited):
    client = TestClient(limited, client=("203.0.113.7", 1))
    response = client.post("/api/login", json={"username": "a", "password": "b"})
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_health_check_works_from_any_address(limited):
    assert TestClient(limited, client=("127.0.0.1", 1)).get("/api/health").status_code == 200


@pytest.mark.parametrize("address", ["testclient", "", "unknown"])
def test_unreadable_address_is_refused(limited, address):
    assert TestClient(limited, client=(address, 1)).get("/api/setup").status_code == 403


def test_ipv4_mapped_address_counts_as_ipv4(limited):
    assert TestClient(limited, client=("::ffff:10.189.93.6", 1)).get("/api/setup").status_code == 200


def test_proxy_passes_the_real_address(through_proxy):
    assert through_proxy("10.189.93.6").status_code == 200
    assert through_proxy("178.223.88.25").status_code == 403


def test_address_forged_by_the_visitor_is_ignored(through_proxy):
    # Прокси дописывает настоящий адрес в конец, поэтому подставной стоит первым.
    assert through_proxy("10.189.93.6, 178.223.88.25").status_code == 403


def test_request_without_client_address_closes_access(through_proxy):
    # Адрес прокси входит в разрешённую подсеть, но посетителем он не считается:
    # перестань прокси передавать заголовок, доска закроется, а не откроется.
    assert through_proxy().status_code == 403


@pytest.mark.parametrize("value", ["10.189.0.0/33", "not-a-network", "10.189.0.0/16,oops"])
def test_broken_setting_stops_the_start(db_path, notifier, monkeypatch, value):
    monkeypatch.setenv("TODO_ALLOWED_NETWORKS", value)
    with pytest.raises(ValueError):
        create_app(db_path, notifier=notifier)


def test_trusting_every_proxy_stops_the_start(db_path, notifier, monkeypatch):
    # Иначе посетитель сам назовёт себе разрешённый адрес.
    monkeypatch.setenv("TODO_ALLOWED_NETWORKS", NETWORKS)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    with pytest.raises(ValueError):
        create_app(db_path, notifier=notifier)


def test_spaces_and_a_single_address_are_accepted(db_path, notifier, monkeypatch):
    monkeypatch.setenv("TODO_ALLOWED_NETWORKS", " 10.189.0.0/16 , 203.0.113.7 ")
    app = create_app(db_path, notifier=notifier)
    assert TestClient(app, client=("203.0.113.7", 1)).get("/api/setup").status_code == 200
    assert TestClient(app, client=("203.0.113.8", 1)).get("/api/setup").status_code == 403
