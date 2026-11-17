# Day-of runbook

## Before the workshop

1. **Phase 2:** `terraform apply` the infra (jump host + VMs). Confirm
   `scripts/check-ready.sh` is green for every VM.
2. Deploy the signup app (Railway/Dokku) with production env vars. Leave
   `DRY_RUN` unset.
   - `KEYCTL_PRIVATE_KEY` = the app's key (authorized as `keyadmin` on the jump host).
   - `KEYCTL_KNOWN_HOSTS` = the jump host's real host key. Get it with:
     `ssh-keyscan -t ed25519 <JUMP_PUBLIC_IP>`.
3. Open `/admin`, click **Sync pool from keyctl**. Confirm the VM count matches.
4. Set `WORKSHOP_CODE` to the code you'll show on the slide.
5. Smoke-test one signup yourself end to end, then **Release** that VM from `/admin`.

## During signup

- Put the signup URL and the access code on a slide.
- Watch `/admin`: free vs taken counts, who has what.
- "All VMs are taken" → you've run out; you provisioned `spare_count` for this,
  release any no-shows from `/admin`.

## Common attendee issues

| symptom | fix |
|---|---|
| "you pasted your private key" | they need the `.pub` file: `cat ~/.ssh/id_ed25519.pub` |
| no key at all | `ssh-keygen -t ed25519`, accept defaults, re-paste |
| `ssh dokku@dokku-userNN` hangs | SSH config not saved, or wrong file location — check `~/.ssh/config` |
| lost their page | they re-sign up with the same key → same VM, same page |

## Reconcile / recover

- `/admin` → **Reconcile** compares the jump host's authorizations with the DB and
  flags drift (authorized-but-free, taken-but-unauthorized).
- If the signup app dies mid-workshop, **logins keep working** — keys already live
  in the jump host's `authorized_keys`. Only *new* signups are affected.

## After the workshop

- Export assignments: `/admin` → **Export CSV**.
- Tear down: `scripts/teardown.sh` (Phase 2), which wraps `terraform destroy`.
