"""
Operational MCP workflow prompts for end-to-end Odoo business processes.

Unlike the diagnostic prompts in :mod:`prompts`, these encode multi-step,
often write-bearing procedures. Every write-bearing step routes through the
gated workflow (preview_write -> validate_write -> execute_approved_write)
or chatter_post -- never a direct create/write/unlink -- and each prompt
names a human-escalation checkpoint and the modules it depends on.
"""

from .server_core import mcp


@mcp.prompt(
    name="invoice_approval_chain",
    description="Find, validate, and gated-post draft customer invoices with human checkpoints.",
)
def prompt_invoice_approval_chain(
    journal: str = "",
    date_from: str = "",
    date_to: str = "",
) -> str:
    """Workflow: triage draft invoices and post each one through the write gate."""
    return (
        "Process draft customer invoices safely. Requires the Accounting/Invoicing "
        "module — confirm with business_pack_report(pack='accounting') and stop with "
        "a clear message if it is not installed.\n"
        f"Journal filter: {journal or '<all sales journals>'}\n"
        f"Date range: {date_from or '<unbounded>'} to {date_to or '<unbounded>'}\n\n"
        "Steps:\n"
        "1. search_records on account.move with domain "
        "[('move_type','=','out_invoice'),('state','=','draft')] (plus the journal/"
        "date filters). Use aggregate_records to summarise count and total by partner.\n"
        "2. For each invoice, read_record to check partner_id, invoice_date, "
        "invoice_line_ids amounts, and tax totals. Flag anything missing a customer, "
        "a due date, or with a zero/negative total — do NOT post those.\n"
        "3. Present the validated batch to the human and STOP for explicit go-ahead "
        "before any posting.\n"
        "4. For each approved invoice, post it through the gate: preview_write -> "
        "validate_write -> execute_approved_write on account.move with the posting "
        "action your validate step confirmed. Show the diff summary at each "
        "execute_approved_write and require confirm=true.\n"
        "5. Record an audit note per posted invoice with chatter_post. Never create, "
        "write, or unlink invoices with a direct, ungated call — only through the "
        "gate above."
    )


@mcp.prompt(
    name="po_to_receipt",
    description="Three-way match a purchase order against its receipt and vendor bill; flag discrepancies.",
)
def prompt_po_to_receipt(purchase_order: str) -> str:
    """Workflow: read-only three-way match; report mismatches instead of writing."""
    return (
        "Perform a three-way match for a purchase order. Requires Purchase and "
        "Inventory — verify with business_pack_report(pack='inventory') and "
        "list_models; stop if purchase.order or stock.picking is unavailable.\n"
        f"Purchase order: {purchase_order}\n\n"
        "Steps:\n"
        "1. read_record on purchase.order for ordered lines (product, qty, price_unit) "
        "and state.\n"
        "2. search_records on stock.picking linked to the PO for received quantities; "
        "search_records on account.move (move_type='in_invoice') for the vendor bill "
        "lines.\n"
        "3. Compare ordered vs received vs billed quantity and price per line. Build a "
        "discrepancy table (over-receipt, short-receipt, price variance, untaxed vs "
        "taxed mismatch).\n"
        "4. This workflow is READ-ONLY: report the discrepancies and recommended "
        "action to the human. Do NOT confirm receipts or post bills here — if a "
        "correction is approved, hand off to invoice_approval_chain or the gated "
        "write tools (preview_write -> validate_write -> execute_approved_write) in a "
        "separate, explicitly confirmed step."
    )


@mcp.prompt(
    name="customer_onboarding",
    description="Dedup-check then create a customer with contacts and payment terms via the write gate.",
)
def prompt_customer_onboarding(
    company_name: str,
    email: str = "",
    vat: str = "",
) -> str:
    """Workflow: dedup first, then gated create of partner + child contacts."""
    return (
        "Onboard a new customer without creating duplicates. Core res.partner is "
        "always available; payment terms need Accounting — check with list_models "
        "for account.payment.term before referencing it.\n"
        f"Company: {company_name}\n"
        f"Email: {email or '<none>'}\n"
        f"VAT: {vat or '<none>'}\n\n"
        "Steps:\n"
        "1. Dedup FIRST: search_records on res.partner with a free-text query on the "
        "company name, and separately by email/vat when provided. If a confident "
        "match exists, STOP and report it instead of creating a duplicate.\n"
        "2. If no match, propose the partner values (name, is_company=true, email, "
        "vat, customer_rank, property_payment_term_id) plus any child contacts. "
        "Present them to the human for confirmation.\n"
        "3. Create through the gate: preview_write -> validate_write -> "
        "execute_approved_write on res.partner (use values_list for the parent plus "
        "child contacts in one reviewed batch where possible). Require confirm=true "
        "and show the diff. Never create partners with a direct, ungated write."
    )


@mcp.prompt(
    name="expense_claim_review",
    description="Review pending expense claims against policy and gated-approve or refuse them.",
)
def prompt_expense_claim_review(
    employee: str = "",
    max_amount: str = "",
) -> str:
    """Workflow: policy-check pending expenses, then gated approve/refuse."""
    return (
        "Review pending employee expense claims. Requires the Expenses module — "
        "confirm hr.expense exists via list_models and STOP with a clear message if "
        "it is not installed.\n"
        f"Employee filter: {employee or '<all>'}\n"
        f"Auto-approve ceiling: {max_amount or '<none; all need human sign-off>'}\n\n"
        "Steps:\n"
        "1. search_records on hr.expense.sheet with state in ('submit','draft') "
        "(optionally filtered by employee). read_record each sheet for amount, "
        "category, attached receipts (read_attachment), and dates.\n"
        "2. Policy checks: missing receipt, amount over category limit, duplicate "
        "submission, out-of-period date. Build an approve / refuse / needs-info list.\n"
        "3. STOP and present the triage to the human. Anything above the ceiling or "
        "failing a policy check requires explicit human decision.\n"
        "4. Apply each approved decision through the gate: preview_write -> "
        "validate_write -> execute_approved_write on hr.expense.sheet for the "
        "approve/refuse state change, confirm=true, diff shown. Add the rationale "
        "with chatter_post. Do not change expense state with a direct, ungated write."
    )


@mcp.prompt(
    name="accounting_close_checklist",
    description="Read-only month-end close checklist: aging, unreconciled items, draft backlog, lock-date guidance.",
)
def prompt_accounting_close_checklist(period_end: str = "") -> str:
    """Workflow: read-only month-end review; surfaces work, never writes."""
    return (
        "Run a month-end accounting close checklist. Requires Accounting — confirm "
        "with business_pack_report(pack='accounting') and stop if unavailable. This "
        "workflow is strictly READ-ONLY.\n"
        f"Period end: {period_end or '<today>'}\n\n"
        "Steps:\n"
        "1. Receivables/payables: receivable_payable_aging(direction='receivable') "
        "and ('payable') as of the period end; highlight buckets over 90 days.\n"
        "2. Health snapshot: accounting_health_summary for open AR/AP item counts and "
        "the draft invoice backlog.\n"
        "3. Drafts to clear: search_records on account.move (state='draft') grouped "
        "with aggregate_records by journal — these block a clean close.\n"
        "4. Unreconciled bank items: search_records on account.bank.statement.line "
        "(or account.move.line with reconciled=false) for outstanding amounts.\n"
        "5. Produce a close checklist with counts and totals, and recommend a "
        "lock-date once items are cleared. Do NOT post, reconcile, or set lock dates "
        "here — setting the lock date is a deliberate, separately confirmed gated "
        "write the human performs after review."
    )
