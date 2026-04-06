"""
First-time QuickBooks Online setup.

Run this from a regular terminal shell — NOT from inside an AI tool like Claude Code,
which does not provide an interactive session and will time out waiting for the OAuth
callback. Open a terminal, navigate to the repo, and run:

    cd ~/GitHub/sensible-quickbooks-py
    python quickbooks-setup.py

Required env vars: QBO_CLIENT_ID, QBO_CLIENT_SECRET
One-time Intuit console step: add http://localhost:8080/callback as a redirect URI.
Tokens are saved to ~/.qbo_tokens.json (outside the repo) with 0600 permissions.

PRODUCTION: This script has no equivalent in a production deployment. The
initial OAuth grant is handled through your web app's /oauth/authorize and
/oauth/callback routes. Run this only in local dev to bootstrap a token file
for manual testing. Do not run it as part of any automated deployment pipeline.
"""

from qbo_auth import get_qb_client, token_path

print("Connecting to QuickBooks Online...")
get_qb_client()
print(f"Setup complete. Tokens saved to {token_path()}")
