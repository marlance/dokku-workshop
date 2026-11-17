"""Validate and normalize attendee-supplied SSH public keys.

The app and the keyctl script both validate independently; this module is the
app-side gate. We never trust the browser: a key only passes if ``ssh-keygen``
can read it. We also catch the single most common support issue — someone pasting
their *private* key — and give a clear message.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

# Minimum RSA modulus we accept. ed25519/ecdsa have no size knob.
MIN_RSA_BITS = 2048

_PRIVATE_KEY_MARKERS = ("PRIVATE KEY", "BEGIN OPENSSH PRIVATE")
_ALLOWED_TYPES = {"ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
                  "ecdsa-sha2-nistp521", "ssh-rsa"}


class KeyError_(ValueError):
    """Raised with a human-friendly, attendee-facing message."""


@dataclass(frozen=True)
class ParsedKey:
    normalized: str   # single line: "<type> <base64> <comment>"
    fingerprint: str  # e.g. "SHA256:abc..."
    key_type: str     # e.g. "ssh-ed25519"
    bits: int


def _run_keygen(path: str) -> str:
    try:
        proc = subprocess.run(
            ["ssh-keygen", "-l", "-f", path],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError as exc:  # pragma: no cover - env without openssh
        raise KeyError_("Server is missing ssh-keygen; contact the instructor.") from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        raise KeyError_("Could not read that key (timed out). Try again.") from exc
    if proc.returncode != 0:
        raise KeyError_(
            "That does not look like a valid SSH public key. Paste the full "
            "contents of your .pub file (one line starting with ssh-ed25519 or ssh-rsa)."
        )
    return proc.stdout.strip()


def parse_public_key(raw: str) -> ParsedKey:
    """Return a :class:`ParsedKey` or raise :class:`KeyError_` with a clear message."""
    if not raw or not raw.strip():
        raise KeyError_("Please paste your SSH public key.")

    text = raw.strip()

    # 1. Catch pasted private keys before doing anything else.
    upper = text.upper()
    if any(marker in upper for marker in _PRIVATE_KEY_MARKERS):
        raise KeyError_(
            "That is your PRIVATE key — never share it. Paste your PUBLIC key "
            "instead: the contents of ~/.ssh/id_ed25519.pub (ends in .pub)."
        )

    # 2. Collapse to a single line and sanity-check the prefix.
    #    A .pub file is one line; if someone pasted extra whitespace/newlines we
    #    take the first non-empty line.
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    parts = first_line.split()
    if len(parts) < 2:
        raise KeyError_(
            "That does not look like an SSH public key. It should be one line like "
            "'ssh-ed25519 AAAA... you@laptop'."
        )
    key_type = parts[0]
    if key_type not in _ALLOWED_TYPES:
        raise KeyError_(
            f"Unsupported key type '{key_type}'. Use ed25519 (recommended), ecdsa, "
            "or RSA. Create one with: ssh-keygen -t ed25519"
        )

    # 3. Hand it to ssh-keygen — the real validator.
    with tempfile.NamedTemporaryFile("w", suffix=".pub", delete=False) as fh:
        fh.write(first_line + "\n")
        tmp = fh.name
    try:
        out = _run_keygen(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover
            pass

    # ssh-keygen -l output: "<bits> SHA256:<fp> <comment> (<TYPE>)"
    m = re.match(r"^(\d+)\s+(\S+)\s+(.*)\((\w+)\)\s*$", out)
    if not m:  # pragma: no cover - defensive
        raise KeyError_("Could not fingerprint that key. Paste the .pub file contents.")
    bits = int(m.group(1))
    fingerprint = m.group(2)

    if key_type == "ssh-rsa" and bits < MIN_RSA_BITS:
        raise KeyError_(
            f"Your RSA key is only {bits} bits. Use at least {MIN_RSA_BITS} bits, "
            "or better, create an ed25519 key: ssh-keygen -t ed25519"
        )

    return ParsedKey(
        normalized=first_line,
        fingerprint=fingerprint,
        key_type=key_type,
        bits=bits,
    )
