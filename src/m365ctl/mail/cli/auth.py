"""`m365ctl mail auth login|whoami` — shares the same token cache as od-auth."""
from __future__ import annotations

from m365ctl.onedrive.cli.auth import main as od_auth_main


def main(argv: list[str]) -> int:
    # Shared delegated cache means mail-auth login === od-auth login.
    return od_auth_main(argv)
