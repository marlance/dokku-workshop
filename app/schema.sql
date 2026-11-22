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
