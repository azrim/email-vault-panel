"""Gmail vault client — OAuth + search by delivered-to alias."""
from __future__ import annotations

import base64
import json
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_SECRETS_NAME = "gmail_client_secrets.json"
TOKEN_NAME = "gmail_token.json"


class GmailError(Exception):
    pass


class GmailVault:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.secrets_dir = self.data_dir / "secrets"
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.client_secrets_path = self.secrets_dir / CLIENT_SECRETS_NAME
        self.token_path = self.secrets_dir / TOKEN_NAME

    def has_client_secrets(self) -> bool:
        return self.client_secrets_path.is_file()

    def has_token(self) -> bool:
        return self.token_path.is_file()

    def save_client_secrets(self, raw: dict[str, Any] | str | bytes) -> None:
        if isinstance(raw, (str, bytes)):
            data = json.loads(raw)
        else:
            data = raw
        if "installed" not in data and "web" not in data:
            raise GmailError(
                "Invalid OAuth client JSON — need Google 'Desktop' or 'Web' client secrets"
            )
        self.client_secrets_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        self.client_secrets_path.chmod(0o600)

    def status(self) -> dict[str, Any]:
        connected = False
        email: str | None = None
        err: str | None = None
        if self.has_token():
            try:
                svc = self.service()
                profile = svc.users().getProfile(userId="me").execute()
                email = profile.get("emailAddress")
                connected = True
            except Exception as e:  # noqa: BLE001 — surface to UI
                err = str(e)
        return {
            "client_secrets": self.has_client_secrets(),
            "token_file": self.has_token(),
            "connected": connected,
            "email": email,
            "error": err,
            "scopes": SCOPES,
        }

    def _load_creds(self) -> Credentials | None:
        if not self.token_path.is_file():
            return None
        return Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

    def _save_creds(self, creds: Credentials) -> None:
        self.token_path.write_text(creds.to_json(), encoding="utf-8")
        self.token_path.chmod(0o600)

    def service(self):
        creds = self._load_creds()
        if not creds:
            raise GmailError("Gmail not connected — run OAuth first")
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_creds(creds)
            else:
                raise GmailError("Gmail token invalid — reconnect")
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _client_config(self) -> dict[str, Any]:
        if not self.has_client_secrets():
            raise GmailError(
                "Missing client secrets — upload Google OAuth JSON first "
                f"(save as {self.client_secrets_path})"
            )
        return json.loads(self.client_secrets_path.read_text(encoding="utf-8"))

    def _flow(self, redirect_uri: str) -> Flow:
        cfg = self._client_config()
        # Normalize: Flow.from_client_config wants installed or web key
        flow = Flow.from_client_config(cfg, scopes=SCOPES, redirect_uri=redirect_uri)
        return flow

    def authorization_url(self, redirect_uri: str) -> tuple[str, str]:
        flow = self._flow(redirect_uri)
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        # persist state + redirect for callback
        state_path = self.secrets_dir / "oauth_state.json"
        state_path.write_text(
            json.dumps({"state": state, "redirect_uri": redirect_uri}),
            encoding="utf-8",
        )
        state_path.chmod(0o600)
        return auth_url, state

    def finish_oauth(self, code: str, state: str | None = None) -> dict[str, Any]:
        state_path = self.secrets_dir / "oauth_state.json"
        if not state_path.is_file():
            raise GmailError("OAuth state missing — start auth again")
        meta = json.loads(state_path.read_text(encoding="utf-8"))
        if state and meta.get("state") and state != meta["state"]:
            raise GmailError("OAuth state mismatch")
        redirect_uri = meta["redirect_uri"]
        flow = self._flow(redirect_uri)
        flow.fetch_token(code=code)
        creds = flow.credentials
        self._save_creds(creds)
        try:
            state_path.unlink(missing_ok=True)
        except TypeError:
            if state_path.exists():
                state_path.unlink()
        return self.status()

    def disconnect(self) -> None:
        if self.token_path.is_file():
            self.token_path.unlink()

    def search_for_alias(
        self, alias: str, *, max_results: int = 20
    ) -> list[dict[str, Any]]:
        alias = alias.strip().lower()
        if "@" not in alias:
            raise GmailError("invalid alias")
        # CF routing: try deliveredto first, then to:
        queries = [
            f"deliveredto:{alias}",
            f"to:{alias}",
            f'"{alias}"',
        ]
        svc = self.service()
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for q in queries:
            try:
                resp = (
                    svc.users()
                    .messages()
                    .list(userId="me", q=q, maxResults=max_results)
                    .execute()
                )
            except HttpError as e:
                raise GmailError(f"Gmail search failed: {e}") from e
            for m in resp.get("messages") or []:
                mid = m["id"]
                if mid in seen:
                    continue
                seen.add(mid)
                out.append(self.get_message_summary(mid))
                if len(out) >= max_results:
                    return out
            if out:
                # first query that returns hits wins (prefer deliveredto)
                break
        return out

    def get_message_summary(self, message_id: str) -> dict[str, Any]:
        svc = self.service()
        msg = (
            svc.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date", "Delivered-To"],
            )
            .execute()
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in (msg.get("payload") or {}).get("headers") or []
        }
        return {
            "id": message_id,
            "thread_id": msg.get("threadId"),
            "snippet": msg.get("snippet") or "",
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "delivered_to": headers.get("delivered-to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "internal_date": msg.get("internalDate"),
        }

    def get_message_body(self, message_id: str) -> dict[str, Any]:
        svc = self.service()
        msg = (
            svc.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        summary = self.get_message_summary(message_id)
        text, html = _extract_bodies(msg.get("payload") or {})
        # prefer text; strip html lightly if only html
        body = text.strip() if text.strip() else _html_to_text(html)
        codes = _guess_codes(body + "\n" + (summary.get("snippet") or ""))
        summary["body"] = body[:50_000]
        summary["codes"] = codes
        return summary


def _extract_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = (part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        data = body.get("data")
        if data:
            try:
                raw = base64.urlsafe_b64decode(data + "===").decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                raw = ""
            if mime == "text/plain":
                text_parts.append(raw)
            elif mime == "text/html":
                html_parts.append(raw)
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    return "\n".join(text_parts), "\n".join(html_parts)


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    # crude strip tags
    t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<br\s*/?>", "\n", t)
    t = re.sub(r"(?s)</p>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def _guess_codes(text: str) -> list[str]:
    """Heuristic OTP / verify codes for quick UI highlight."""
    found: list[str] = []
    for m in re.finditer(r"\b(\d{4,8})\b", text):
        code = m.group(1)
        if code not in found:
            found.append(code)
    for m in re.finditer(r"\b([A-Z0-9]{6,10})\b", text):
        code = m.group(1)
        if code.isalpha():
            continue
        if code not in found:
            found.append(code)
    return found[:8]
