# Dokku Workshop Platform

Self-service signup + infrastructure for a 50-minute hands-on **Dokku on Civo**
workshop. Each attendee gets their own pre-provisioned Civo VM (Dokku + k3s
scheduler), reached through a single jump host. Attendees paste an SSH public key
into a web app and are handed a VM, an SSH config and a cheat sheet.

> **Status:** Phase 1 (signup app + keyctl) and Phase 2 (Terraform — jump host +
> attendee VMs on Civo, under `terraform/`) are both built.

```
Attendee laptop
  ├── HTTPS ─▶ Signup app (Railway/Dokku)
  │               └─ SSH (restricted key) ─▶ keyadmin@jump ─▶ workshop-keyctl
  │                                                            ├─ writes jump user's authorized_keys
  │                                                            └─ SSH root@VM: dokku ssh-keys:add
  ├── SSH via ProxyJump ─▶ jump@jump ─▶ dokku@VM  (git push, dokku commands)
  └── HTTP ─▶ Caddy on jump ─▶ VM:80  (attendee apps)
```

**Design rules**
- The app never holds VM credentials. It holds **one** key that can only run
  `workshop-keyctl` on the jump host (forced command). The VM root key lives only
  on the jump host.
- **Push model.** Attendee keys are written into the `jump` user's
  `authorized_keys`. SSH logins never depend on the signup app being up.
- **Isolation.** Each key is authorized `restrict,port-forwarding,permitopen="<vm_ip>:22"`,
  so it can only tunnel to its own VM.

## Layout

```
app/          FastAPI signup app (signup / personal / admin pages)
keyctl/       workshop-keyctl — the forced command that runs on the jump host
sample-app/   the Node app attendees deploy during the workshop
tests/        pytest suite (key validation, concurrency, full flow)
docs/         day-of runbook
dryrun/       local fake VM pool so the app runs with no infrastructure
```

## Packaging: buildpacks, no Dockerfile

Deploys to **Railway, Render, Dokku or Fly** straight from source via Cloud Native
/ Heroku buildpacks — fitting, since the whole workshop is "git push, no
Dockerfile". The buildpack needs only:

- `requirements.txt` — triggers the Python buildpack
- `Procfile` — `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- `.python-version` / `runtime.txt` — pins Python 3.12

`docker-compose.yml` is for **local dev only** (Postgres + the app on a stock
`python:3.12` image).

## Quick start (local, no infrastructure)

```bash
cp .env.example .env            # defaults are fine for dry-run
docker compose up               # Postgres + app in DRY_RUN mode
# open http://localhost:8000      signup
# open http://localhost:8000/admin   (admin / change-me)
```

In `DRY_RUN=1` the app reads a fake pool from `dryrun/vm_pool.json` and records
"authorizations" in `dryrun/authorized.json` instead of calling a real jump host,
so the full signup flow works on a laptop.

## Run the tests

```bash
pip install -r requirements-dev.txt
docker compose up -d db
DATABASE_URL=postgresql://workshop:workshop@localhost:5432/workshop pytest
bash keyctl/test/test_keyctl.sh      # keyctl, no DB needed
```

Pure unit tests (key validation) run with just `pytest`; DB-backed tests skip
automatically if Postgres isn't reachable.

## Deploy the signup app (production)

Set these env vars on your platform (see `.env.example` for the full list):

| var | meaning |
|---|---|
| `DATABASE_URL` | managed Postgres |
| `WORKSHOP_CODE` | access code shown on a slide |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `/admin` basic auth |
| `SECRET_KEY` | signs CSRF tokens |
| `JUMP_HOST` / `JUMP_PUBLIC_IP` / `BASE_DOMAIN` | shown to attendees |
| `KEYADMIN_USER` | user on the jump host (default `keyadmin`) |
| `KEYCTL_PRIVATE_KEY` | key that authenticates to `keyadmin@jump` (contents) |
| `KEYCTL_KNOWN_HOSTS` | pinned jump-host host key (required; we never disable checking) |
| `DRY_RUN` | unset/`0` in production |

Then on the jump host, install `keyctl/workshop-keyctl` to
`/usr/local/bin/workshop-keyctl` and force it for `keyadmin` (Phase 2 cloud-init
does this automatically). See `keyctl/README.md`.

## Phase 1 acceptance

- [x] Full signup flow works locally in dry-run with docker-compose
- [x] Two simultaneous signups never get the same VM (`tests/test_signup_concurrency.py`)
- [x] Same key twice returns the same VM
- [x] A pasted private key is rejected with a clear message
- [x] keyctl rejects bad input even when called directly (`keyctl/test/`)
- [x] keyctl `add` is idempotent and `remove` fully revokes
