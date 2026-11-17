# Phase 2 — Terraform (jump host + attendee VMs on Civo)

Provisions the infrastructure the signup app drives: one **jump host** (Caddy
ingress + SSH bastion + `keyadmin` forced command + VM root key + pool file) and
**N attendee VMs** (Dokku + the k3s scheduler), wired to the exact contract
Phase 1 expects.

```
Attendee ──HTTP──▶ *.<base_domain> (wildcard DNS) ──▶ Caddy on jump ──▶ VM:80 (dokku nginx)
Attendee ──SSH ProxyJump──▶ jump ──(restricted, permitopen=its VM)──▶ dokku@VM
Signup app ──SSH──▶ keyadmin@jump ──(forced command)──▶ workshop-keyctl
```

## What gets created

| Resource | Notes |
|---|---|
| `civo_network` | project network |
| `civo_reserved_ip` (jump) | stable public IP; wildcard DNS points here |
| `civo_firewall` × 2 | jump: 22+80 public. VMs: 22+80 **only from the jump IP** |
| `civo_instance` (jump) | Caddy, bastion users (`jump`/`keyadmin`/`ops`), keyctl, pool, VM key |
| `civo_instance` × `vm_count` | Dokku + k3s scheduler, root authorized for the jump |
| `cloudflare_record` × 2 | `*` and `jump` → jump IP (DNS-only, in Cloudflare) |
| `tls_private_key` × 2 | keyctl key (app) and vm_root key (jump→VM) — generated, not pasted |

## Prerequisites

- Terraform ≥ 1.3, the `civo` CLI logged in (`civo apikey current`), `export CIVO_TOKEN=...`.
- A domain in **Cloudflare** (→ `cloudflare_zone`), and a Cloudflare API token with
  Zone:DNS:Edit: `export CLOUDFLARE_API_TOKEN=...`.
- Your SSH keypair (→ `operator_ssh_public_key`).

## Apply

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # cloudflare_zone + base_domain + operator key
export CIVO_TOKEN=...  CLOUDFLARE_API_TOKEN=...
terraform init
terraform plan        # sanity-check sizes/image/region here
terraform apply
```

VMs take several minutes (Dokku + k3s). Then:

```bash
chmod +x scripts/*.sh
./scripts/check-ready.sh     # expect: ALL READY
./scripts/smoke-test.sh      # edge check (502 for the app host is fine pre-deploy)
```

## Wire the signup app to it (leave dry-run)

`terraform output app_env` prints what to set; plus the two that aren't in state:

```bash
terraform output -raw keyctl_private_key      # -> KEYCTL_PRIVATE_KEY
ssh-keyscan -t ed25519 "$(terraform output -raw jump_public_ip)"   # -> KEYCTL_KNOWN_HOSTS
```

Set those on the Konstruct app, **unset `DRY_RUN`**, then in `/admin` click
*Sync pool from keyctl* and confirm the VM count.

## Teardown

```bash
./scripts/teardown.sh        # wraps terraform destroy (asks for confirmation)
```

## Things to confirm on first `plan` (Civo catalogue changes)

- **`instance_size` / `jump_size`** — `civo size ls` for the 8GB/4CPU slug.
- **`ubuntu_filter`** — `civo diskimage ls` for the exact Ubuntu name.
- **`dokku_version`** — a current tag at github.com/dokku/dokku/releases.
- **`region`** — that `mum1` is reachable with your token.

## Notes / design choices

- **Cost:** VMs bill while running. 12 × g3.large ≈ $1.44/hr; destroy after.
- **Isolation:** each attendee key is `permitopen="<their VM>:22"` on the jump, so
  it can only tunnel to its own VM. VM firewall only admits the jump.
- **Attendee app naming:** Caddy maps `myapp-userNN` → that VM with Host
  `myapp.<base_domain>`, and `myapp-k8s-userNN` → Host `myapp-k8s.<base_domain>`.
  So the workshop convention is app names **`myapp`** (default scheduler) and
  **`myapp-k8s`** (k3s scheduler). Adjust the Caddyfile template if you rename.
- **TLS:** HTTP only today (matches the app's `http://` URLs). To add HTTPS,
  drop the `http://` prefix in `templates/Caddyfile.tftpl` and open 443 on the
  jump firewall — Caddy will get Let's Encrypt certs automatically.
- **Script size:** the jump cloud-init embeds `workshop-keyctl` (base64). If Civo
  rejects the user-data for size, host the script elsewhere and `curl` it in.
