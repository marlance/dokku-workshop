"""Postgres access via psycopg3 async pool.

All state lives here. The one piece that matters for correctness is
:func:`claim_free_vm`, which uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so two
attendees signing up at the same instant can never be handed the same VM.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class NoFreeVMError(RuntimeError):
    """No free VM left in the pool."""


class AlreadyClaimedError(RuntimeError):
    """This email or SSH key already holds a VM (lost a race with itself)."""


def normalize_email(raw: str) -> str:
    """Canonical form used everywhere an email is stored or compared."""
    return (raw or "").strip().lower()


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
    key_state: str | None = None   # 'preparing' | 'ready' | 'failed' | None (= ready)
    key_error: str | None = None
    public_key: str | None = None
    email: str | None = None

    @property
    def access(self) -> str:
        """Effective key state for a taken VM (legacy NULL counts as ready)."""
        return self.key_state or "ready"


def _vm(row: dict) -> VM:
    return VM(**{k: row.get(k) for k in VM.__annotations__})


class Database:
    def __init__(self, dsn: str):
        # open=False so construction never blocks; we open() in the app lifespan.
        # A whole room signs up in the same few seconds. Keep several
        # connections warm so that burst doesn't start by opening them, and
        # allow enough that requests queue only briefly. `check` tests a
        # connection before handing it out, so one the server dropped while
        # idle (overnight, or a database restart) is replaced instead of
        # failing the next request.
        self.pool = AsyncConnectionPool(
            dsn, open=False,
            min_size=int(os.environ.get("DB_POOL_MIN", "5")),
            max_size=int(os.environ.get("DB_POOL_MAX", "20")),
            check=AsyncConnectionPool.check_connection,
            kwargs={"row_factory": dict_row},
        )

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

    async def get_by_email(self, email: str) -> VM | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM vms WHERE email = %s", (email,))
            row = await cur.fetchone()
        return _vm(row) if row else None

    # -- registered attendees ------------------------------------------------ #
    async def get_attendee(self, email: str) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM attendees WHERE email = %s", (email,))
            return await cur.fetchone()

    async def list_attendees(self) -> list[dict]:
        """Registered attendees with the VM each holds, if any."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT a.email, a.name, v.vm_user
                FROM attendees a LEFT JOIN vms v ON v.email = a.email
                ORDER BY a.email
                """
            )
            return await cur.fetchall()

    async def add_attendees(self, rows: list[tuple[str, str | None]],
                            replace: bool = False) -> int:
        """Upsert (email, name) pairs. With replace, the list becomes exactly
        these rows. VMs already claimed are never touched either way."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                if replace:
                    await conn.execute("DELETE FROM attendees")
                for email, name in rows:
                    await conn.execute(
                        """
                        INSERT INTO attendees (email, name) VALUES (%s, %s)
                        ON CONFLICT (email) DO UPDATE
                          SET name = COALESCE(EXCLUDED.name, attendees.name)
                        """,
                        (email, name),
                    )
        return len(rows)

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
    async def claim_free_vm(self, attendee: str, fingerprint: str,
                            public_key: str | None = None,
                            email: str | None = None) -> VM:
        """Atomically claim one free VM for this attendee.

        FOR UPDATE SKIP LOCKED means concurrent callers skip rows already locked
        by another in-flight transaction, so no two attendees get the same VM.

        With ``public_key`` the row starts in key_state 'preparing': the key is
        authorized on the jump host afterwards by the background worker.
        """
        key_state = "preparing" if public_key else "ready"
        try:
            return await self._claim(attendee, fingerprint, public_key, email, key_state)
        except pg_errors.UniqueViolation as exc:
            # idx_vms_email / idx_vms_fingerprint: the same email or key was
            # claimed by a concurrent request. The transaction rolled back, so
            # the VM this request picked is free again.
            raise AlreadyClaimedError() from exc

    async def _claim(self, attendee, fingerprint, public_key, email, key_state) -> VM:
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
                        label = %s, token = %s, assigned_at = now(),
                        key_state = %s, key_error = NULL, public_key = %s, email = %s
                    WHERE vm_user = %s
                    """,
                    (attendee, fingerprint, vm_user, token, key_state, public_key,
                     email, vm_user),
                )
                cur = await conn.execute("SELECT * FROM vms WHERE vm_user = %s", (vm_user,))
                return _vm(await cur.fetchone())

    async def release_vm(self, vm_user: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                UPDATE vms
                SET status = 'free', attendee = NULL, key_fingerprint = NULL,
                    label = NULL, token = NULL, assigned_at = NULL,
                    key_state = NULL, key_error = NULL, public_key = NULL, email = NULL
                WHERE vm_user = %s
                """,
                (vm_user,),
            )

    # -- background key setup ------------------------------------------------ #
    async def set_key_state(self, vm_user: str, token: str, state: str,
                            error: str | None = None) -> bool:
        """Record the outcome of key setup for one claim.

        Matches on the claim's token as well as the VM, so a result that
        arrives after the VM was released (or handed to someone else) is
        dropped instead of overwriting the new owner's state. Returns whether
        the claim still existed.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE vms SET key_state = %s, key_error = %s
                WHERE vm_user = %s AND token = %s AND status = 'taken'
                RETURNING vm_user
                """,
                (state, error, vm_user, token),
            )
            return await cur.fetchone() is not None

    async def list_preparing(self) -> list[VM]:
        """Claims whose key setup has not finished (e.g. app restarted mid-way)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM vms WHERE status = 'taken' AND key_state = 'preparing' "
                "ORDER BY assigned_at"
            )
            rows = await cur.fetchall()
        return [_vm(r) for r in rows]

    async def mark_retry(self, token: str) -> VM | None:
        """Move a failed claim back to 'preparing'. None if it wasn't failed."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE vms SET key_state = 'preparing', key_error = NULL
                WHERE token = %s AND status = 'taken' AND key_state = 'failed'
                RETURNING *
                """,
                (token,),
            )
            row = await cur.fetchone()
        return _vm(row) if row else None
