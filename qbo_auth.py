"""
Shared QuickBooks Online auth helper.

One-time setup required:
  Add http://localhost:8080/callback as an allowed redirect URI in your
  Intuit Developer app at https://developer.intuit.com/

Environment variables:
  Required: QBO_CLIENT_ID, QBO_CLIENT_SECRET
  Optional: QBO_TOKEN_FILE — override token storage path (default: ~/.qbo_tokens.json).

--- PRODUCTION NOTES ---
This module is written for a local dev / tutorial context. For production, the
entire auth strategy needs to change:

1. CREDENTIALS: Load QBO_CLIENT_ID and QBO_CLIENT_SECRET from a secrets manager
   (e.g. AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) rather than
   plain environment variables. Rotating them should not require a redeploy.

2. REDIRECT URI: Replace localhost:8080 with a real HTTPS endpoint your server
   controls (e.g. https://yourapp.com/oauth/callback). Register that URI in the
   Intuit Developer console instead of the localhost one.

3. TOKEN STORAGE: The flat JSON file approach (qbo_tokens.json) is dev-only.
   In production, store tokens in an encrypted secrets store or a database with
   encryption at rest. Never write tokens to the filesystem in a server environment.

4. MULTI-TENANCY: This code assumes a single QuickBooks company (one realm_id).
   If your service connects to multiple QBO accounts, you need per-account token
   storage keyed by realm_id, and a way to route requests to the right credentials.

5. ENVIRONMENT: Change environment="sandbox" to environment="production" in both
   the AuthClient and QuickBooks constructors below before going live.

6. BROWSER FLOW: The _browser_flow() function below is completely unusable in a
   server/CI environment — it requires a human to open a browser. In production,
   the initial OAuth grant is done once through a proper web redirect flow (user
   visits /oauth/authorize, gets redirected to Intuit, comes back to your callback
   route). After the one-time grant, only the refresh loop is needed.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from intuitlib.exceptions import AuthClientError
from quickbooks import QuickBooks
from quickbooks.exceptions import QuickbooksException

# PRODUCTION: Replace with your real HTTPS callback URL registered in the Intuit
# Developer console (e.g. "https://yourapp.com/oauth/callback"). Using localhost
# here means this flow only works on a developer's machine.
_REDIRECT_URI = "http://localhost:8080/callback"


def token_path() -> Path:
    """Return the resolved token file path (public — useful for callers that need to display or check the path)."""
    if "QBO_TOKEN_FILE" in os.environ:
        return Path(os.environ["QBO_TOKEN_FILE"])
    return Path.home() / ".qbo_tokens.json"


def _load_tokens(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_tokens(path: Path, tokens: dict) -> None:
    # PRODUCTION: Don't write tokens to disk. Store access_token, refresh_token,
    # and realm_id in an encrypted secrets store (e.g. AWS Secrets Manager) or an
    # encrypted database column. The 0o600 chmod is a bare-minimum local safeguard
    # that has no equivalent protection on a shared server or container filesystem.
    path.write_text(json.dumps(tokens, indent=2))
    os.chmod(path, 0o600)


# PRODUCTION: This entire function is dev-only. A production service cannot open
# a browser or spin up a localhost HTTP server. Replace this with a proper web
# OAuth redirect flow:
#
#   1. /oauth/authorize route: call auth_client.get_authorization_url() and
#      redirect the user's browser to the returned URL.
#   2. /oauth/callback route: Intuit redirects back here with ?code=...&realmId=...
#      Call auth_client.get_bearer_token(code, realm_id=realm_id), then persist
#      the resulting tokens (see _save_tokens note above).
#
# The one-time browser authorization only needs to happen once per QBO account.
# After that, only the refresh loop in get_qb_client() is needed for ongoing access.
def _browser_flow(auth_client: AuthClient) -> dict:
    result = {}
    state = secrets.token_urlsafe(16)

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            error = params.get("error", [None])[0]
            if error:
                result["error"] = error
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h1>Authorization denied. You can close this tab.</h1>")
                return
            if params.get("state", [None])[0] != state:
                result["error"] = "state_mismatch"
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h1>Invalid state parameter. Authorization aborted.</h1>")
                return
            result["code"] = params.get("code", [None])[0]
            result["realm_id"] = params.get("realmId", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorized! You can close this tab.</h1>")

        def log_message(self, format, *args):
            pass  # suppress request logs

    try:
        server = HTTPServer(("localhost", 8080), _Handler)
    except OSError:
        # Likely a stale process from a previous run — try to free the port and retry once.
        print("  Port 8080 in use. Attempting to free it...")
        subprocess.run("lsof -ti:8080 | xargs kill -9", shell=True, capture_output=True)
        time.sleep(1)
        try:
            server = HTTPServer(("localhost", 8080), _Handler)
        except OSError:
            print("Error: port 8080 is still in use. Stop the other process and try again.")
            sys.exit(1)

    server.socket.settimeout(120)
    auth_url = auth_client.get_authorization_url([Scopes.ACCOUNTING], state_token=state)
    print(f"\n  Opening browser to authorize...")
    try:
        opened = webbrowser.open(auth_url)
    except Exception:
        opened = False
    if not opened:
        print(f"  Could not open browser automatically. Open this URL manually:\n\n  {auth_url}\n")
    print("  Waiting for authorization (120s timeout)...")

    try:
        server.handle_request()
    except socket.timeout:
        pass  # handle_request() may return silently on timeout instead of raising

    if not result:
        print("Error: no callback received within 120 seconds. Did you authorize in the browser?")
        sys.exit(1)

    if result.get("error"):
        print(f"Error: authorization failed ({result['error']}). Re-run setup to try again.")
        sys.exit(1)

    auth_client.get_bearer_token(result["code"], realm_id=result["realm_id"])
    return {
        "access_token": auth_client.access_token,
        "refresh_token": auth_client.refresh_token,
        "realm_id": result["realm_id"],
    }


def get_qb_client() -> QuickBooks:
    """Return an authenticated QuickBooks client, handling all token management.

    On first run: opens a browser for OAuth authorization and saves tokens.
    On subsequent runs: refreshes the access token silently from the saved file.
    If the refresh token is expired or revoked: re-runs the browser flow.
    """
    path = token_path()
    auth_client = AuthClient(
        # PRODUCTION: Load credentials from a secrets manager rather than env vars.
        # e.g. boto3.client("secretsmanager").get_secret_value(SecretId="qbo/client_id")
        client_id=os.environ["QBO_CLIENT_ID"],
        client_secret=os.environ["QBO_CLIENT_SECRET"],
        redirect_uri=_REDIRECT_URI,
        # PRODUCTION: Change to environment="production" when using live QBO accounts.
        environment="sandbox",
    )

    tokens = _load_tokens(path)

    if tokens.get("refresh_token"):
        try:
            auth_client.refresh(refresh_token=tokens["refresh_token"])
            tokens["access_token"] = auth_client.access_token
            tokens["refresh_token"] = auth_client.refresh_token
            _save_tokens(path, tokens)
        except (AuthClientError, QuickbooksException):
            print("  Warning: saved tokens are invalid or expired. Re-authorizing...")
            tokens = {}

    if not tokens:
        # PRODUCTION: This branch should never be hit at runtime. Initial authorization
        # must be completed out-of-band (via your /oauth/callback web route) before
        # deploying the service. If tokens are missing at startup, raise an exception
        # or alert rather than trying to open a browser.
        tokens = _browser_flow(auth_client)
        _save_tokens(path, tokens)

    return QuickBooks(
        auth_client=auth_client,
        refresh_token=auth_client.refresh_token,
        # PRODUCTION: If handling multiple QBO accounts, realm_id must come from
        # per-request context (e.g. a database lookup by user/org), not a single
        # stored value.
        company_id=tokens["realm_id"],
        minorversion=75,
        # PRODUCTION: Change to environment="production" to match the AuthClient above.
        environment="sandbox",
    )
