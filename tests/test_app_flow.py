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


def _signup(client, name="Ada", key=None):
    token = _csrf(client.get("/").text)
    r = client.post("/signup", follow_redirects=False, data={
        "name": name, "access_code": "dokku2026",
        "public_key": key or client.pubkey, "_csrf": token,
    })
    assert r.status_code == 303, r.text[:300]
    return r.headers["location"]


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
        "name": "Ada", "access_code": "dokku2026",
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
    data = {"name": "Ada", "access_code": "dokku2026",
            "public_key": client.pubkey, "_csrf": token}
    first = client.post("/signup", follow_redirects=False, data=data)
    second = client.post("/signup", follow_redirects=False, data=data)
    assert first.headers["location"] == second.headers["location"]


@requires_pg
def test_private_key_rejected(client):
    token = _csrf(client.get("/").text)
    r = client.post("/signup", follow_redirects=False, data={
        "name": "Ada", "access_code": "dokku2026",
        "public_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nxxx\n-----END OPENSSH PRIVATE KEY-----",
        "_csrf": token,
    })
    assert r.status_code == 400
    assert "PRIVATE" in r.text.upper()


@requires_pg
def test_wrong_access_code_rejected(client):
    token = _csrf(client.get("/").text)
    r = client.post("/signup", follow_redirects=False, data={
        "name": "Ada", "access_code": "wrong",
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
