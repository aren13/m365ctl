# First run

Goal: working `./bin/od-auth whoami` in **≤ 20 minutes** from a fresh clone.

If it takes longer, [file an issue](https://github.com/aren13/m365ctl/issues)
— the onboarding gap is a bug.

## Prerequisites

- macOS or Linux.
- Python 3.11+ (managed for you by `uv`).
- `uv` — https://docs.astral.sh/uv/.
- Admin access (or cooperating admin) on a Microsoft 365 / Entra tenant.

## 1. Clone + install (≈ 2 min)

```bash
git clone https://github.com/<you>/m365ctl
cd m365ctl
uv sync --all-extras
```

## 2. Copy the config template (≈ 1 min)

```bash
cp config.toml.example config.toml
```

Leave the placeholders in place for now — you'll fill them in after the Azure
steps.

## 3. Register the Entra app (≈ 8 min)

Follow [azure-app-registration.md](azure-app-registration.md):

- Register the app (1 min).
- Add delegated + application API permissions (3 min).
- Grant admin consent (1 min — **longer if you are not a tenant admin**; in
  that case ask one. It is a one-time action.).
- Copy `tenant_id` and `client_id` into `config.toml` (1 min).

> **Non-admin note:** Admin consent must be granted by a Global Administrator,
> Privileged Role Administrator, or Cloud App Administrator. If you have to
> wait for someone else, this step can push the first-run well past 20
> minutes. Skip to step 4 and come back to `od-auth login` afterwards.

## 4. Generate + upload the cert (≈ 3 min)

```bash
scripts/setup/create-cert.sh
```

Then per [certificate-auth.md](certificate-auth.md):

- Upload `~/.config/m365ctl/m365ctl.cer` to Entra → Certificates & secrets.
- Copy the two `cert_*` paths into `config.toml`.

## 5. First login (≈ 2 min)

```bash
./bin/od-auth login
```

This starts the device-code flow: it prints a short code and a URL in the
terminal. Open the URL in any browser, enter the code, and approve the
permissions. The login shell returns when auth completes.

## 6. Verify (≈ 1 min)

```bash
./bin/od-auth whoami
```

Prints the delegated UPN plus the cert's CN / thumbprint / days-until-expiry.
Both lines non-empty = you're done.

## 7. First real command (≈ 1 min)

```bash
./bin/od-inventory --top-by-size 10
```

Lists your ten largest OneDrive files. All reads — no mutation.

## Next steps

- Browse `bin/` for other verbs. Each accepts `--help`.
- Read the safety-model section of [README.md](../../README.md) before running
  anything mutating.
- If you hit an error, the full traceback goes to `logs/ops/<date>.jsonl`.
