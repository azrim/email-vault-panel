#!/usr/bin/env python3
"""Desktop Gmail OAuth helper for Email Vault (loopback, not OOB).

Run this on a laptop/PC that has a real browser (not inside Umbrel container).

Google blocks:
  - OOB copy/paste (urn:ietf:wg:oauth:2.0:oob)  → Error 400 invalid_request
  - non-public redirects (umbrel.local, LAN IP) → Invalid Redirect

Desktop clients must use loopback: http://127.0.0.1:PORT/

Usage:
  pip install google-auth-oauthlib google-auth
  python oauth_desktop_helper.py /path/to/client_secret.json
  # browser opens → login vault Gmail → Allow
  # script prints token JSON → paste into Email Vault "Import token"

Optional:
  python oauth_desktop_helper.py client.json --port 8765 --out token.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Gmail Desktop OAuth → token JSON")
    ap.add_argument("client_secrets", type=Path, help="Desktop OAuth client JSON")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", type=Path, default=None, help="also write token file")
    args = ap.parse_args()

    if not args.client_secrets.is_file():
        print(f"missing {args.client_secrets}", file=sys.stderr)
        return 2

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("pip install google-auth-oauthlib google-auth", file=sys.stderr)
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secrets), SCOPES)
    # loopback IP flow (supported); opens local browser
    creds = flow.run_local_server(
        host="127.0.0.1",
        port=args.port,
        authorization_prompt_message="Open this URL if browser did not open:\n{url}",
        success_message="Auth OK — you can close this tab and return to Email Vault.",
        open_browser=True,
    )
    token_json = creds.to_json()
    print("\n=== PASTE THIS TOKEN JSON INTO EMAIL VAULT ===\n")
    print(token_json)
    print("\n=== END TOKEN ===\n")
    if args.out:
        args.out.write_text(token_json, encoding="utf-8")
        print(f"also wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
