# Two keypairs, both generated here so nothing is pasted by hand:
#
#  * keyctl  — the ONE key the signup app holds. Its public half is the forced
#              command on keyadmin@jump; its private half is handed to the app as
#              KEYCTL_PRIVATE_KEY (sensitive output). The app can do nothing on
#              the jump host except run workshop-keyctl.
#
#  * vm_root — lets the jump host SSH root@VM to run `dokku ssh-keys:add`. Private
#              half lives ONLY on the jump host (/etc/workshop/vm_root_ed25519);
#              public half is authorized for root on every attendee VM.
resource "tls_private_key" "keyctl" {
  algorithm = "ED25519"
}

resource "tls_private_key" "vm_root" {
  algorithm = "ED25519"
}

# Uploaded so Civo also authorizes the vm_root key at build time (belt and
# braces — the VM cloud-init appends it to /root/.ssh/authorized_keys too).
resource "civo_ssh_key" "vm_root" {
  name       = "${var.name_prefix}-vm-root"
  public_key = tls_private_key.vm_root.public_key_openssh
}

# Operator key, so you can log in to the jump host as the default user even if
# cloud-init hasn't finished creating the 'ops' user yet.
resource "civo_ssh_key" "operator" {
  name       = "${var.name_prefix}-operator"
  public_key = var.operator_ssh_public_key
}
