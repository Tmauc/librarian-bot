#!/usr/bin/env python3
"""One-shot helper: obtain a Google Drive refresh token for librarian-bot.

Prompts for your OAuth *Desktop app* client id/secret, opens a browser to authorize
the ``drive.file`` scope, catches the redirect on localhost, exchanges the code, and
prints the three lines to paste into your ``.env``.

Dependency-free (standard library only), so it runs with any Python:
    python scripts/get_gdrive_token.py
"""

import http.server
import json
import secrets
import socketserver
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/drive.file"
HOST, PORT = "127.0.0.1", 8765
REDIRECT_URI = f"http://{HOST}:{PORT}/"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def main() -> None:
    client_id = input("Google OAuth Client ID: ").strip()
    client_secret = input("Google OAuth Client secret: ").strip()
    if not client_id or not client_secret:
        raise SystemExit("Client id and secret are required.")

    state = secrets.token_urlsafe(16)
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )

    result: dict[str, str | None] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (name imposed by http.server)
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = params.get("code", [None])[0]
            result["state"] = params.get("state", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"librarian-bot: authorized. You can close this tab.")

        def log_message(self, *args):  # silence the default logging
            pass

    print("\nOpening your browser to authorize…")
    print(f"If it does not open, paste this URL manually:\n{auth_url}\n")
    webbrowser.open(auth_url)

    with socketserver.TCPServer((HOST, PORT), Handler) as httpd:
        httpd.handle_request()  # serve exactly the single redirect

    if not result.get("code") or result.get("state") != state:
        raise SystemExit("Authorization failed (no code, or state mismatch).")

    body = urllib.parse.urlencode(
        {
            "code": result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")  # noqa: S310 (fixed https URL)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
            tok = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Token exchange failed ({e.code}): {e.read().decode(errors='ignore')}") from e

    refresh_token = tok.get("refresh_token")
    if not refresh_token:
        raise SystemExit(
            "No refresh_token returned. Revoke this app's access at "
            "https://myaccount.google.com/permissions and run again (prompt=consent)."
        )

    print("\n=== Paste these into your .env ===")
    print(f"GDRIVE_CLIENT_ID={client_id}")
    print(f"GDRIVE_CLIENT_SECRET={client_secret}")
    print(f"GDRIVE_REFRESH_TOKEN={refresh_token}")
    print("GDRIVE_FOLDER_ID=   # the id from your Drive folder's URL (optional)")


if __name__ == "__main__":
    main()
