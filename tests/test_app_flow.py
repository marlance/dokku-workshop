"""Full signup flow through the real ASGI app in DRY_RUN mode.

Covers the Phase 1 acceptance checks that need the web layer:
 - a good key claims a VM and lands on the personal page
 - the same key twice returns the same assignment
 - a pasted private key is rejected with a clear message
 - a wrong access code is rejected
 - /admin requires basic auth
"""
from __future__ import annotations

import json
import os
import re

import pytest

from conftest import DSN, requires_pg


def _reset_db():
    import psycopg
    from app.db import SCHEMA_PATH
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text())
        conn.execute("TRUNCATE vms")
        conn.execute("TRUNCATE attendees")
        conn.execute("INSERT INTO attendees (email, name) VALUES "
                     "('ada@example.com', 'Ada Lovelace'), ('grace@example.com', 'Grace Hopper'),"
                     "('linus@example.com', NULL)")


def _csrf(html: str) -> str:
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    assert m, "no CSRF token in form"
    return m.group(1)


def _wait_state(client, loc: str, want=("ready", "failed"), timeout: float = 8.0) -> str:
    """Poll the status endpoint until key setup settles."""
    import time
    deadline = time.monotonic() + timeout
    state = "?"
    while time.monotonic() < deadline:
        state = client.get(loc + "/status").json()["state"]
        if state in want:
            return state
        time.sleep(0.05)
    return state


def _post_signup(client, email="ada@example.com", key=None, code="dokku2026"):
    token = _csrf(client.get("/").text)
    return client.post("/signup", follow_redirects=False, data={
        "email": email, "access_code": code,
        "public_key": key or client.pubkey, "_csrf": token,
    })


def _signup(client, email="ada@example.com", key=None):
    r = _post_signup(client, email, key)
    assert r.status_code == 303, r.text[:300]
    return r.headers["location"]


def _new_key(tmp_path, name="k2") -> str:
    import subprocess
    path = tmp_path / name
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)],
                   check=True, capture_output=True)
    return (tmp_path / f"{name}.pub").read_text().strip()


@pytest.fixture
def client(tmp_path, ssh_keypair, monkeypatch):
    dry = tmp_path / "dry"
    dry.mkdir()
    (dry / "vm_pool.json").write_text(json.dumps(
        [{"user": "user01", "ip": "10.0.0.11"},
         {"user": "user02", "ip": "10.0.0.12"},
         {"user": "user03", "ip": "10.0.0.13"}]
    ))
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("DRYRUN_DIR", str(dry))
    monkeypatch.setenv("DRYRUN_WRITE_DIR", str(dry))
    monkeypatch.setenv("WORKSHOP_CODE", "dokku2026")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("BASE_DOMAIN", "workshop.example.com")

    _reset_db()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        c.pubkey = ssh_keypair  # stash for tests
        yield c


@requires_pg
def test_good_signup_lands_on_personal_page(client):
    r = client.get("/")
    assert r.status_code == 200
    token = _csrf(r.text)

    r = client.post("/signup", follow_redirects=False, data={
        "email": "ada@example.com", "access_code": "dokku2026",
        "public_key": client.pubkey, "_csrf": token,
    })
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/me/")

    # Key setup runs in the background; the page is usable once it is ready.
    assert _wait_state(client, loc) == "ready"
    page = client.get(loc)
    assert page.status_code == 200
    assert "user0" in page.text
    assert "ssh dokku@dokku-user0" in page.text


@requires_pg
def test_same_key_returns_same_assignment(client):
    token = _csrf(client.get("/").text)
    data = {"email": "ada@example.com", "access_code": "dokku2026",
            "public_key": client.pubkey, "_csrf": token}
    first = client.post("/signup", follow_redirects=False, data=data)
    second = client.post("/signup", follow_redirects=False, data=data)
    assert first.headers["location"] == second.headers["location"]


