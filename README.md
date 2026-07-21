# Email Vault Panel

Phase **1**: manage catch-all aliases (generate / list / search / archive).  
Phase **2**: Gmail vault OAuth + find mail per alias (`deliveredto:` / `to:`).

## Run (dev)

```bash
cd /opt/data/projects/email-vault-panel
uv venv .venv && uv pip install -e .
export DATA_DIR=/tmp/email-vault-data DEFAULT_DOMAIN=sukiliar.pro
uv run uvicorn email_vault_panel.main:app --host 127.0.0.1 --port 8787
```

## Gmail setup (once)

1. Google Cloud Console → enable **Gmail API**
2. OAuth consent (External / Testing OK) + scope `gmail.readonly`
3. Create OAuth client:
   - **Web** recommended for Umbrel (add redirect URI from app UI), or Desktop
4. In Email Vault UI → paste client JSON → **Save client secrets**
5. **Connect Gmail** → login vault Gmail → done

Token stored at `$DATA_DIR/secrets/gmail_token.json`.

Optional: set `PUBLIC_BASE_URL=https://umbrel…/apps/…` if OAuth redirect host is wrong behind proxy.

## API (phase 2 extras)

| Method | Path |
|--------|------|
| GET | `/api/gmail/status` |
| POST | `/api/gmail/client-secrets` |
| GET | `/api/gmail/auth/start` |
| GET | `/api/gmail/auth/callback` |
| POST | `/api/gmail/disconnect` |
| GET | `/api/gmail/messages?alias=` |
| GET | `/api/gmail/messages/{id}` |

## Docker

```bash
docker build -t ghcr.io/azrim/email-vault-panel:0.2.0 .
docker push ghcr.io/azrim/email-vault-panel:0.2.0
```

Umbrel packaging: `azrim/azrim_umbrel` → `azrim-umbrel-email-vault`.
