"""`od-audit-sharing` subcommand: shell out to PnP.PowerShell.

Plan 3 delivers only the Python wrapper. The heavy lifting lives in
``scripts/ps/audit-sharing.ps1``; see ``docs/ops/pnp-powershell-setup.md``
for one-time setup.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from m365ctl.common.config import load_config


def run_audit(
    *, config_path: Path, scope: str, output_format: str
) -> int:
    cfg = load_config(config_path)
    repo_root = Path(__file__).resolve().parents[4]
    script = repo_root / "scripts" / "ps" / "audit-sharing.ps1"
    if not script.exists():
        print(f"error: {script} not found", file=sys.stderr)
        return 2

    cmd = [
        "pwsh", "-NoLogo", "-NoProfile", "-File", str(script),
        "-Scope", scope,
        "-OutputFormat", output_format,
        "-Tenant", cfg.tenant_id,
        "-ClientId", cfg.client_id,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr, end="")
        return proc.returncode
    print(proc.stdout, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-audit-sharing")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope", required=True,
                   help="site:<url> (site-id form requires admin endpoint)")
    p.add_argument("--output-format", choices=["json", "tsv"], default="json")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_audit(
        config_path=Path(args.config),
        scope=args.scope,
        output_format=args.output_format,
    )
