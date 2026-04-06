"""
Extracts invoice data from a local PDF using Sensible and creates a bill in QuickBooks Online.

Environment: macOS, Windows, Linux desktop, or WSL with a web browser accessible.
See qbo_auth.py for production considerations.

Note for LLM agents: this script is interactive — it opens a browser for OAuth. Do NOT
run it via a Bash tool. In Claude Code, use the ! prefix instead (e.g. ! python invoice_to_quickbooks.py)
so the auth URL appears in the conversation where the user can access it.
"""

import os
from datetime import datetime
from pathlib import Path

from sensibleapi import SensibleSDK
from quickbooks.objects.account import Account
from quickbooks.objects.bill import Bill
from quickbooks.objects.detailline import (
    AccountBasedExpenseLine,
    AccountBasedExpenseLineDetail,
)
from quickbooks.objects.vendor import Vendor
from quickbooks.objects.base import Ref
from quickbooks.exceptions import QuickbooksException
from qbo_auth import get_qb_client

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_VENDOR = "Unmatched - Review Required"

PREFERRED_ACCOUNT_NAMES = [
    "Uncategorized Expense",
    "Miscellaneous",
    "Miscellaneous Expense",
    "Ask My Accountant",
    "Other Miscellaneous Expense",
]

FALLBACK_ACCOUNT_NAME = "Invoice Imports - Needs Review"

# ── Helpers ────────────────────────────────────────────────────────────────────


def get_field(parsed, key):
    """Extract a field value from a Sensible parsed_document result.

    Sensible fields are dicts with at least "value" and "type" keys. Returns None if
    the field dict itself is missing or falsy (e.g. None, False, or an empty dict);
    inner values of 0 or False are returned correctly. Does not raise.
    """
    return (parsed.get(key) or {}).get("value")


def make_ref(id, name):
    """Build a QuickBooks Ref object linking to another record by ID and display name.

    Ref is python-quickbooks's generic foreign key. value holds the record ID,
    name holds the display name shown in QBO. Used when pointing a Bill at a
    Vendor or an ExpenseLine at an Account.
    """
    ref = Ref()
    ref.value = id
    ref.name = name
    return ref


def parse_amount(value):
    """Convert a currency value to a float, stripping commas. Returns 0.0 if value is None.

    Sensible may return amounts as formatted strings like "1,234.56". Commas are
    stripped before float conversion. None becomes 0.0; a numeric 0 is converted
    correctly via the float() path. Raises ValueError if the string is not a valid
    float after comma-stripping (e.g. a bare currency symbol or a label string like
    "N/A" returned from a low-confidence extraction).
    """
    return float(str(value).replace(",", "")) if value is not None else 0.0


def get_default_expense_account(qb_client):
    """Find or create a catch-all expense account for unclassified invoice line items.

    Checks existing QBO Expense and "Other Expense" accounts against PREFERRED_ACCOUNT_NAMES
    in priority order (case-insensitive). Returns the first match as a Ref. If none
    of the preferred accounts exist, attempts to create a new account named
    FALLBACK_ACCOUNT_NAME (AccountSubType: OtherMiscellaneousServiceCost). Raises
    QuickbooksException if the fallback account cannot be created.
    """
    accounts = Account.filter(AccountType="Expense", qb=qb_client)
    accounts += Account.filter(AccountType="Other Expense", qb=qb_client)

    by_name = {a.Name.lower(): a for a in accounts}

    for name in PREFERRED_ACCOUNT_NAMES:
        match = by_name.get(name.lower())
        if match:
            print(f"  ✓ Using existing account: {match.Name!r} (ID {match.Id})")
            return make_ref(match.Id, match.Name)

    new_acct = Account()
    new_acct.Name = FALLBACK_ACCOUNT_NAME
    new_acct.AccountType = "Expense"
    new_acct.AccountSubType = "OtherMiscellaneousServiceCost"
    try:
        new_acct.save(qb=qb_client)
    except QuickbooksException as e:
        print(f"  Error: could not create fallback account {FALLBACK_ACCOUNT_NAME!r}: {e}")
        raise

    print(f"  ✓ Created new account: {new_acct.Name!r} (ID {new_acct.Id})")
    return make_ref(new_acct.Id, new_acct.Name)


# ── Sensible extraction ────────────────────────────────────────────────────────
# This script pulls from a local file, but Sensible supports other input methods:
# - extract(url=...) or extract(file=...) for remote/in-memory documents
# - Webhook-driven flow: if you configure automatic extractions (e.g. via Sensible's
#   email processor), Sensible POSTs parsed_document to your endpoint — no extract()
#   call needed. See: https://github.com/sensible-hq/sensible-api-py
#   and https://docs.sensible.so/docs/getting-started-email

