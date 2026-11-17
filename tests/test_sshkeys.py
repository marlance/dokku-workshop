"""Unit tests for public-key validation — no DB, no network."""
from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from app.sshkeys import KeyError_, parse_public_key


def test_valid_ed25519(ssh_keypair):
    parsed = parse_public_key(ssh_keypair)
    assert parsed.key_type == "ssh-ed25519"
    assert parsed.fingerprint.startswith("SHA256:")
    assert parsed.normalized.startswith("ssh-ed25519 ")


def test_extra_whitespace_is_normalized(ssh_keypair):
    parsed = parse_public_key("\n\n  " + ssh_keypair + "  \n")
    assert parsed.normalized == ssh_keypair.strip()


def test_private_key_is_rejected_clearly():
    blob = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    with pytest.raises(KeyError_) as exc:
        parse_public_key(blob)
    assert "PRIVATE" in str(exc.value).upper()


def test_garbage_is_rejected():
    with pytest.raises(KeyError_):
        parse_public_key("this is not a key")


def test_empty_is_rejected():
    with pytest.raises(KeyError_):
        parse_public_key("   ")


def test_unsupported_type_is_rejected():
    with pytest.raises(KeyError_):
        parse_public_key("ssh-dss AAAAB3NzaC1kc3M... nope")


def test_small_rsa_is_rejected():
    """RSA < 2048 bits must be refused. Skips if the host won't make a 1024-bit key."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "id")
        proc = subprocess.run(
            ["ssh-keygen", "-t", "rsa", "-b", "1024", "-N", "", "-f", path],
            capture_output=True,
        )
        if proc.returncode != 0:
            pytest.skip("host refuses to generate 1024-bit RSA")
        pub = open(path + ".pub").read().strip()
    with pytest.raises(KeyError_):
        parse_public_key(pub)
