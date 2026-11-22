"""Client for the jump host's ``workshop-keyctl`` forced command.

The app never holds VM credentials. It holds ONE key that can only run
``workshop-keyctl`` on the jump host (forced command). Every call shells out to:

    ssh -i <app_key> keyadmin@<jump> "<subcommand> <args...>"

and parses the single JSON line the script prints on stdout.

:class:`DryRunKeyctl` replaces all of that with a local file so the whole signup
flow runs on a laptop with no infrastructure (``DRY_RUN=1``).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Protocol

from .config import Settings


class KeyctlError(RuntimeError):
    """A keyctl call failed; message is safe to log (no key material)."""


class Keyctl(Protocol):
    def pool(self) -> list[dict]: ...
    def add(self, vm_user: str, label: str, public_key: str) -> dict: ...
    def remove(self, vm_user: str, label: str) -> dict: ...
    def list(self) -> list[dict]: ...


# --------------------------------------------------------------------------- #
# Real client: SSH to keyadmin@jump
# --------------------------------------------------------------------------- #
class SSHKeyctl:
    def __init__(self, settings: Settings):
        self._s = settings
        self._key_path: str | None = None
        self._known_hosts_path: str | None = None
        self._write_credentials()

    def _write_credentials(self) -> None:
        if self._s.keyctl_private_key:
            fd, path = tempfile.mkstemp(prefix="keyctl_", suffix=".key")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as fh:
                key = self._s.keyctl_private_key
                fh.write(key if key.endswith("\n") else key + "\n")
            self._key_path = path
        if self._s.keyctl_known_hosts:
            fd, path = tempfile.mkstemp(prefix="keyctl_", suffix=".known_hosts")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as fh:
                kh = self._s.keyctl_known_hosts
                fh.write(kh if kh.endswith("\n") else kh + "\n")
            self._known_hosts_path = path

    def _ssh_base(self) -> list[str]:
        if not self._key_path:
            raise KeyctlError("KEYCTL_PRIVATE_KEY is not set; cannot reach the jump host.")
        if not self._known_hosts_path:
            # We refuse to disable host-key checking. Fail loudly instead.
            raise KeyctlError("KEYCTL_KNOWN_HOSTS is not set; refusing to skip host-key checking.")
        return [
            "ssh",
            "-i", self._key_path,
            "-p", str(self._s.keyctl_port),
            "-o", "BatchMode=yes",
            "-o", "AddressFamily=inet",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self._known_hosts_path}",
            "-o", f"ConnectTimeout={max(1, self._s.keyctl_timeout // 2)}",
            f"{self._s.keyadmin_user}@{self._s.jump_host}",
        ]

    def _run(self, remote_cmd: str, stdin: str | None = None) -> dict | list:
        argv = self._ssh_base() + [remote_cmd]
        try:
            proc = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self._s.keyctl_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise KeyctlError("keyctl call timed out talking to the jump host.") from exc
        out = (proc.stdout or "").strip()
        # The script prints exactly one JSON line on stdout.
        line = out.splitlines()[-1] if out else ""
        try:
            payload = json.loads(line) if line else {}
        except json.JSONDecodeError as exc:
            raise KeyctlError(
                f"keyctl returned unparseable output (rc={proc.returncode})."
            ) from exc
        if proc.returncode != 0 or (isinstance(payload, dict) and payload.get("ok") is False):
            msg = payload.get("error") if isinstance(payload, dict) else None
            if not msg:
                # rc=255 is ssh itself failing (DNS, network, host key, bad
                # key). Its stderr says which, so surface the last line —
                # it holds no secrets, only the reason and the target host.
                err = (proc.stderr or "").strip().splitlines()
                detail = f" ssh said: {err[-1][:200]}" if err else ""
                msg = (f"keyctl failed (rc={proc.returncode}) talking to "
                       f"{self._s.keyadmin_user}@{self._s.jump_host}:{self._s.keyctl_port}.{detail}")
            raise KeyctlError(msg)
        return payload

    def pool(self) -> list[dict]:
        res = self._run("pool")
        return res if isinstance(res, list) else res.get("pool", [])

    def add(self, vm_user: str, label: str, public_key: str) -> dict:
        res = self._run(f"add {vm_user} {label}", stdin=public_key + "\n")
        return res  # type: ignore[return-value]

    def remove(self, vm_user: str, label: str) -> dict:
        res = self._run(f"remove {vm_user} {label}")
        return res  # type: ignore[return-value]

    def list(self) -> list[dict]:
        res = self._run("list")
        return res if isinstance(res, list) else res.get("authorizations", [])


# --------------------------------------------------------------------------- #
# Dry-run client: a local file, no infrastructure
# --------------------------------------------------------------------------- #
class DryRunKeyctl:
    """Mimics the keyctl JSON contract against ``dryrun/``.

    - pool is read from ``dryrun/vm_pool.json`` (seeded in the repo).
    - authorizations are kept in ``dryrun/authorized.json``.
    """

    def __init__(self, settings: Settings):
        self._dir = settings.dryrun_dir
        self._pool_file = self._dir / "vm_pool.json"
        # The pool seed is read-only and shipped with the app. Mutable state
        # (authorizations) must go somewhere writable: under buildpack runtimes
        # the app dir (/workspace) is read-only at launch, so default to a
        # writable temp dir. Override with DRYRUN_WRITE_DIR if needed.
        write_dir = Path(
            os.environ.get("DRYRUN_WRITE_DIR")
            or (Path(tempfile.gettempdir()) / "dokku-workshop-dryrun")
        )
        write_dir.mkdir(parents=True, exist_ok=True)
        self._auth_file = write_dir / "authorized.json"
        self._lock = threading.Lock()
        if not self._auth_file.exists():
            self._auth_file.write_text("[]\n")

    def _load_pool(self) -> list[dict]:
        if not self._pool_file.exists():
            return []
        return json.loads(self._pool_file.read_text() or "[]")

    def _load_auth(self) -> list[dict]:
        return json.loads(self._auth_file.read_text() or "[]")

    def _save_auth(self, rows: list[dict]) -> None:
        tmp = self._auth_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2) + "\n")
        tmp.replace(self._auth_file)

    def pool(self) -> list[dict]:
        return self._load_pool()

    def _vm(self, vm_user: str) -> dict | None:
        return next((v for v in self._load_pool() if v.get("user") == vm_user), None)

    def add(self, vm_user: str, label: str, public_key: str) -> dict:
        with self._lock:
            vm = self._vm(vm_user)
            if vm is None:
                raise KeyctlError(f"unknown vm_user: {vm_user}")
            fp = public_key.split()[1][:20] if len(public_key.split()) > 1 else public_key[:20]
            rows = self._load_auth()
            for r in rows:
                if r["vm_user"] == vm_user and r["label"] == label:
                    return {"ok": True, "vm_user": vm_user, "label": label,
                            "vm_ip": vm["ip"], "idempotent": True}
            rows.append({"vm_user": vm_user, "label": label, "vm_ip": vm["ip"],
                         "key_preview": fp})
            self._save_auth(rows)
            return {"ok": True, "vm_user": vm_user, "label": label,
                    "vm_ip": vm["ip"], "idempotent": False}

    def remove(self, vm_user: str, label: str) -> dict:
        with self._lock:
            rows = self._load_auth()
            kept = [r for r in rows if not (r["vm_user"] == vm_user and r["label"] == label)]
            self._save_auth(kept)
            return {"ok": True, "vm_user": vm_user, "label": label,
                    "removed": len(rows) - len(kept)}

    def list(self) -> list[dict]:
        return self._load_auth()


def make_keyctl(settings: Settings) -> Keyctl:
    return DryRunKeyctl(settings) if settings.dry_run else SSHKeyctl(settings)
