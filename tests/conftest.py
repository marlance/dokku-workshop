"""Shared test helpers.

Tests that need Postgres are skipped automatically when it isn't reachable, so
`pytest` still runs the pure unit tests on a bare laptop. To run everything:

    docker compose up -d db
    DATABASE_URL=postgresql://workshop:workshop@localhost:5432/workshop pytest
"""
from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://workshop:workshop@localhost:5432/workshop"
)


def _pg_available() -> bool:
    try:
        import psycopg
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _pg_available(), reason="Postgres not reachable")


@pytest.fixture
def ssh_keypair():
    """Generate a throwaway ed25519 key; yield its public key line."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "id")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "tester@laptop", "-f", path],
            check=True, capture_output=True,
        )
        with open(path + ".pub") as fh:
            yield fh.read().strip()


def seed_pool(n: int) -> None:
    """Reset the vms table and seed n free VMs. Requires Postgres."""
    import psycopg
    from app.db import SCHEMA_PATH

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text())
        conn.execute("TRUNCATE vms")
        for i in range(1, n + 1):
            conn.execute(
                "INSERT INTO vms (vm_user, vm_ip, status) VALUES (%s, %s, 'free')",
                (f"user{i:02d}", f"10.0.0.{10 + i}"),
            )
