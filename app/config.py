"""Runtime configuration, read once from the environment.

Every value has a sensible default for local dry-run so the app boots on a laptop
with nothing but ``DRY_RUN=1``. Production supplies the rest via env vars.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _keyctl_private_key() -> str:
    """The app's keyctl private key.

    Prefer KEYCTL_PRIVATE_KEY_B64 — a single-line base64 of the key — because
    many secret UIs/stores flatten the newlines of a multi-line value, which
    makes OpenSSH reject the key ("invalid format"). The base64 form has no
    newlines to lose. Falls back to the raw multi-line KEYCTL_PRIVATE_KEY.
    """
    b64 = (os.environ.get("KEYCTL_PRIVATE_KEY_B64") or "").strip()
    if b64:
        import base64 as _b64
        try:
            return _b64.b64decode(b64, validate=False).decode("utf-8")
        except Exception:  # noqa: BLE001 - fall back to the raw key
            pass
    return os.environ.get("KEYCTL_PRIVATE_KEY", "")


@dataclass(frozen=True)
class Settings:
    database_url: str
    workshop_code: str
    admin_user: str
    admin_password: str
    secret_key: str

    jump_host: str
    jump_public_ip: str
    base_domain: str

    keyadmin_user: str
    keyctl_private_key: str
    keyctl_known_hosts: str
    keyctl_timeout: int
    # Port the app dials on the jump host. 22 is blocked outbound on some
    # hosts (Railway), so the jump also listens on 2222 for the app.
    keyctl_port: int

    dry_run: bool
    rate_limit_per_min: int

    # Where dry-run keeps its fake pool + authorizations.
    dryrun_dir: Path


def load_settings() -> "Settings":
    repo_root = Path(__file__).resolve().parent.parent
    secret = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://workshop:workshop@localhost:5432/workshop"
        ),
        workshop_code=os.environ.get("WORKSHOP_CODE", "dokku2026"),
        admin_user=os.environ.get("ADMIN_USER", "admin"),
        admin_password=os.environ.get("ADMIN_PASSWORD", "change-me"),
        secret_key=secret,
        jump_host=os.environ.get("JUMP_HOST", "workshop-jump"),
        jump_public_ip=os.environ.get("JUMP_PUBLIC_IP", "203.0.113.10"),
        base_domain=os.environ.get("BASE_DOMAIN", "workshop.example.com"),
        keyadmin_user=os.environ.get("KEYADMIN_USER", "keyadmin"),
        keyctl_private_key=_keyctl_private_key(),
        keyctl_known_hosts=os.environ.get("KEYCTL_KNOWN_HOSTS", ""),
        keyctl_timeout=int(os.environ.get("KEYCTL_TIMEOUT", "20")),
        keyctl_port=int(os.environ.get("KEYCTL_PORT", "22")),
        dry_run=_bool("DRY_RUN", default=False),
        rate_limit_per_min=int(os.environ.get("RATE_LIMIT_PER_MIN", "10")),
        dryrun_dir=Path(os.environ.get("DRYRUN_DIR", str(repo_root / "dryrun"))),
    )
