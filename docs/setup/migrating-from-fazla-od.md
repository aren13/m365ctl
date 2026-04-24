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

## 5. Live-test env var

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