@requires_pg
def test_private_key_rejected(client):
    token = _csrf(client.get("/").text)
    r = client.post("/signup", follow_redirects=False, data={
        "email": "ada@example.com", "access_code": "dokku2026",
        "public_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nxxx\n-----END OPENSSH PRIVATE KEY-----",
        "_csrf": token,
    })
    assert r.status_code == 400
    assert "PRIVATE" in r.text.upper()


@requires_pg
def test_wrong_access_code_rejected(client):
    token = _csrf(client.get("/").text)
    r = client.post("/signup", follow_redirects=False, data={
        "email": "ada@example.com", "access_code": "wrong",
        "public_key": client.pubkey, "_csrf": token,
    })
    assert r.status_code == 400
    assert "access code" in r.text.lower()


@requires_pg
def test_admin_requires_auth(client):
    assert client.get("/admin").status_code == 401
    ok = client.get("/admin", auth=("admin", "pw"))
    assert ok.status_code == 200
    assert "Workshop admin" in ok.text


# --------------------------------------------------------------------------- #
# background key setup
# --------------------------------------------------------------------------- #
@requires_pg
def test_signup_returns_before_key_setup_finishes(client):
    """The form must not wait for the jump host; the page shows a waiting state."""
    import threading
    gate = threading.Event()
    real_add = client.app.state.keyctl.add

    def slow_add(*a, **kw):
        gate.wait(5)
        return real_add(*a, **kw)

    client.app.state.keyctl.add = slow_add
    try:
        loc = _signup(client)                       # returns while add is blocked
        assert client.get(loc + "/status").json()["state"] == "preparing"
        page = client.get(loc)
        assert "Setting up your SSH access" in page.text
        assert 'http-equiv="refresh"' in page.text
        assert "ssh dokku@dokku-user0" not in page.text   # no instructions yet
    finally:
        gate.set()
    assert _wait_state(client, loc) == "ready"
    assert "ssh dokku@dokku-user0" in client.get(loc).text


@requires_pg
def test_key_setup_failure_keeps_vm_and_offers_retry(client, monkeypatch):
    import app.main as main
    from app.keyctl import KeyctlError
    monkeypatch.setattr(main, "KEY_ATTEMPTS", 1)
    real_add = client.app.state.keyctl.add

    def boom(*a, **kw):
        raise KeyctlError("jump host unreachable")

    client.app.state.keyctl.add = boom
    loc = _signup(client)
    assert _wait_state(client, loc) == "failed"
    page = client.get(loc)
    assert "jump host unreachable" in page.text
    assert "Try again" in page.text
    # The VM is still theirs: nobody else can be handed it.
    admin = client.get("/admin", auth=("admin", "pw")).text
    assert "1 taken" in admin and "failed" in admin

    client.app.state.keyctl.add = real_add
    page = client.get(loc)          # fresh token (the admin page reset the cookie)
    r = client.post(loc + "/retry", follow_redirects=False,
                    data={"_csrf": _csrf(page.text)})
    assert r.status_code == 303
    assert _wait_state(client, loc) == "ready"


@requires_pg
def test_preparing_claims_resume_after_restart(tmp_path, ssh_keypair, monkeypatch):
    """A claim left 'preparing' by a stopped process is finished on next boot."""
    import json as _json
    import psycopg
    dry = tmp_path / "dry"
    dry.mkdir()
    (dry / "vm_pool.json").write_text(_json.dumps([{"user": "user01", "ip": "10.0.0.11"}]))
    for k, v in {"DRY_RUN": "1", "DATABASE_URL": DSN, "DRYRUN_DIR": str(dry),
                 "DRYRUN_WRITE_DIR": str(dry), "SECRET_KEY": "s",
                 "ADMIN_USER": "admin", "ADMIN_PASSWORD": "pw"}.items():
        monkeypatch.setenv(k, v)
    _reset_db()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO vms (vm_user, vm_ip, status, attendee, key_fingerprint, label,"
            " token, assigned_at, key_state, public_key)"
            " VALUES ('user01','10.0.0.11','taken','Ada','fp','user01','tok123', now(),"
            " 'preparing', %s)", (ssh_keypair,))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert _wait_state(c, "/me/tok123") == "ready"
        assert _json.loads((dry / "authorized.json").read_text())[0]["vm_user"] == "user01"


