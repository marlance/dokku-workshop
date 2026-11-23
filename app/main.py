"""FastAPI signup app for the Dokku-on-Civo workshop.

Flow: attendee pastes name + SSH public key + access code -> app validates ->
claims a free VM (SKIP LOCKED) -> authorizes the key on the jump host via keyctl
-> shows a personal page with SSH config, URLs and a cheat sheet.
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import load_settings
from .db import AlreadyClaimedError, Database, NoFreeVMError, normalize_email
from .keyctl import KeyctlError, make_keyctl
from .security import CSRF_COOKIE, CSRF_FIELD, Security
from .sshkeys import KeyError_, parse_public_key

from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    db = Database(settings.database_url)
    await db.open()
    await db.init_schema()
    keyctl = make_keyctl(settings)

    # In dry-run, seed the VM table from the fake pool so the app is usable
    # immediately. In production the admin clicks "Sync" (or we sync on boot too).
    try:
        synced = await db.sync_pool(await asyncio.to_thread(keyctl.pool))
    except Exception:  # noqa: BLE001 - never block startup on a sync hiccup
        synced = 0

    app.state.settings = settings
    app.state.db = db
    app.state.keyctl = keyctl
    app.state.security = Security(
        settings.secret_key, settings.admin_user,
        settings.admin_password, settings.rate_limit_per_min,
    )
    app.state.jinja = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    app.state.synced_at_boot = synced

    # Dry-run only: load the mock registration list if none exists, so local
    # runs and tests can sign up straight away. Production lists come from /admin.
    if settings.dry_run and not await db.list_attendees():
        mock = settings.dryrun_dir / "attendees.mock.csv"
        if mock.exists():
            rows, _ = parse_attendee_lines(mock.read_text())
            await db.add_attendees(rows)

    # Background key setup: bounded concurrency, and pick up any claim that was
    # still 'preparing' when the previous process stopped.
    app.state.key_sem = asyncio.Semaphore(settings.keyctl_concurrency)
    app.state.key_tasks = set()
    for vm in await db.list_preparing():
        schedule_key_setup(app.state, vm.token)
    try:
        yield
    finally:
        # Unfinished claims stay 'preparing' in the DB and resume on next boot.
        for task in list(app.state.key_tasks):
            task.cancel()
        await asyncio.gather(*app.state.key_tasks, return_exceptions=True)
        await db.close()


app = FastAPI(title="Dokku Workshop Signup", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# background key setup
# --------------------------------------------------------------------------- #
KEY_ATTEMPTS = 3

# Deliberately loose: the registration list is the real check.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def schedule_key_setup(state, token: str) -> None:
    """Authorize a claim's SSH key on the jump host without blocking the request."""
    task = asyncio.create_task(_setup_key(state, token))
    state.key_tasks.add(task)
    task.add_done_callback(state.key_tasks.discard)


async def _setup_key(state, token: str) -> None:
    db: Database = state.db
    async with state.key_sem:
        vm = await db.get_by_token(token)
        if vm is None or vm.status != "taken" or vm.key_state != "preparing" or not vm.public_key:
            return  # released, already done, or nothing to authorize
        label = vm.label or vm.vm_user
        error: str | None = None
        for attempt in range(1, KEY_ATTEMPTS + 1):
            try:
                await asyncio.to_thread(state.keyctl.add, vm.vm_user, label, vm.public_key)
                error = None
                break
            except KeyctlError as exc:
                error = str(exc)
                if attempt < KEY_ATTEMPTS:
                    await asyncio.sleep(2 * attempt)
            except Exception as exc:  # noqa: BLE001 - record it; never lose the claim
                error = f"unexpected error: {exc.__class__.__name__}"
                break

        if error is None:
            still_theirs = await db.set_key_state(vm.vm_user, token, "ready")
        else:
            still_theirs = await db.set_key_state(vm.vm_user, token, "failed", error[:300])

        if error is None and not still_theirs:
            # The VM was released while we were adding the key. If it is free
            # now, take the key back off. (If someone else already holds it,
            # their own setup replaces the key for this label.)
            now = next((v for v in await db.list_vms() if v.vm_user == vm.vm_user), None)
            if now is not None and now.status == "free":
                try:
                    await asyncio.to_thread(state.keyctl.remove, vm.vm_user, label)
                except KeyctlError:
                    pass


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def render(request: Request, template: str, status_code: int = 200,
           set_csrf: bool = False, **ctx) -> HTMLResponse:
    s = request.app.state.settings
    env = request.app.state.jinja
    base = {"base_domain": s.base_domain, "jump_host": s.jump_host,
            "jump_public_ip": s.jump_public_ip}
    html = env.get_template(template).render(**base, **ctx)
    resp = HTMLResponse(html, status_code=status_code)
    if set_csrf:
        resp.set_cookie(
            CSRF_COOKIE, ctx["csrf"],
            httponly=True, secure=not s.dry_run, samesite="lax", max_age=3600,
        )
    return resp


