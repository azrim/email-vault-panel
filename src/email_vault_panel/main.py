"""Email Vault Panel — aliases + Gmail vault finder."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from email_vault_panel import __version__
from email_vault_panel.db import STATUSES, Vault
from email_vault_panel.gmail import GmailError, GmailVault

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("VAULT_DB", str(DATA_DIR / "vault.db")))
DEFAULT_DOMAIN = os.environ.get("DEFAULT_DOMAIN", "sukiliar.pro")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8787"))
# Optional fixed public origin for OAuth redirect (Umbrel app URL)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Email Vault", version=__version__)
_vault: Vault | None = None
_gmail: GmailVault | None = None


def get_vault() -> Vault:
    global _vault
    if _vault is None:
        _vault = Vault(DB_PATH)
    return _vault


def get_gmail() -> GmailVault:
    global _gmail
    if _gmail is None:
        _gmail = GmailVault(DATA_DIR)
    return _gmail


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class GenerateBody(BaseModel):
    count: int = Field(5, ge=1, le=500)
    domain: str | None = None
    prefix: str = "x"
    style: str = Field("random", pattern="^(random|word|seq)$")
    notes: str = ""
    status: str = "fresh"


class PatchBody(BaseModel):
    status: str | None = None
    notes: str | None = None


class ImportBody(BaseModel):
    path: str


class ClientSecretsBody(BaseModel):
    """Paste Google OAuth client JSON (Desktop or Web)."""

    client_secrets: dict[str, Any] | str


class OAuthCodeBody(BaseModel):
    """Paste the one-time code Google shows after Desktop/OOB consent."""

    code: str
    state: str | None = None


def _request_base(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    # Honor reverse-proxy headers (Umbrel app_proxy)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


@app.get("/api/health")
def health() -> dict[str, Any]:
    gstat = get_gmail().status()
    return {
        "ok": True,
        "phase": 2,
        "version": __version__,
        "gmail": gstat.get("connected", False),
        "gmail_status": gstat,
        "domain": DEFAULT_DOMAIN,
        "db": str(DB_PATH),
        "counts": get_vault().counts(),
    }


@app.get("/api/aliases")
def list_aliases(
    q: str = "",
    status: str | None = None,
    include_archived: bool = False,
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if status and status not in STATUSES:
        raise HTTPException(400, f"invalid status; want one of {sorted(STATUSES)}")
    items = get_vault().list_aliases(
        q=q,
        status=status,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "counts": get_vault().counts()}


@app.post("/api/aliases/generate")
def generate_aliases(body: GenerateBody) -> dict[str, Any]:
    try:
        created = get_vault().generate(
            count=body.count,
            domain=body.domain or DEFAULT_DOMAIN,
            prefix=body.prefix,
            style=body.style,
            notes=body.notes,
            status=body.status,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"created": created, "counts": get_vault().counts()}


@app.patch("/api/aliases/{alias_id}")
def patch_alias(alias_id: int, body: PatchBody) -> dict[str, Any]:
    try:
        row = get_vault().patch(alias_id, status=body.status, notes=body.notes)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not row:
        raise HTTPException(404, "not found")
    return {"item": row, "counts": get_vault().counts()}


@app.post("/api/aliases/{alias_id}/archive")
def archive_alias(alias_id: int) -> dict[str, Any]:
    row = get_vault().archive(alias_id)
    if not row:
        raise HTTPException(404, "not found")
    return {"item": row, "counts": get_vault().counts()}


@app.post("/api/aliases/{alias_id}/unarchive")
def unarchive_alias(alias_id: int) -> dict[str, Any]:
    row = get_vault().unarchive(alias_id)
    if not row:
        raise HTTPException(404, "not found")
    return {"item": row, "counts": get_vault().counts()}


@app.post("/api/import/csv")
def import_csv(body: ImportBody) -> dict[str, Any]:
    try:
        result = get_vault().import_csv(Path(body.path))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return {**result, "counts": get_vault().counts()}


# --- Gmail ---


@app.get("/api/gmail/status")
def gmail_status() -> dict[str, Any]:
    return get_gmail().status()


@app.post("/api/gmail/client-secrets")
def gmail_upload_secrets(body: ClientSecretsBody) -> dict[str, Any]:
    try:
        get_gmail().save_client_secrets(body.client_secrets)
    except GmailError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"invalid JSON: {e}") from e
    return {"ok": True, "status": get_gmail().status()}


@app.get("/api/gmail/auth/start")
def gmail_auth_start(request: Request) -> dict[str, Any]:
    """Start OAuth. Desktop clients use OOB (paste code); Web can use callback."""
    # Prefer OOB so Umbrel .local / LAN IP is not sent to Google as redirect
    try:
        kind = "unknown"
        if get_gmail().has_client_secrets():
            cfg = get_gmail()._client_config()
            kind = "installed" if "installed" in cfg else "web" if "web" in cfg else "unknown"
        if kind == "web":
            redirect_uri = f"{_request_base(request)}/api/gmail/auth/callback"
            data = get_gmail().authorization_url(redirect_uri)
        else:
            data = get_gmail().authorization_url(None)
    except GmailError as e:
        raise HTTPException(400, str(e)) from e
    return data


@app.post("/api/gmail/auth/code")
def gmail_auth_code(body: OAuthCodeBody) -> dict[str, Any]:
    """Exchange pasted OOB / Desktop code for tokens (no public redirect needed)."""
    if not body.code.strip():
        raise HTTPException(400, "code required")
    try:
        status = get_gmail().finish_oauth(body.code, body.state)
    except GmailError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "status": status}


@app.get("/api/gmail/auth/callback")
def gmail_auth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        return RedirectResponse(f"/?gmail_error={error}", status_code=302)
    if not code:
        return RedirectResponse("/?gmail_error=missing_code", status_code=302)
    try:
        get_gmail().finish_oauth(code, state)
    except GmailError as e:
        return RedirectResponse(
            f"/?gmail_error={quote(str(e), safe='')}", status_code=302
        )
    return RedirectResponse("/?gmail=connected", status_code=302)


@app.post("/api/gmail/disconnect")
def gmail_disconnect() -> dict[str, Any]:
    get_gmail().disconnect()
    return {"ok": True, "status": get_gmail().status()}


@app.get("/api/gmail/messages")
def gmail_messages(
    alias: str = Query(..., min_length=3),
    max_results: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    try:
        items = get_gmail().search_for_alias(alias, max_results=max_results)
    except GmailError as e:
        raise HTTPException(400, str(e)) from e
    return {"alias": alias, "items": items}


@app.get("/api/gmail/messages/{message_id}")
def gmail_message_detail(message_id: str) -> dict[str, Any]:
    try:
        return get_gmail().get_message_body(message_id)
    except GmailError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(500, "UI missing")
    return FileResponse(index_path)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "email_vault_panel.main:app",
        host=HOST,
        port=PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    run()
