# Migrating from fazla-od

m365ctl is the Phase 0 rebrand of the previous `fazla-od` toolkit. This page
covers the one-time migration. New installs can ignore it.

## 1. Config dir

m365ctl reads config from `~/.config/m365ctl/`. On first run, if that
directory does not exist but `~/.config/fazla-od/` does, m365ctl
auto-migrates (see Group 4 in the Phase 0 plan).

If you prefer to do it manually:

```bash
mv ~/.config/fazla-od ~/.config/m365ctl
```

## 2. macOS Keychain

Legacy keychain entries live under the `FazlaODToolkit` service. m365ctl will
write new entries under its own `m365ctl` service on the next login, but the
old ones are orphaned and confusing. Remove them:

```bash
security delete-generic-password -s FazlaODToolkit -a DelegatedTokenCache 2>/dev/null || true
security delete-generic-password -s FazlaODToolkit -a PfxPassword 2>/dev/null || true
```

Then re-authenticate:

```bash
./bin/od-auth login
```

## 3. config.toml paths

If you hand-edited any `cert_path` / `cert_public` values to point at
`~/.config/fazla-od/...`, rewrite them:

```bash
sed -i '' 's|~/.config/fazla-od|~/.config/m365ctl|g' config.toml
```

(The `-i ''` form is macOS sed; on Linux use `sed -i '...'`.)

## 4. Audit log + undo compatibility

Legacy op_ids in `logs/ops/*.jsonl` remain undoable. Pre-refactor plans used
bare action names (`move`, `rename`, `copy`); Phase 0 namespaces them
(`od.move`, etc.) and normalizes bare actions on read. You do not need to
rewrite old plan or log files.

## 5. PnP.PowerShell PFX + Keychain (recycle-bin / audit-sharing / label paths)

The PnP.PowerShell helpers were updated to default to:

- **PFX:** `~/.config/m365ctl/m365ctl.pfx` (was `~/.config/fazla-od/fazla-od.pfx`)
- **Keychain account:** `m365ctl` (was `fazla-od`); service is unchanged at
  `m365ctl:PfxPassword`.

Existing installs keep working: every PS entrypoint
(`audit-sharing.ps1`, `recycle-purge.ps1`, `recycle-restore.ps1`, plus the
shared `_M365ctlRecycleHelpers.ps1`) silently falls back to the legacy PFX
path and Keychain account when the new ones are missing, with a one-line
deprecation notice on stderr. To clean the warning up:

```bash
# 5a. Move the PFX (or delete it and re-run convert-cert.sh).
mkdir -p ~/.config/m365ctl
mv ~/.config/fazla-od/fazla-od.pfx ~/.config/m365ctl/m365ctl.pfx
chmod 600 ~/.config/m365ctl/m365ctl.pfx

# 5b. Rotate the Keychain account from "fazla-od" to "m365ctl".
PWD_OLD="$(security find-generic-password -a fazla-od -s m365ctl:PfxPassword -w)"
security add-generic-password -a m365ctl -s m365ctl:PfxPassword -w "$PWD_OLD" -T /usr/bin/security
security delete-generic-password -a fazla-od -s m365ctl:PfxPassword
unset PWD_OLD

# 5c. Verify the new locations resolve.
ls -la ~/.config/m365ctl/m365ctl.pfx
security find-generic-password -a m365ctl -s m365ctl:PfxPassword -w | wc -c   # ~40
```

Or, if you'd rather start fresh: `rm ~/.config/fazla-od/fazla-od.pfx` and
re-run `./scripts/ps/convert-cert.sh` (it now writes to the new defaults).

## 6. Live-test env var

The live-test opt-in renamed:

- **Old:** `FAZLA_OD_LIVE_TESTS=1`
- **New:** `M365CTL_LIVE_TESTS=1`

The legacy name still works for one minor version with a deprecation warning.
Update your shell rc file now to avoid future breakage:

```bash
# ~/.zshrc or ~/.bashrc
# OLD: export FAZLA_OD_LIVE_TESTS=1
export M365CTL_LIVE_TESTS=1
```