@requires_pg
def test_release_during_setup_does_not_leave_key_behind(client):
    import threading
    gate = threading.Event()
    real_add = client.app.state.keyctl.add

    def slow_add(*a, **kw):
        gate.wait(5)
        return real_add(*a, **kw)

    client.app.state.keyctl.add = slow_add
    loc = _signup(client)
    admin = client.get("/admin", auth=("admin", "pw"))
    r = client.post("/admin/release/user01", auth=("admin", "pw"),
                    data={"_csrf": _csrf(admin.text)})
    assert r.status_code == 200 and "Released user01" in r.text
    gate.set()                                   # the add now lands on a free VM
    import time
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and client.app.state.keyctl.list():
        time.sleep(0.05)
    assert client.app.state.keyctl.list() == []
    assert client.get(loc).status_code == 404    # their link is gone with the claim


# --------------------------------------------------------------------------- #
# registered emails: one VM each
# --------------------------------------------------------------------------- #
@requires_pg
def test_unregistered_email_is_refused(client):
    r = _post_signup(client, email="stranger@example.com")
    assert r.status_code == 403
    assert "registration list" in r.text
    assert "0 taken" in client.get("/admin", auth=("admin", "pw")).text


@requires_pg
def test_email_is_matched_case_and_space_insensitively(client):
    first = _signup(client, email="ada@example.com")
    again = _signup(client, email="  Ada@Example.COM ")
    assert first == again
    assert "1 taken" in client.get("/admin", auth=("admin", "pw")).text


@requires_pg
def test_same_email_with_a_different_key_is_refused(client, tmp_path):
    loc = _signup(client)
    assert _wait_state(client, loc) == "ready"
    r = _post_signup(client, key=_new_key(tmp_path))
    assert r.status_code == 409
    assert "different SSH key" in r.text
    admin = client.get("/admin", auth=("admin", "pw")).text
    assert "1 taken" in admin                       # no second VM
    assert len(client.app.state.keyctl.list()) == 1  # and the original key stands


@requires_pg
def test_one_key_cannot_serve_two_attendees(client):
    _signup(client, email="ada@example.com")
    r = _post_signup(client, email="grace@example.com")   # same key as Ada
    assert r.status_code == 409
    assert "already registered to another attendee" in r.text


@requires_pg
def test_name_comes_from_the_registration_list(client, tmp_path):
    loc = _signup(client, email="ada@example.com")
    assert _wait_state(client, loc) == "ready"
    assert "Ada Lovelace" in client.get(loc).text
    loc2 = _signup(client, email="linus@example.com", key=_new_key(tmp_path))  # no name on file
    assert _wait_state(client, loc2) == "ready"
    assert "linus" in client.get(loc2).text


@requires_pg
def test_admin_can_paste_and_replace_the_attendee_list(client):
    admin = client.get("/admin", auth=("admin", "pw"))
    r = client.post("/admin/attendees", auth=("admin", "pw"), data={
        "_csrf": _csrf(admin.text),
        "attendees": "email,name\nNew.Person@Example.com, New Person\nplain@example.com\nnot an email\n",
        "replace": "1",
    })
    assert r.status_code == 200
    assert "Replaced the list with 2" in r.text and "Skipped 1" in r.text
    assert "<td>new.person@example.com</td>" in r.text
    assert "<td>ada@example.com</td>" not in r.text      # (the form placeholder still mentions it)
    assert _post_signup(client, email="ada@example.com").status_code == 403   # off the list now
    assert _post_signup(client, email="new.person@example.com").status_code == 303


def test_parse_attendee_lines():
    from app.main import parse_attendee_lines
    rows, bad = parse_attendee_lines(
        "# comment\nemail,name\nA@x.io, Ann\nBob Smith;bob@x.io\n\nc@x.io\na@x.io,Ann Again\nnope\n")
    assert dict(rows) == {"a@x.io": "Ann Again", "bob@x.io": "Bob Smith", "c@x.io": None}
    assert bad == ["nope"]
