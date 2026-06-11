from datetime import date

from odoo_mcp.accounting_tools import (
    aging_domain,
    bucket_for_days,
    build_aging_report,
    build_unreconciled_summary,
    fetch_aging_lines,
)


def test_aging_domain_receivable():
    domain = aging_domain("receivable")
    assert ("account_id.account_type", "=", "asset_receivable") in domain
    assert ("parent_state", "=", "posted") in domain
    assert ("reconciled", "=", False) in domain


def test_aging_domain_payable():
    assert ("account_id.account_type", "=", "liability_payable") in aging_domain(
        "payable"
    )


def test_bucket_boundaries():
    assert bucket_for_days(-5) == "not_due"
    assert bucket_for_days(0) == "not_due"
    assert bucket_for_days(1) == "1-30"
    assert bucket_for_days(30) == "1-30"
    assert bucket_for_days(31) == "31-60"
    assert bucket_for_days(60) == "31-60"
    assert bucket_for_days(61) == "61-90"
    assert bucket_for_days(90) == "61-90"
    assert bucket_for_days(91) == "90+"
    assert bucket_for_days(400) == "90+"


def _line(amount, maturity, partner=(1, "Azure")):
    return {
        "amount_residual": amount,
        "date_maturity": maturity,
        "date": maturity,
        "partner_id": list(partner) if partner else False,
    }


def test_build_aging_report_buckets_and_partners():
    as_of = date(2026, 6, 11)
    lines = [
        _line(100.0, "2026-07-01"),  # not due
        _line(50.0, "2026-06-01"),  # 10 days -> 1-30
        _line(25.0, "2026-03-01", partner=(2, "Deco")),  # 102 days -> 90+
    ]
    report = build_aging_report(lines, "receivable", as_of)
    assert report["buckets"]["not_due"] == 100.0
    assert report["buckets"]["1-30"] == 50.0
    assert report["buckets"]["90+"] == 25.0
    assert report["total_outstanding"] == 175.0
    assert report["partner_count"] == 2
    assert report["partners"][0]["partner"] == "Azure"
    assert report["partners"][0]["total"] == 150.0


def test_build_aging_report_payable_sign_flip():
    as_of = date(2026, 6, 11)
    report = build_aging_report(
        [_line(-200.0, "2026-05-01")], "payable", as_of
    )
    assert report["total_outstanding"] == 200.0
    assert report["buckets"]["31-60"] == 200.0


def test_build_aging_report_handles_missing_dates_and_amounts():
    as_of = date(2026, 6, 11)
    lines = [
        {"amount_residual": 10.0, "date_maturity": False, "date": False,
         "partner_id": False},
        {"amount_residual": None, "date_maturity": "2026-01-01",
         "partner_id": False},
        {"amount_residual": 0, "date_maturity": "2026-01-01", "partner_id": False},
    ]
    report = build_aging_report(lines, "receivable", as_of)
    # Undated lines land in not_due; zero/None amounts skipped.
    assert report["buckets"]["not_due"] == 10.0
    assert report["skipped_lines"] == 2
    assert report["partners"][0]["partner"] == "(no partner)"


def test_build_aging_report_top_partners_cap():
    as_of = date(2026, 6, 11)
    lines = [
        _line(float(i), "2026-06-01", partner=(i, f"P{i}")) for i in range(1, 30)
    ]
    report = build_aging_report(lines, "receivable", as_of, top_partners=5)
    assert len(report["partners"]) == 5
    assert report["partner_count"] == 29
    assert report["partners"][0]["partner"] == "P29"


class FakeClient:
    def __init__(self):
        self.search_read_calls = []
        self.execute_calls = []

    def search_read(self, model, domain, fields=None, limit=None):
        self.search_read_calls.append((model, domain, fields, limit))
        return [_line(10.0, "2026-06-01")]

    def execute_method(self, model, method, domain):
        self.execute_calls.append((model, method, domain))
        return 3


def test_fetch_aging_lines_clamps_limit():
    client = FakeClient()
    lines = fetch_aging_lines(client, "receivable", limit=999999)
    assert len(lines) == 1
    model, domain, fields, limit = client.search_read_calls[0]
    assert model == "account.move.line"
    assert limit == 5000
    assert "amount_residual" in fields


def test_build_unreconciled_summary_counts():
    client = FakeClient()
    summary = build_unreconciled_summary(client)
    assert summary["open_receivable_items"] == 3
    assert summary["open_payable_items"] == 3
    assert summary["draft_invoices"] == 3
    models = [call[0] for call in client.execute_calls]
    assert models.count("account.move.line") == 2
    assert "account.move" in models
