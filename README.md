# Sensible → QuickBooks Online Integration

> **This is a proof-of-concept tutorial.** It is not intended for production use. The OAuth flow, token storage, and credential handling are intentionally simplified for local development. See the inline `PRODUCTION:` comments in `qbo_auth.py` for a summary of what would need to change before deploying this anywhere real.

Extracts data from a vendor invoice PDF using Sensible, then creates a bill in QuickBooks Online. 

For information about running these scripts, including prerequisites, see [Integrate with Quickbooks using Python](https://docs.sensible.so/docs/).


