#!/usr/bin/env python3
"""
Load environment variables from env.secrets.enc (SOPS-encrypted) or env.secrets (plaintext).

Priority:
  1. env.secrets.enc  — decrypted in-memory via sops; requires age key at
     ~/.config/sops/age/keys.txt or SOPS_AGE_KEY / SOPS_AGE_KEY_FILE env vars.
  2. env.secrets      — plain dotenv fallback.

Existing environment variables are never overwritten (os.environ.setdefault).
"""

import os
import subprocess
import sys


def _parse_dotenv(text: str) -> dict:
    """Parse KEY=VALUE lines, skipping blanks and comments."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, _, value = line.partition('=')
            result[key.strip()] = value.strip()
    return result


def load_secrets(secrets_enc: str = 'env.secrets.enc', secrets_plain: str = 'env.secrets') -> None:
    """Populate os.environ from the secrets file (encrypted preferred)."""

    if os.path.exists(secrets_enc):
        if not _sops_available():
            print(
                f"Error: '{secrets_enc}' exists but sops is not installed.\n"
                "Install sops: https://github.com/getsops/sops/releases",
                file=sys.stderr,
            )
            sys.exit(1)

        result = subprocess.run(
            ['sops', '-d', '--input-type', 'dotenv', '--output-type', 'dotenv', secrets_enc],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"Error: SOPS decryption of '{secrets_enc}' failed.\n"
                "Check that the age private key is available at "
                "~/.config/sops/age/keys.txt (or set SOPS_AGE_KEY_FILE).\n"
                f"sops stderr: {result.stderr.strip()}",
                file=sys.stderr,
            )
            sys.exit(1)

        for key, value in _parse_dotenv(result.stdout).items():
            os.environ.setdefault(key, value)
        return

    if os.path.exists(secrets_plain):
        with open(secrets_plain) as f:
            for key, value in _parse_dotenv(f.read()).items():
                os.environ.setdefault(key, value)
        return

    # Neither file found — callers will catch missing env vars themselves.


def _sops_available() -> bool:
    return subprocess.run(
        ['sops', '--version'],
        capture_output=True,
    ).returncode == 0
