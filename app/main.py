"""FastAPI signup app for the Dokku-on-Civo workshop.

Flow: attendee pastes name + SSH public key + access code -> app validates ->
claims a free VM (SKIP LOCKED) -> authorizes the key on the jump host via keyctl
-> shows a personal page with SSH config, URLs and a cheat sheet.
"""
from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import load_settings
from .db import Database, NoFreeVMError
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
    try:
        yield
    finally:
        await db.close()


app = FastAPI(title="Dokku Workshop Signup", lifespan=lifespan)


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
        "token": vm.token,
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
                  free=counts["free"], error=None, name="", public_key="")


@app.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request,
    name: str = Form(""),
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
                      csrf=new_csrf, error=msg, name=name, public_key=public_key,
                      free=None)

    if not secrets_equal(access_code.strip(), settings.workshop_code):
        return fail("That access code is not right. Check the slide.")

    name = (name or "").strip()[:80]
    if not name:
        return fail("Please enter your name.")

    try:
        key = parse_public_key(public_key)
    except KeyError_ as exc:
        return fail(str(exc))

    # Already registered with this key -> return the same assignment.
    existing = await db.get_by_fingerprint(key.fingerprint)
    if existing is not None:
        return RedirectResponse(f"/me/{existing.token}", status_code=status.HTTP_303_SEE_OTHER)

    # Claim a VM, then authorize the key. If keyctl fails, roll the claim back.
    try:
        vm = await db.claim_free_vm(name, key.fingerprint)
    except NoFreeVMError:
        return fail("All VMs are taken — please ask the instructor.", code=409)

    label = vm.vm_user  # matches ^[a-z0-9-]{1,32}$
    try:
        # ssh to the jump host takes seconds; run it off the event loop so
        # simultaneous signups proceed in parallel instead of queueing.
        await asyncio.to_thread(app_state.keyctl.add, vm.vm_user, label, key.normalized)
    except KeyctlError as exc:
        await db.release_vm(vm.vm_user)
        return fail(f"Could not authorize your key ({exc}). Please try again or ask the instructor.",
                    code=502)

    return RedirectResponse(f"/me/{vm.token}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/me/{token}", response_class=HTMLResponse)
async def personal_page(request: Request, token: str):
    vm = await request.app.state.db.get_by_token(token)
    if vm is None:
        return render(request, "message.html", status_code=404,
                      title="Not found",
                      body="That link is not valid. Sign up again from the home page.")
    return render(request, "me.html", **attendee_view(request, vm))


# --------------------------------------------------------------------------- #
# admin routes (HTTP basic auth)
# --------------------------------------------------------------------------- #
@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    db: Database = request.app.state.db
    vms = await db.list_vms()
    counts = await db.counts()
    csrf = sec.issue_csrf()
    return render(request, "admin.html", set_csrf=True, csrf=csrf,
                  vms=vms, counts=counts, mismatches=None, notice=None)


@app.post("/admin/sync", response_class=HTMLResponse)
async def admin_sync(request: Request, csrf_token: str = Form("", alias="_csrf")):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    sec.verify_csrf(request.cookies.get(CSRF_COOKIE), csrf_token)
    n = await request.app.state.db.sync_pool(
        await asyncio.to_thread(request.app.state.keyctl.pool))
    return await _admin_render(request, notice=f"Synced {n} VMs from the pool.")


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
    taken = {v.vm_user for v in await db.list_vms() if v.status == "taken"}
    mismatches = {
        "authorized_but_free": sorted(authed - taken),
        "taken_but_unauthorized": sorted(taken - authed),
    }
    return await _admin_render(request, mismatches=mismatches)


@app.get("/admin/export.csv")
async def admin_export(request: Request):
    sec: Security = request.app.state.security
    sec.check_admin(request)
    vms = await request.app.state.db.list_vms()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["vm_user", "vm_ip", "status", "attendee", "key_fingerprint", "assigned_at"])
    for v in vms:
        w.writerow([v.vm_user, v.vm_ip, v.status, v.attendee or "",
                    v.key_fingerprint or "", v.assigned_at or ""])
    return PlainTextResponse(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=assignments.csv"},
    )


async def _admin_render(request: Request, notice=None, mismatches=None) -> HTMLResponse:
    sec: Security = request.app.state.security
    db: Database = request.app.state.db
    vms = await db.list_vms()
    counts = await db.counts()
    csrf = sec.issue_csrf()
    return render(request, "admin.html", set_csrf=True, csrf=csrf,
                  vms=vms, counts=counts, mismatches=mismatches, notice=notice)


def secrets_equal(a: str, b: str) -> bool:
    import secrets as _s
    return _s.compare_digest(a, b)
