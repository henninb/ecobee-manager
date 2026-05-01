#!/usr/bin/env python3
"""
Load environment variables from gopass.

Retrieves ecobee/email and ecobee/password from gopass and populates
os.environ with ECOBEE_EMAIL and ECOBEE_PASSWORD.

Existing environment variables are never overwritten (os.environ.setdefault).
"""

import os
import subprocess

_GOPASS_TIMEOUT = 10  # seconds


def _gopass_get(path: str) -> str:
    """Retrieve a secret from gopass."""
    result = subprocess.run(
        ['gopass', 'show', path],
        capture_output=True,
        text=True,
        timeout=_GOPASS_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gopass failed to retrieve '{path}': {result.stderr.strip()}"
        )
    return result.stdout.strip()


def load_secrets() -> None:
    """Populate os.environ from gopass, skipping if credentials are already set."""
    if os.environ.get('ECOBEE_EMAIL') and os.environ.get('ECOBEE_PASSWORD'):
        return

    try:
        os.environ.setdefault('ECOBEE_EMAIL', _gopass_get('ecobee/email'))
        os.environ.setdefault('ECOBEE_PASSWORD', _gopass_get('ecobee/password'))
    except FileNotFoundError:
        raise RuntimeError(
            "gopass is not installed or not in PATH. "
            "Install gopass: https://github.com/gopasspw/gopass"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"gopass timed out after {_GOPASS_TIMEOUT}s — "
            "check that your gopass store is unlocked"
        ) from None
