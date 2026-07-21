"""Email Vault Panel — phase 1: alias CRUD (Gmail phase 2)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from email_vault_panel.db import STATUSES, Vault

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = Path(os.environ.get("VAULT_DB", str(DATA_DIR / "vault.db")))
DEFAULT_DOMAIN = os.environ.get("DEFAULT_DOMAIN", "sukiliar.pro")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8787"))

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Email Vault", version="0.1.0")
vault = Vault(DB_PATH)

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


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "phase": 1,
        "gmail": False,
        "domain": DEFAULT_DOMAIN,
        "db": str(DB_PATH),
        "counts": vault.counts(),
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
    items = vault.list_aliases(
        q=q,
        status=status,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "counts": vault.counts()}


@app.post("/api/aliases/generate")
def generate_aliases(body: GenerateBody) -> dict[str, Any]:
    try:
        created = vault.generate(
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
    return {"created": created, "counts": vault.counts()}


@app.patch("/api/aliases/{alias_id}")
def patch_alias(alias_id: int, body: PatchBody) -> dict[str, Any]:
    try:
        row = vault.patch(alias_id, status=body.status, notes=body.notes)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not row:
        raise HTTPException(404, "not found")
    return {"item": row, "counts": vault.counts()}


@app.post("/api/aliases/{alias_id}/archive")
def archive_alias(alias_id: int) -> dict[str, Any]:
    row = vault.archive(alias_id)
    if not row:
        raise HTTPException(404, "not found")
    return {"item": row, "counts": vault.counts()}


@app.post("/api/aliases/{alias_id}/unarchive")
def unarchive_alias(alias_id: int) -> dict[str, Any]:
    row = vault.unarchive(alias_id)
    if not row:
        raise HTTPException(404, "not found")
    return {"item": row, "counts": vault.counts()}


@app.post("/api/import/csv")
def import_csv(body: ImportBody) -> dict[str, Any]:
    """Import from a path mounted into the container (ops / migrate)."""
    try:
        result = vault.import_csv(Path(body.path))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return {**result, "counts": vault.counts()}


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
