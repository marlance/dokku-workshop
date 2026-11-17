# workshop-keyctl

The **only** thing the signup app can run on the jump host. Installed as the
forced command for the `keyadmin` user, so a compromised app key can do nothing
except call this script.

## Install (done automatically by Phase 2 cloud-init)

```bash
install -m 0755 workshop-keyctl /usr/local/bin/workshop-keyctl
```

`keyadmin`'s `~/.ssh/authorized_keys` (one line):

```
command="/usr/local/bin/workshop-keyctl",restrict ssh-ed25519 AAAA... signup-app
```

The script needs permission to write `/home/jump/.ssh/authorized_keys` and to read
the VM root key — in production it runs as root (via the forced command / sudo set
up in cloud-init). It keeps `authorized_keys` at mode `600` owned by `jump` so
sshd `StrictModes` stays happy.

## Contract

Reads the action from `SSH_ORIGINAL_COMMAND` (or argv when testing). Always prints
exactly **one JSON line** on stdout; non-zero exit on failure.

| command | does |
|---|---|
| `pool` | print the VM pool JSON (`/etc/workshop/vm_pool.json`, written by Terraform) |
| `add <vm_user> <label>` | read a public key on stdin; append a restricted line to the jump user's `authorized_keys`; run `dokku ssh-keys:add <label>` on that VM. Idempotent. |
| `remove <vm_user> <label>` | remove the key from both places |
| `list` | print current authorizations as JSON (for reconciliation) |

Validation (never trusts the caller): `vm_user` must exist in the pool, `label`
must match `^[a-z0-9-]{1,32}$`, the key must pass `ssh-keygen -l -f` and must not
be a private key. Edits are done under `flock` and replace `authorized_keys`
atomically.

## Env overrides (for testing / non-default layouts)

| var | default |
|---|---|
| `WORKSHOP_POOL_FILE` | `/etc/workshop/vm_pool.json` |
| `WORKSHOP_JUMP_AUTHKEYS` | `/home/jump/.ssh/authorized_keys` |
| `WORKSHOP_LOCK` | `/run/lock/workshop-keyctl.lock` |
| `WORKSHOP_VM_KEY` | `/etc/workshop/vm_root_ed25519` |
| `WORKSHOP_SSH_BIN` | `ssh` |
| `WORKSHOP_VM_SSH_USER` | `root` |
| `WORKSHOP_JUMP_USER` | `jump` |
| `WORKSHOP_NO_CHOWN` | (unset) — set `1` to skip chown in tests |

## Test

```bash
bash test/test_keyctl.sh
```

Runs the full add/list/remove/idempotency/rejection suite with a stub `ssh`, so no
jump host or VMs are required.
