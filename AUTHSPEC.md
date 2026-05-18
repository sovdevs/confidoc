Build Phase 3: Zone 1 auth and user settings for Confidoc demo.
Goal:
Predefined Zone 1 users can log in from the Start page, then configure their default processing and server settings without editing files manually.
Keep it secure but simple. No public sign-up.
Core requirements:
1. Predefined users only
- Add users config file, e.g.
  data/auth/users.json
- No sign-up route.
- Passwords must not be plaintext.
- Store password hashes using passlib/bcrypt or argon2.
- Include a CLI helper to create/update demo users:
  uv run python -m confidoc.auth create-user <username>
2. Session auth
- Add login form on Start page.
- Add logout button.
- Use secure HTTP-only session cookie.
- Session expiry default: 8 hours.
- Protect Zone 1 routes and gateway/server endpoints.
- Unauthenticated users should be redirected to Start/Login.
3. User settings screen
After login, user can configure:
- default OCR provider
- default OCR model
- default entity/anon LLM provider
- default entity/anon model
- default export/report LLM provider
- default export/report model
- optional API keys / BYOK keys
- default gateway source
- SFTP server details
- local folder source details
4. SFTP source config UI
Allow logged-in user to create/edit an SFTP source that writes to sources.json or a user-specific equivalent.
Fields:
- source id
- label
- type = sftp
- host
- port
- username
- remote_base_path
- auth method: ssh_key or password
- ssh private key path OR uploaded private key
- optional passphrase
- enabled true/false
Important:
- Do not send stored secrets back to frontend.
- When returning source config to frontend, redact:
  password
  private_key
  passphrase
  token
  api_key
- Show only “configured: true/false” for secrets.
5. Secret handling
For Phase 3, acceptable:
- Store secrets encrypted at rest using existing Fernet/MAPPING_KEY mechanism or a new SETTINGS_KEY.
- Do not store raw API keys/passwords in sources.json.
- sources.json should contain non-secret metadata only.
- secrets go into encrypted per-user settings file, e.g.
  data/auth/user_settings/{username}.enc
6. BYOK/default model handling
- User may enter provider/model/key defaults.
- These become defaults for Confidoc OCR/entity/export tasks.
- Runtime override still allowed in existing UI.
- Keys should be stored encrypted if user selects “remember key”.
- If “session only”, keep key in server memory for current session only.
7. UI placement
Start page:
- login form if logged out
- after login show:
  - user name
  - default provider/model summary
  - configured gateway sources
  - buttons:
    - Open Zone 1
    - Settings
    - Logout
Server tab:
- continue showing Local Folder and SFTP sections
- use logged-in user’s saved sources
- allow Test Connection, Scan, Process Next, Process All
8. Security boundaries
- This is demo-grade auth, not enterprise IAM.
- No multi-tenant hardening yet.
- No OAuth/SAML yet.
- No sign-up.
- Do not expose mappings, secrets, or raw uploaded docs across users.
- Add clear TODO comments where production hardening would be needed.
9. Documentation
Update DEV.md with:
- how to create a demo user
- how to log in
- where settings are stored
- how secrets are encrypted/redacted
- how SFTP source config works
- demo flow:
  login → configure SFTP → test connection → upload PDF to remote incoming/ → scan/process → exports pushed back

Important architecture decision:

Use global predefined users + encrypted per-user settings.
Do not let the UI write plaintext secrets into sources.json.

That gives you a believable secure demo without turning this into a full identity-management project.