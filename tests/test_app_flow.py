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
