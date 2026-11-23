"""The core safety property: two simultaneous signups never get the same VM.

Drives claim_free_vm (SELECT ... FOR UPDATE SKIP LOCKED) with more concurrent
claimers than there are VMs, and asserts every claim is distinct and the pool is
exactly exhausted.
"""
from __future__ import annotations

import asyncio

from conftest import DSN, requires_pg, seed_pool

from app.db import AlreadyClaimedError, Database, NoFreeVMError


@requires_pg
def test_concurrent_claims_never_collide():
    n_vms = 10
    n_claimers = 25
    seed_pool(n_vms)

    async def run():
        db = Database(DSN)
        await db.open()
        try:
            async def claim(i: int):
                try:
                    vm = await db.claim_free_vm(f"attendee-{i}", f"fp-{i}")
                    return vm.vm_user
                except NoFreeVMError:
                    return None

            results = await asyncio.gather(*(claim(i) for i in range(n_claimers)))
        finally:
            await db.close()
        return results

    results = asyncio.run(run())
    claimed = [r for r in results if r is not None]

    assert len(claimed) == n_vms, "exactly the pool size should be handed out"
    assert len(set(claimed)) == n_vms, "every attendee got a DISTINCT VM"
    assert results.count(None) == n_claimers - n_vms, "the rest correctly got none"


@requires_pg
def test_same_fingerprint_reuses_assignment():
    """The app returns the existing VM for a repeat key; the unique index also
    guarantees the same fingerprint can't occupy two VMs."""
    seed_pool(3)

    async def run():
        db = Database(DSN)
        await db.open()
        try:
            vm1 = await db.claim_free_vm("alice", "fp-shared")
            found = await db.get_by_fingerprint("fp-shared")
            return vm1.vm_user, found.vm_user
        finally:
            await db.close()

    first, looked_up = asyncio.run(run())
    assert first == looked_up


@requires_pg
def test_one_email_racing_itself_gets_exactly_one_vm():
    """Many simultaneous signups for the SAME email (different keys) must end
    with one VM taken. The unique index on vms.email is what guarantees it."""
    seed_pool(10)

    async def run():
        db = Database(DSN)
        await db.open()
        try:
            async def claim(i: int):
                try:
                    vm = await db.claim_free_vm("ada", f"fp-race-{i}", None, "ada@example.com")
                    return vm.vm_user
                except AlreadyClaimedError:
                    return "dup"
                except NoFreeVMError:
                    return "none"

            results = await asyncio.gather(*(claim(i) for i in range(20)))
            taken = [v for v in await db.list_vms() if v.status == "taken"]
        finally:
            await db.close()
        return results, taken

    results, taken = asyncio.run(run())
    winners = [r for r in results if r not in ("dup", "none")]
    assert len(winners) == 1, results
    assert len(taken) == 1 and taken[0].email == "ada@example.com"
    assert results.count("dup") + results.count("none") == 19
