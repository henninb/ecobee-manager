#!/usr/bin/env python3
"""
Load environment variables from gopass.

Retrieves ecobee/email and ecobee/password from gopass and populates
os.environ with ECOBEE_EMAIL and ECOBEE_PASSWORD.

Existing environment variables are never overwritten (os.environ.setdefault).
"""

import os
import subprocess
import sys


def _gopass_get(path: str) -> str:
    """Retrieve a secret from gopass."""
    result = subprocess.run(
        ['gopass', 'show', path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            f"Error: gopass failed to retrieve '{path}'.\n"
            f"gopass stderr: {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)
    return result.stdout.strip()


def load_secrets() -> None:
    """Populate os.environ from gopass, skipping if credentials are already set."""
    if os.environ.get('ECOBEE_EMAIL') and os.environ.get('ECOBEE_PASSWORD'):
        return

    if not _gopass_available():
        print(
            "Error: gopass is not installed or not in PATH.\n"
            "Install gopass: https://github.com/gopasspw/gopass",
            file=sys.stderr,
        )
        sys.exit(1)

    os.environ.setdefault('ECOBEE_EMAIL', _gopass_get('ecobee/email'))
    os.environ.setdefault('ECOBEE_PASSWORD', _gopass_get('ecobee/password'))


def _gopass_available() -> bool:
    return subprocess.run(
        ['gopass', 'version'],
        capture_output=True,
    ).returncode == 0
