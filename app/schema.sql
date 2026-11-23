-- One row per attendee VM. Terraform (Phase 2) produces the pool; the app syncs
-- it in via `keyctl pool` and then hands rows out to attendees.
CREATE TABLE IF NOT EXISTS vms (
    vm_user         TEXT PRIMARY KEY,              -- e.g. "user01"
    vm_ip           TEXT NOT NULL,                 -- private IP, used verbatim in permitopen
    status          TEXT NOT NULL DEFAULT 'free'   -- 'free' | 'taken'
                    CHECK (status IN ('free', 'taken')),
    attendee        TEXT,
    key_fingerprint TEXT,                          -- SHA256:... ; dedupe repeat signups
    label           TEXT,                          -- keyctl/dokku label (= vm_user)
    token           TEXT UNIQUE,                   -- personal page token
    assigned_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vms_status ON vms (status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vms_fingerprint
    ON vms (key_fingerprint) WHERE key_fingerprint IS NOT NULL;

-- Key setup runs in the background after a VM is claimed, so signup returns
-- immediately. NULL key_state on a taken row means "ready" (rows from before
-- this column existed).
ALTER TABLE vms ADD COLUMN IF NOT EXISTS key_state TEXT
    CHECK (key_state IN ('preparing', 'ready', 'failed'));
ALTER TABLE vms ADD COLUMN IF NOT EXISTS key_error  TEXT;
ALTER TABLE vms ADD COLUMN IF NOT EXISTS public_key TEXT;  -- what to authorize; public, not secret

-- Registered attendees. Only an email on this list can claim a VM, and each
-- email gets at most one. Loaded from the admin page.
CREATE TABLE IF NOT EXISTS attendees (
    email     TEXT PRIMARY KEY,                  -- stored trimmed + lowercased
    name      TEXT,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE vms ADD COLUMN IF NOT EXISTS email TEXT;
-- The one-VM-per-email rule lives here, not in app code: two simultaneous
-- signups for the same email can both pick a free VM, but only one can commit.
CREATE UNIQUE INDEX IF NOT EXISTS idx_vms_email ON vms (email) WHERE email IS NOT NULL;
