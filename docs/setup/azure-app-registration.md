# Azure App Registration

m365ctl talks to Microsoft Graph via an Entra (Azure AD) app registration. You
need one app that supports **both** flows:

- **Delegated** (user sign-in, device-code) — for most everyday verbs.
- **Application** (app-only, client-credential + certificate) — for verbs that
  cross mailboxes / drives, e.g. tenant-wide audit.

## Step-by-step

1. Open the Entra admin center: https://entra.microsoft.com.
2. **Applications → App registrations → New registration.**
3. Fill in:
   - **Name:** `m365ctl` (or whatever you like — this is tenant-internal).
   - **Supported account types:** *Accounts in this organizational directory only (Single tenant)*.
   - **Redirect URI:** leave blank. Device-code flow does not use one.
4. Click **Register**.

## Capture the IDs

On the app's **Overview** page copy:

- **Directory (tenant) ID** → `tenant_id` in `config.toml`.
- **Application (client) ID** → `client_id` in `config.toml`.

## API permissions

Go to **API permissions → Add a permission → Microsoft Graph**. Add these:

**Delegated** (user-signed-in flows):

- `Files.ReadWrite.All`
- `Sites.ReadWrite.All`
- `User.Read`
- `Mail.ReadWrite`
- `Mail.Send`
- `MailboxSettings.ReadWrite`

**Application** (app-only flows):

- `Files.ReadWrite.All`
- `Sites.ReadWrite.All`
- `Mail.ReadWrite`
- `Mail.Send`
- `MailboxSettings.ReadWrite`

After adding, click **Grant admin consent for <your tenant>**. You need a
Global Administrator (or Privileged Role Administrator / Cloud App
Administrator) account for this step. If you are not an admin, ask one to
grant consent once — it is a one-time action.

## Certificate

App-only flows need a certificate, not a secret. Generate and upload it per
[certificate-auth.md](certificate-auth.md).

## Reference

- Microsoft Learn quickstart: https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app
