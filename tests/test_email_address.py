import re
from datetime import timedelta

from app import db

from .helpers import deliver, login, make_task, make_user
from .test_mailer import FakeMailer, mail, mail_app, mama  # noqa: F401 — фикстуры


def request_email(client, address):
    return client.put("/api/me/email", json={"email": address})


def confirm_link(mail):
    return re.search(r"https://b\.example(/api/email/confirm\?token=[\w-]+)", mail.sent[-1]["text"]).group(1)


def test_confirmation_flow(mail_app, mama, mail):
    assert request_email(mama, "mama@example.org").status_code == 202
    assert mail.sent[-1]["to"] == "mama@example.org"
    me = mama.get("/api/me").json()
    assert me["email"] is None and me["email_pending"] == "mama@example.org"
    r = mama.get(confirm_link(mail), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?email=confirmed"
    me = mama.get("/api/me").json()
    assert me["email"] == "mama@example.org" and me["email_pending"] is None and me["email_notify"] is True


def test_unconfirmed_address_gets_no_notifications(mail_app, mama, mail):
    petya = make_user(mama, "petya")
    request_email(login(mail_app, "petya", "pass1234"), "petya@example.org")
    mail.sent.clear()
    make_task(mama, assignee_id=petya["id"])
    deliver(mail_app)
    assert mail.sent == []


def test_link_expires_after_24h(mail_app, mama, mail, clock):
    request_email(mama, "mama@example.org")
    clock.now += timedelta(hours=24, seconds=1)
    r = mama.get(confirm_link(mail), follow_redirects=False)
    assert r.headers["location"] == "/?email=invalid"
    assert mama.get("/api/me").json()["email"] is None


def test_token_single_use(mail_app, mama, mail):
    request_email(mama, "mama@example.org")
    link = confirm_link(mail)
    mama.get(link)
    assert mama.get(link, follow_redirects=False).headers["location"] == "/?email=invalid"


def test_link_works_without_signing_in(mail_app, mama, mail):
    from fastapi.testclient import TestClient
    request_email(mama, "mama@example.org")
    r = TestClient(mail_app).get(confirm_link(mail), follow_redirects=False)
    assert r.headers["location"] == "/?email=confirmed"


def test_change_keeps_old_until_confirmed(mail_app, mama, mail, clock):
    request_email(mama, "old@example.org")
    mama.get(confirm_link(mail))
    clock.now += timedelta(minutes=2)
    request_email(mama, "new@example.org")
    me = mama.get("/api/me").json()
    assert me["email"] == "old@example.org" and me["email_pending"] == "new@example.org"


def test_rate_limits(mail_app, mama, mail, clock):
    assert request_email(mama, "a@example.org").status_code == 202
    assert request_email(mama, "a@example.org").status_code == 429
    for _ in range(4):
        clock.now += timedelta(minutes=2)
        assert request_email(mama, "a@example.org").status_code == 202
    clock.now += timedelta(minutes=2)
    assert request_email(mama, "a@example.org").status_code == 429
    clock.now += timedelta(hours=24)
    assert request_email(mama, "a@example.org").status_code == 202


def test_bad_addresses_rejected(mail_app, mama):
    for bad in ("", "no-at", "a@b", "a b@example.org", "a@example.org\nBcc: x@y.z", "x" * 250 + "@ex.org"):
        assert request_email(mama, bad).status_code == 422, bad


def test_email_hidden_from_others(mail_app, mama, mail, db_path):
    make_user(mama, "petya")
    petya = login(mail_app, "petya", "pass1234")
    request_email(petya, "petya@example.org")
    petya.get(confirm_link(mail))
    for row in mama.get("/api/users").json():
        assert "email" not in row and "email_pending" not in row
    assert all("petya@example.org" not in str(row) for row in petya.get("/api/users").json())


def test_admin_cannot_set_email_for_others(mail_app, mama, db_path):
    petya = make_user(mama, "petya")
    mama.patch(f"/api/users/{petya['id']}", json={"email": "evil@example.org"})
    conn = db.connect(db_path)
    assert conn.execute("SELECT email, email_pending FROM users WHERE id = ?", (petya["id"],)).fetchone()[:] == (None, None)


def test_disabled_or_removed_email_drops_queue(mail_app, mama, mail, db_path):
    petya = make_user(mama, "petya")
    p = login(mail_app, "petya", "pass1234")
    request_email(p, "petya@example.org")
    p.get(confirm_link(mail))
    mail.sent.clear()
    make_task(mama, assignee_id=petya["id"])
    assert p.patch("/api/me/notifications", json={"email_notify": False}).json()["email_notify"] is False
    deliver(mail_app)
    assert mail.sent == []
    p.patch("/api/me/notifications", json={"email_notify": True})
    make_task(mama, title="Вторая", assignee_id=petya["id"])
    assert p.delete("/api/me/email").status_code == 204
    deliver(mail_app)
    assert mail.sent == []
    assert db.connect(db_path).execute("SELECT COUNT(*) FROM outbox WHERE channel = 'email'").fetchone()[0] == 0


def test_email_off_without_smtp(app, admin):
    assert admin.get("/api/config").json()["email"] is False
    assert request_email(admin, "mama@example.org").status_code == 400


def test_digest_toggle_saved(mail_app, mama):
    assert mama.get("/api/me").json()["email_digest"] is False
    assert mama.patch("/api/me/notifications", json={"email_digest": True}).json()["email_digest"] is True