def attendee_view(request: Request, vm) -> dict:
    """Context for the personal page, derived from a claimed VM row."""
    s = request.app.state.settings
    nn = vm.vm_user  # e.g. "user01"
    return {
        "user_nn": nn,
        "vm_ip": vm.vm_ip,
        "attendee": vm.attendee,
        "email": vm.email,
        "token": vm.token,
        "key_state": vm.access,
        "key_error": vm.key_error,
        "app_url": f"http://myapp-{nn}.{s.base_domain}",
        "app_k8s_url": f"http://myapp-k8s-{nn}.{s.base_domain}",
    }


# --------------------------------------------------------------------------- #
# public routes
# --------------------------------------------------------------------------- #
@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
async def signup_form(request: Request):
    sec: Security = request.app.state.security
    counts = await request.app.state.db.counts()
    csrf = sec.issue_csrf()
    return render(request, "signup.html", set_csrf=True, csrf=csrf,
                  free=counts["free"], error=None, email="", public_key="")


@app.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request,
    email: str = Form(""),
    public_key: str = Form(""),
    access_code: str = Form(""),
    csrf_token: str = Form("", alias="_csrf"),
):
    app_state = request.app.state
    sec: Security = app_state.security
    db: Database = app_state.db
    settings = app_state.settings

    sec.check_rate(request)
    sec.verify_csrf(request.cookies.get(CSRF_COOKIE), csrf_token)

    def fail(msg: str, code: int = 400):
        new_csrf = sec.issue_csrf()
        return render(request, "signup.html", status_code=code, set_csrf=True,
                      csrf=new_csrf, error=msg, email=email, public_key=public_key,
                      free=None)

    def to_page(vm):
        return RedirectResponse(f"/me/{vm.token}", status_code=status.HTTP_303_SEE_OTHER)

    different_key = ("A VM is already assigned to this email with a different SSH key. "
                     "Ask the instructor to release it if you need to change keys.")

    if not secrets_equal(access_code.strip(), settings.workshop_code):
        return fail("That access code is not right. Check the slide.")

    email_n = normalize_email(email)[:254]
    if not EMAIL_RE.match(email_n):
        return fail("Please enter the email address you registered with.")

    # Only registered attendees can claim a VM.
    registered = await db.get_attendee(email_n)
    if registered is None:
        return fail("That email isn't on the registration list. Use the address you "
                    "registered with, or ask the instructor.", code=403)

    try:
        key = parse_public_key(public_key)
    except KeyError_ as exc:
        return fail(str(exc))

    # One VM per email. Same email + same key -> their existing page. A
    # different key is refused rather than swapped in, so knowing someone's
    # email is not enough to take over their VM.
    mine = await db.get_by_email(email_n)
    if mine is not None:
        return to_page(mine) if mine.key_fingerprint == key.fingerprint else fail(different_key, 409)

    # A key can't belong to two attendees either.
    if await db.get_by_fingerprint(key.fingerprint) is not None:
        return fail("That SSH key is already registered to another attendee. "
                    "Generate a key of your own and paste that one.", code=409)

    # Claim a VM now; authorize the key in the background. The jump-host call
    # takes seconds, and the attendee doesn't need to wait on the form for it.
    name = (registered.get("name") or "").strip() or email_n.split("@")[0]
    try:
        vm = await db.claim_free_vm(name[:80], key.fingerprint, key.normalized, email_n)
    except (NoFreeVMError, AlreadyClaimedError) as exc:
        # Either the pool is empty, or this same email/key won a VM through a
        # request that raced this one (the unique index let only one commit).
        mine = await db.get_by_email(email_n)
        if mine is not None:
            return to_page(mine) if mine.key_fingerprint == key.fingerprint else fail(different_key, 409)
        if isinstance(exc, AlreadyClaimedError):
            return fail("That SSH key is already registered to another attendee.", code=409)
        return fail("All VMs are taken — please ask the instructor.", code=409)
    schedule_key_setup(app_state, vm.token)
    return to_page(vm)


@app.get("/me/{token}", response_class=HTMLResponse)
async def personal_page(request: Request, token: str):
    vm = await request.app.state.db.get_by_token(token)
    if vm is None:
        return render(request, "message.html", status_code=404,
                      title="Not found",
                      body="That link is not valid. Sign up again from the home page.")
    view = attendee_view(request, vm)
    if view["key_state"] != "failed":
        return render(request, "me.html", **view)
    # Only the failed page has a form (Try again), so only it needs a token.
    sec: Security = request.app.state.security
    csrf = sec.issue_csrf()
    return render(request, "me.html", set_csrf=True, csrf=csrf, **view)


@app.get("/me/{token}/status")
async def personal_status(request: Request, token: str):
    """Small JSON view of key setup, for the page's auto-refresh and for tests."""
    vm = await request.app.state.db.get_by_token(token)
    if vm is None:
        return JSONResponse({"state": "unknown"}, status_code=404)
    return JSONResponse({"state": vm.access, "vm_user": vm.vm_user})


