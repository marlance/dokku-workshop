# Deploying the signup app

The app runs on the `konduit-mum1-kubefunk` cluster and is deployed by ArgoCD.

- **URL:** https://dokku-workshop.mum1.apps.kubefunk.net
- **ArgoCD:** https://argocd.mum1.apps.kubefunk.net (application `dokku-workshop`)
- **Namespace:** `dokku-workshop`

## How a change reaches the cluster

1. Push to `main`.
2. If the app code changed, `.github/workflows/image.yml` builds the image,
   pushes `ghcr.io/marlance/dokku-workshop:<commit>` and writes that tag into
   `deploy/values.yaml`.
3. ArgoCD notices the change to `deploy/values.yaml` and rolls the app
   (within about three minutes).

To change a setting, edit `deploy/values.yaml` and push. To roll back, set
`image.tag` to an earlier commit id and push.

## Secrets

Never put secrets in this repository. Two Kubernetes secrets must exist in the
`dokku-workshop` namespace:

| Secret | Keys |
|---|---|
| `dokku-workshop-env` | `DATABASE_URL`, `SECRET_KEY`, `WORKSHOP_CODE`, `ADMIN_USER`, `ADMIN_PASSWORD`, `KEYCTL_PRIVATE_KEY_B64` |
| `ghcr-pull` | registry login for `ghcr.io` (a token with `read:packages`) |

After changing `dokku-workshop-env`, restart the app so it reads the new values:

    kubectl -n dokku-workshop rollout restart deploy/dokku-workshop

## When the jump host is rebuilt

Its address and host key change. Update `JUMP_PUBLIC_IP` and
`KEYCTL_KNOWN_HOSTS` in `deploy/values.yaml` (`ssh-keyscan -p 2222 -t ed25519
jump.kuberocket.com`) and replace `KEYCTL_PRIVATE_KEY_B64` in the secret.
