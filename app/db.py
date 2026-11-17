"""Postgres access via psycopg3 async pool.

All state lives here. The one piece that matters for correctness is
:func:`claim_free_vm`, which uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so two
attendees signing up at the same instant can never be handed the same VM.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class NoFreeVMError(RuntimeError):
    """No free VM left in the pool."""


@dataclass
class VM:
    vm_user: str
    vm_ip: str
    status: str
    attendee: str | None
    key_fingerprint: str | None
    label: str | None
    token: str | None
    assigned_at: object | None


def _vm(row: dict) -> VM:
    return VM(**{k: row.get(k) for k in VM.__annotations__})


class Database:
    def __init__(self, dsn: str):
        # open=False so construction never blocks; we open() in the app lifespan.
        self.pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=10,
                                        kwargs={"row_factory": dict_row})

    async def open(self) -> None:
        await self.pool.open()
        await self.pool.wait()

    async def close(self) -> None:
        await self.pool.close()

    async def init_schema(self) -> None:
        sql = SCHEMA_PATH.read_text()
        async with self.pool.connection() as conn:
            await conn.execute(sql)

    # -- pool sync ---------------------------------------------------------- #
    async def sync_pool(self, entries: list[dict]) -> int:
        """Upsert VMs from `keyctl pool`. Never touches existing assignments."""
        n = 0
        async with self.pool.connection() as conn:
            for e in entries:
                user, ip = e.get("user"), e.get("ip")
                if not user or not ip:
                    continue
                await conn.execute(
                    """
                    INSERT INTO vms (vm_user, vm_ip, status)
                    VALUES (%s, %s, 'free')
                    ON CONFLICT (vm_user) DO UPDATE SET vm_ip = EXCLUDED.vm_ip
                    """,
                    (user, ip),
                )
                n += 1
        return n

    # -- lookups ------------------------------------------------------------ #
    async def get_by_fingerprint(self, fingerprint: str) -> VM | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM vms WHERE key_fingerprint = %s", (fingerprint,)
            )
            row = await cur.fetchone()
        return _vm(row) if row else None

    async def get_by_token(self, token: str) -> VM | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM vms WHERE token = %s", (token,))
            row = await cur.fetchone()
        return _vm(row) if row else None

    async def list_vms(self) -> list[VM]:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM vms ORDER BY vm_user")
            rows = await cur.fetchall()
        return [_vm(r) for r in rows]

    async def counts(self) -> dict[str, int]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT status, count(*) AS n FROM vms GROUP BY status"
            )
            rows = await cur.fetchall()
        out = {"free": 0, "taken": 0}
        for r in rows:
            out[r["status"]] = r["n"]
        return out

    # -- the important one -------------------------------------------------- #
    async def claim_free_vm(self, attendee: str, fingerprint: str) -> VM:
        """Atomically claim one free VM for this attendee.

        FOR UPDATE SKIP LOCKED means concurrent callers skip rows already locked
        by another in-flight transaction, so no two attendees get the same VM.
        """
        token = secrets.token_urlsafe(16)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """
                    SELECT vm_user, vm_ip FROM vms
                    WHERE status = 'free'
                    ORDER BY vm_user
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
                row = await cur.fetchone()
                if row is None:
                    raise NoFreeVMError()
                vm_user = row["vm_user"]
                await conn.execute(
                    """
                    UPDATE vms
                    SET status = 'taken', attendee = %s, key_fingerprint = %s,
                        label = %s, token = %s, assigned_at = now()
                    WHERE vm_user = %s
                    """,
                    (attendee, fingerprint, vm_user, token, vm_user),
                )
                cur = await conn.execute("SELECT * FROM vms WHERE vm_user = %s", (vm_user,))
                return _vm(await cur.fetchone())

    async def release_vm(self, vm_user: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                UPDATE vms
                SET status = 'free', attendee = NULL, key_fingerprint = NULL,
                    label = NULL, token = NULL, assigned_at = NULL
                WHERE vm_user = %s
                """,
                (vm_user,),
            )
