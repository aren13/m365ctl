# Certificate-based auth (app-only)

## Why a cert?

App-only flows (`client_credentials`) need either a **client secret** or a
**certificate**. For a long-lived CLI, a self-signed cert is the right
default:

- Secrets expire after at most 2 years and are awkward to rotate from a
  checked-in config.
- Certs are stored on disk under `~/.config/m365ctl/` with `600` permissions.
- Microsoft's own tooling (PnP.PowerShell, Microsoft.Graph PowerShell) takes
  the same PEM cert — so one file covers every supporting tool.

## Generate the cert

```bash
scripts/setup/create-cert.sh m365ctl
```

Outputs:

- Private key: `~/.config/m365ctl/m365ctl.key` (mode 600, never leaves disk).
- Public cert: `~/.config/m365ctl/m365ctl.cer` (upload to Entra).

The script also prints the SHA-1 thumbprint — copy it if any downstream
tooling (e.g. PnP.PowerShell) asks for it explicitly.

## Upload to Entra

1. Entra → **App registrations** → your app → **Certificates & secrets**.
2. **Certificates** tab → **Upload certificate**.
3. Pick `~/.config/m365ctl/m365ctl.cer`.
4. Save.

Entra will display the thumbprint; it should match what the script printed.

## Wire it into `config.toml`

```toml
cert_path   = "~/.config/m365ctl/m365ctl.key"
cert_public = "~/.config/m365ctl/m365ctl.cer"
```

`~` is expanded on load.

## Verify

```bash
./bin/od-auth whoami
```

This prints:

- The signed-in user (delegated flow).
- The cert's CN, SHA-1 thumbprint, and days-until-expiry (app-only flow).

If the thumbprint does not match what Entra shows, re-upload the `.cer`.

## Rotation

Certs issued by `create-cert.sh` are valid for 730 days. About a month before
expiry:

1. Re-run `scripts/setup/create-cert.sh` with a different CN (e.g.
   `m365ctl-2028`) or move the old files aside first.
2. Upload the new `.cer` to Entra (you can have multiple certs attached
   simultaneously).
3. Update `config.toml` paths if they changed.
4. Once `od-auth whoami` works with the new cert, remove the old one from
   Entra.
