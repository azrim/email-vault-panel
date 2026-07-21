# Email Vault Panel

Phase **1**: manage catch-all aliases (generate / list / search / archive).  
Phase **2** (later): Gmail vault search by alias.

## Run (dev)

```bash
cd /opt/data/projects/email-vault-panel
uv venv .venv && uv pip install -e .
export DATA_DIR=/tmp/email-vault-data DEFAULT_DOMAIN=sukiliar.pro
uv run uvicorn email_vault_panel.main:app --host 127.0.0.1 --port 8787
```

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/health` | counts + phase |
| GET | `/api/aliases?q=&include_archived=` | list |
| POST | `/api/aliases/generate` | `{count,prefix,style,notes}` |
| POST | `/api/aliases/{id}/archive` | soft archive |
| POST | `/api/aliases/{id}/unarchive` | back to `fresh` |
| PATCH | `/api/aliases/{id}` | status / notes |
| POST | `/api/import/csv` | `{path}` on container FS |

## Docker

```bash
docker build -t ghcr.io/azrim/email-vault-panel:0.1.0 .
```

Umbrel app packaging lives in `azrim/azrim_umbrel` → `azrim-umbrel-email-vault`.

## Data

SQLite at `$DATA_DIR/vault.db` (default `/data/vault.db`).
