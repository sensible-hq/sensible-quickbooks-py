# Sensible → QuickBooks Online Integration

> **This is a proof-of-concept tutorial.** It is not intended for production use. The OAuth flow, token storage, and credential handling are intentionally simplified for local development. See the inline `PRODUCTION:` comments in `qbo_auth.py` for a summary of what would need to change before deploying this anywhere real.

Extracts data from a vendor invoice PDF using Sensible, then creates a bill in QuickBooks Online.

---

## Prerequisites

### 1. Install dependencies

```bash
pip install sensible-sdk python-quickbooks intuitlib
```

### 2. Set environment variables

```bash
export SENSIBLE_API_KEY=your_sensible_api_key
export QBO_CLIENT_ID=your_intuit_app_client_id
export QBO_CLIENT_SECRET=your_intuit_app_client_secret
```

### 3. Add the localhost callback to your Intuit app (one-time)

1. Go to [developer.intuit.com](https://developer.intuit.com/) and sign in.
2. Click **Dashboard** in the top nav, then select your app. If you don't have one yet, click **+ Create an app**, choose **QuickBooks Online and Payments**, and give it a name.
3. In your app, go to the **Settings** tab.
4. Under **Redirect URIs**, click **Add URI**.
5. Enter `http://localhost:8080/callback` and click **Save**.

That's it — this redirect URI is what lets the setup script catch the OAuth callback automatically instead of requiring you to copy a code manually.

---

## First-time setup

Run the setup script to authorize the app and save your QuickBooks tokens:

```bash
cd scripts/quickbooks_sensible
python quickbooks-setup.py
```

This opens a browser window asking you to sign in to QuickBooks Online and authorize the app. Once you click **Connect**, the tokens are saved to `~/.qbo_tokens.json` and you're done. You won't need to do this again unless the refresh token expires (after 100 days of inactivity).

---

## Run the integration

```bash
python invoice_to_quickbooks.py
```

This will:

1. Extract `invoice_sample.pdf` using Sensible (`invoices` document type)
2. Authenticate with QuickBooks using saved tokens (auto-refreshes silently)
3. Find or create an expense account
4. Find or create a vendor
5. Create a bill in QuickBooks with the extracted line items

---

## Token storage

Tokens are saved to `~/.qbo_tokens.json` by default (outside the repo, with `0600` permissions). To override the path, set:

```bash
export QBO_TOKEN_FILE=/path/to/tokens.json
```

---

## Re-authorizing

If your tokens expire or are revoked, just run setup again:

```bash
python quickbooks-setup.py
```

The browser flow will trigger automatically.