@app.post("/me/{token}/retry")
async def personal_retry(request: Request, token: str,
                         csrf_token: str = Form("", alias="_csrf")):
    sec: Security = request.app.state.security
    sec.verify_csrf(request.cookies.get(CSRF_COOKIE), csrf_token)
    vm = await request.app.state.db.mark_retry(token)
    if vm is not None:
        schedule_key_setup(request.app.state, token)
    return RedirectResponse(f"/me/{token}", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- #
# admin routes (HTTP basic auth)
# --------------------------------------------------------------------------- #
@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    db: Database = request.app.state.db
    return await _admin_render(request)


@app.post("/admin/sync", response_class=HTMLResponse)
async def admin_sync(request: Request, csrf_token: str = Form("", alias="_csrf")):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    sec.verify_csrf(request.cookies.get(CSRF_COOKIE), csrf_token)
    n = await request.app.state.db.sync_pool(
        await asyncio.to_thread(request.app.state.keyctl.pool))
    return await _admin_render(request, notice=f"Synced {n} VMs from the pool.")


def parse_attendee_lines(text: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Parse pasted registrations: one per line, "email" or "email, name" (CSV
    with either column order works, as does a header row). Returns the rows
    and the lines that had no usable email."""
    rows: dict[str, str | None] = {}
    bad: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip().strip('"') for p in re.split(r"[,;\t]", line)]
        email = next((normalize_email(p) for p in parts if EMAIL_RE.match(normalize_email(p))), None)
        if email is None:
            header = any(p.lower() in ("email", "e-mail", "email address", "mail") for p in parts)
            if not header:                       # a header row is skipped silently
                bad.append(line[:60])
            continue
        name = next((p for p in parts if p and normalize_email(p) != email), None)
        rows[email[:254]] = name[:80] if name else None
    return list(rows.items()), bad


@app.post("/admin/attendees", response_class=HTMLResponse)
async def admin_attendees(request: Request, csrf_token: str = Form("", alias="_csrf"),
                          attendees: str = Form(""), replace: str = Form("")):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    sec.verify_csrf(request.cookies.get(CSRF_COOKIE), csrf_token)
    rows, bad = parse_attendee_lines(attendees)
    if not rows:
        return await _admin_render(request, notice="No email addresses found in what you pasted.")
    n = await request.app.state.db.add_attendees(rows, replace=bool(replace))
    msg = f"{'Replaced the list with' if replace else 'Added or updated'} {n} registered attendees."
    if bad:
        msg += f" Skipped {len(bad)} line(s) with no email: {'; '.join(bad[:3])}"
    return await _admin_render(request, notice=msg)


@app.post("/admin/release/{vm_user}", response_class=HTMLResponse)
async def admin_release(request: Request, vm_user: str, csrf_token: str = Form("", alias="_csrf")):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    sec.verify_csrf(request.cookies.get(CSRF_COOKIE), csrf_token)
    try:
        await asyncio.to_thread(request.app.state.keyctl.remove, vm_user, vm_user)
    except KeyctlError as exc:
        return await _admin_render(request, notice=f"keyctl remove warned: {exc}")
    await request.app.state.db.release_vm(vm_user)
    return await _admin_render(request, notice=f"Released {vm_user}.")


@app.get("/admin/reconcile", response_class=HTMLResponse)
async def admin_reconcile(request: Request):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    db: Database = request.app.state.db
    try:
        authed = {a["vm_user"] for a in await asyncio.to_thread(request.app.state.keyctl.list)}
    except KeyctlError as exc:
        return await _admin_render(request, notice=f"Could not list keyctl authorizations: {exc}")
    vms = await db.list_vms()
    taken = {v.vm_user for v in vms if v.status == "taken"}
    # A claim whose key is still being set up (or failed) is expected to be
    # missing on the jump host; only 'ready' claims count as drift.
    ready = {v.vm_user for v in vms if v.status == "taken" and v.access == "ready"}
    mismatches = {
        "authorized_but_free": sorted(authed - taken),
        "taken_but_unauthorized": sorted(ready - authed),
    }
    return await _admin_render(request, mismatches=mismatches)


@app.get("/admin/export.csv")
async def admin_export(request: Request):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    vms = await request.app.state.db.list_vms()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["vm_user", "vm_ip", "status", "access", "attendee", "email", "key_fingerprint", "assigned_at"])
    for v in vms:
        w.writerow([v.vm_user, v.vm_ip, v.status,
                    v.access if v.status == "taken" else "", v.attendee or "",
                    v.email or "", v.key_fingerprint or "", v.assigned_at or ""])
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=assignments.csv"},
    )


async def _admin_render(request: Request, notice=None, mismatches=None) -> HTMLResponse:
    sec: Security = request.app.state.security
    db: Database = request.app.state.db
    vms = await db.list_vms()
    counts = await db.counts()
    attendees = await db.list_attendees()
    csrf = sec.issue_csrf()
    return render(request, "admin.html", set_csrf=True, csrf=csrf,
                  vms=vms, counts=counts, mismatches=mismatches, notice=notice,
                  attendees=attendees,
                  unclaimed=[a for a in attendees if not a["vm_user"]])


def secrets_equal(a: str, b: str) -> bool:
    import secrets as _s
    return _s.compare_digest(a, b)