invoice_path = Path(__file__).resolve().parent / "invoice_sample.pdf"

print("\n[1/5] Extracting invoice with Sensible ...")
sensible = SensibleSDK(os.environ["SENSIBLE_API_KEY"])

request = sensible.extract(
    path=str(invoice_path),
    document_type="invoices",
    environment="production",
)
result = sensible.wait_for(request)

parsed = result["parsed_document"]

invoice_date   = get_field(parsed, "Invoice date")
due_date       = get_field(parsed, "Invoice due date")
invoice_number = get_field(parsed, "Invoice number")
vendor_name    = get_field(parsed, "Vendor name")
total_amount   = get_field(parsed, "Total amount of invoice")
line_items     = parsed.get("line_items", [])

print(f"  ✓ Vendor: {vendor_name or '(not found)'}")
print(f"  ✓ Invoice #: {invoice_number or '(not found)'}")
print(f"  ✓ Total: {total_amount or '(not found)'}")
print(f"  ✓ Line items: {len(line_items)}")

if not vendor_name:
    print(f"  ⚠ Vendor name not found. Using default: {DEFAULT_VENDOR}")
    vendor_name = DEFAULT_VENDOR

# ── QuickBooks Online auth ─────────────────────────────────────────────────────

print("\n[2/5] Authenticating with QuickBooks Online ...")
qb_client = get_qb_client()
print("  ✓ Connected.")

# ── Find or create a default expense account ──────────────────────────────────

print("\n[3/5] Resolving expense account ...")
expense_account_ref = get_default_expense_account(qb_client)

# ── Find or create vendor ──────────────────────────────────────────────────────

print("\n[4/5] Resolving vendor ...")
vendors = Vendor.filter(DisplayName=vendor_name, qb=qb_client)
if vendors:
    vendor_ref = make_ref(vendors[0].Id, vendors[0].DisplayName)
    print(f"  ✓ Found existing vendor: {vendor_ref.name} (ID {vendor_ref.value})")
else:
    new_vendor = Vendor()
    new_vendor.DisplayName = vendor_name
    new_vendor.save(qb=qb_client)
    vendor_ref = make_ref(new_vendor.Id, new_vendor.DisplayName)
    print(f"  ✓ Created new vendor: {vendor_ref.name} (ID {vendor_ref.value})")

# ── Build bill ─────────────────────────────────────────────────────────────────

print("\n[5/5] Creating bill in QuickBooks ...")

bill = Bill()
bill.TxnDate = str(invoice_date) if invoice_date else None
bill.DueDate = str(due_date) if due_date else None
bill.DocNumber = str(invoice_number) if invoice_number else None
bill.VendorRef = vendor_ref
bill.Line = []

if not line_items:
    detail = AccountBasedExpenseLineDetail()
    detail.AccountRef = expense_account_ref

    line = AccountBasedExpenseLine()
    line.Amount = parse_amount(total_amount)
    line.Description = "Invoice total (line items not extracted)"
    line.AccountBasedExpenseLineDetail = detail
    bill.Line.append(line)
    print(f"  • 1 summary line (no line items extracted): ${line.Amount:,.2f}")
else:
    for i, item in enumerate(line_items, 1):
        detail = AccountBasedExpenseLineDetail()
        detail.AccountRef = expense_account_ref

        amount = parse_amount((item.get("item_total") or {}).get("value"))
        description = (item.get("item_description") or {}).get("value", "")

        line = AccountBasedExpenseLine()
        line.Amount = amount
        line.Description = description
        line.AccountBasedExpenseLineDetail = detail
        bill.Line.append(line)
        print(f"  • Line {i}: {description or '(no description)'} — ${amount:,.2f}")

saved = bill.save(qb=qb_client)
bill_url = f"https://app.sandbox.qbo.intuit.com/app/bill?txnId={saved.Id}"  # PRODUCTION: Change to https://app.qbo.intuit.com/app/bill?txnId={saved.Id}

print(f"\n{'='*60}")
print(f"  ✓ Bill created successfully!")
print(f"    ID:     {saved.Id}")
print(f"    Vendor: {vendor_ref.name}")
print(f"    Date:   {saved.TxnDate}")
print(f"    Lines:  {len(bill.Line)}")
print(f"    View:   {bill_url}")
print(f"{'='*60}")

log_path = Path(__file__).resolve().parent / "logs.txt"
with open(log_path, "a") as f:
    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  bill {saved.Id}  {vendor_ref.name}  {saved.TxnDate}  {bill_url}\n")
