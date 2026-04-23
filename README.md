# Fazla OneDrive Toolkit

CLI for admin-scoped control of the Fazla M365 OneDrive + SharePoint tenant.

See `docs/superpowers/specs/2026-04-24-fazla-onedrive-toolkit-design.md` for the full design.

## Quick start (after Plan 1)

1. Copy `config.toml.example` to `config.toml` and fill in.
2. `./bin/od-auth login` — device-code sign-in (once per token lifetime).
3. `./bin/od-auth whoami` — verify both auth flows work.

## Layout

See spec §9 for the full layout. After Plan 1 only `bin/od-auth` exists.
