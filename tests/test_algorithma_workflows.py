"""Tests for the Algorithma workflow plugin (plugins/algorithma_workflows).

The plugin functions are exercised directly (no registration on the shared
server, so upstream tool counts stay untouched) in request identity mode with
a fake per-user Odoo client, exactly like tests/test_identity_server.py.
"""

import importlib
import itertools
from datetime import datetime, timezone

import pytest

pytest.importorskip("algorithma_workflows")

from algorithma_workflows import plugin  # noqa: E402
from odoo_mcp import identity  # noqa: E402

server = importlib.import_module("odoo_mcp.server")

KEY_A = "a-key-0123456789abcdef0123456789abcdef01"
KEY_B = "b-key-0123456789abcdef0123456789abcdef02"
ANNA = {"x-user-email": "anna@example.ch", "x-odoo-api-key": KEY_A}
BOB = {"x-user-email": "bob@example.ch", "x-odoo-api-key": KEY_B}
INSTANCES = {"bauag2": {"url": "http://127.0.0.1:8071", "db": "bauag2"}}

FIELD_TYPES = {
    "name": "char", "start": "datetime", "stop": "datetime", "allday": "boolean",
    "description": "html", "location": "char", "partner_ids": "many2many",
    "is_company": "boolean", "customer_rank": "integer", "supplier_rank": "integer",
    "email": "char", "phone": "char", "street": "char", "city": "char", "zip": "char",
    "person_id": "many2one", "person_ids": "many2many", "location_id": "many2one",
    "move_type": "selection", "partner_id": "many2one", "invoice_date": "date",
    "invoice_line_ids": "one2many", "payment_date": "date", "parent_id": "many2one",
    "owner_id": "many2one", "employee_id": "many2one", "unit_amount": "float",
    "date": "date", "fsm_order_id": "many2one", "time_type_id": "many2one",
    "order_id": "many2one", "product_id": "many2one", "quantity": "float",
    "edv_number": "char", "price_unit": "float", "tag_id": "many2one", "type": "selection",
    "list_price": "float",
}


class FakeRequest:
    def __init__(self, lifespan_context):
        self.lifespan_context = lifespan_context


class Ctx:
    def __init__(self, app_context, headers):
        self.request_context = FakeRequest(app_context)
        self.headers = headers


class FakeOdoo:
    """Per-user fake client: records every create/write/method call."""

    ids = itertools.count(100)

    def __init__(self, identity_, instance):
        self.identity = identity_
        self.instance = instance
        self.url = "http://127.0.0.1:8071"
        self.calls = []
        self.records = {
            ("fsm.order", 5): {"id": 5, "name": "FO005", "person_id": False, "stage_id": [1, "New"]},
            ("account.move", 7): {"id": 7, "name": "INV/2026/0007", "state": "draft", "amount_total": 1234.5, "partner_id": [3, "Muster AG"], "move_type": "out_invoice", "amount_residual": 1234.5, "payment_state": "not_paid"},
            ("account.move", 8): {"id": 8, "name": "INV/2026/0008", "state": "posted", "amount_total": 500.0, "partner_id": [3, "Muster AG"], "move_type": "out_invoice", "amount_residual": 500.0, "payment_state": "not_paid"},
            ("res.partner", 3): {"id": 3, "name": "Muster AG"},
        }

    def get_model_fields(self, model):
        return {name: {"type": ftype, "string": name, "required": False, "readonly": False} for name, ftype in FIELD_TYPES.items()}

    def search_read(self, model_name, domain, fields=None, offset=None, limit=None, order=None):
        self.calls.append(("search_read", model_name, domain))
        if model_name == "ir.actions.report":
            return [{"id": 1, "report_name": "fieldservice_customer_report.report_fsm_customer", "name": "Einsatzrapport Kunde"}]
        if model_name == "account.tax":
            return [{"id": 11}] if any(d == ["amount", "=", 8.1] for d in domain) else []
        if model_name == "account.account":
            return [{"id": 21, "code": "3000", "name": "Ertrag", "account_type": "income"}]
        if model_name == "fsm.order" and domain == [["id", "=", 5]]:
            return [{"id": 5, "name": "FO005"}]
        for (model, rid), record in self.records.items():
            if model == model_name and domain == [["id", "=", rid]]:
                return [dict(record, display_name=record.get("name"))]
        return []

    def read_records(self, model_name, ids, fields=None):
        self.calls.append(("read", model_name, ids))
        return [dict(self.records[(model_name, rid)]) for rid in ids if (model_name, rid) in self.records]

    def execute_method(self, model, method, *args, **kwargs):
        self.calls.append((method, model, args, kwargs))
        if method == "create":
            new_id = next(self.ids)
            values = args[0] if args else {}
            if isinstance(values, dict):
                self.records[(model, new_id)] = dict(values, id=new_id, name=values.get("name", f"NEW{new_id}"))
            return new_id
        if method == "write":
            for rid in args[0]:
                self.records.setdefault((model, rid), {}).update(args[1])
            return True
        if method == "action_post":
            self.records[("account.move", args[0][0])]["state"] = "posted"
        return True

    def creates(self, model):
        return [call[2][0] for call in self.calls if call[0] == "create" and call[1] == model]


@pytest.fixture
def workflow_env(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "request")
    monkeypatch.setenv("ODOO_MCP_ENABLE_WRITES", "1")
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "off")
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "fsm.order.action_complete,account.move.action_post,account.payment.register.action_create_payments")
    monkeypatch.delenv("ODOO_MCP_POLICY_FILE", raising=False)
    monkeypatch.chdir("/")
    monkeypatch.setenv("ALGORITHMA_TZ", "Europe/Zurich")
    monkeypatch.setenv("ODOO_PUBLIC_URL", "http://localhost:8071")
    monkeypatch.setattr(server, "load_instances_config", lambda: ("bauag2", dict(INSTANCES)))
    clients = {}
    monkeypatch.setattr(server, "build_identity_client", lambda entry, identity_, name="default": clients.setdefault(identity_.email, FakeOdoo(identity_, name)))
    plugin._PENDING.clear()
    app_context = server.AppContext()
    return app_context, clients


# --- time handling --------------------------------------------------------------


def test_zurich_times_become_utc():
    summer = plugin.parse_local_datetime("2026-09-02 10:00")
    assert plugin.to_odoo_utc(summer) == "2026-09-02 08:00:00"  # CEST = UTC+2
    winter = plugin.parse_local_datetime("2026-01-15T10:00:00")
    assert plugin.to_odoo_utc(winter) == "2026-01-15 09:00:00"  # CET = UTC+1
    explicit = plugin.parse_local_datetime("2026-09-02 10:00:00+0000")
    assert plugin.to_odoo_utc(explicit) == "2026-09-02 10:00:00"
    assert plugin.format_local(datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)).endswith("02.09.2026 10:00")
    with pytest.raises(ValueError, match="nicht verstanden"):
        plugin.parse_local_datetime("morgen 10 Uhr")


# --- termin_buchen ------------------------------------------------------------------


def test_termin_buchen_two_call_flow_creates_event_as_user(workflow_env):
    app_context, clients = workflow_env
    ctx = Ctx(app_context, ANNA)
    preview = plugin.termin_buchen(ctx, "Zahnarzt", "2026-09-02 10:00")
    assert preview["success"] and preview["status"] == "BESTAETIGUNG_ERFORDERLICH"
    assert preview["karte"]["details"]["odoo_utc"] == {"start": "2026-09-02 08:00:00", "stop": "2026-09-02 09:00:00"}
    assert preview["freigabe_code"].startswith("algorithma:")
    assert clients["anna@example.ch"].creates("calendar.event") == []  # nothing written yet

    done = plugin.termin_buchen(ctx, "Zahnarzt", "2026-09-02 10:00", bestaetigen=True, freigabe_code=preview["freigabe_code"])
    assert done["success"], done
    created = clients["anna@example.ch"].creates("calendar.event")
    assert created == [{"name": "Zahnarzt", "start": "2026-09-02 08:00:00", "stop": "2026-09-02 09:00:00", "allday": False}]
    assert done["event_id"] >= 100
    assert done["summary"].startswith("Termin 'Zahnarzt'")


def test_termin_buchen_code_is_bound_to_user_and_single_use(workflow_env):
    app_context, clients = workflow_env
    code = plugin.termin_buchen(Ctx(app_context, ANNA), "Zahnarzt", "2026-09-02 10:00")["freigabe_code"]
    as_bob = plugin.termin_buchen(Ctx(app_context, BOB), "Zahnarzt", "2026-09-02 10:00", bestaetigen=True, freigabe_code=code)
    assert as_bob["success"] is False and "anderen Benutzer" in as_bob["error"]
    assert "bob@example.ch" not in clients or clients["bob@example.ch"].creates("calendar.event") == []
    ok = plugin.termin_buchen(Ctx(app_context, ANNA), "Zahnarzt", "2026-09-02 10:00", bestaetigen=True, freigabe_code=code)
    assert ok["success"]
    again = plugin.termin_buchen(Ctx(app_context, ANNA), "Zahnarzt", "2026-09-02 10:00", bestaetigen=True, freigabe_code=code)
    assert again["success"] is False and "Keine offene Freigabe" in again["error"]  # consumed = gone
    missing = plugin.termin_buchen(Ctx(app_context, ANNA), "Zahnarzt", "2026-09-02 10:00", bestaetigen=True)
    assert missing["success"] is False and "Keine offene Freigabe" in missing["error"]


def test_confirmation_without_code_uses_the_users_only_pending_action(workflow_env):
    app_context, clients = workflow_env
    card = plugin.termin_buchen(Ctx(app_context, ANNA), "Zahnarzt", "2026-09-02 10:00")
    assert card["status"] == "BESTAETIGUNG_ERFORDERLICH"
    # Bob has no pending action -> refused even without a code.
    bob = plugin.termin_buchen(Ctx(app_context, BOB), "Zahnarzt", "2026-09-02 10:00", bestaetigen=True)
    assert bob["success"] is False and "Keine offene Freigabe" in bob["error"]
    # Anna's own single pending action is used when the model drops the code.
    done = plugin.termin_buchen(Ctx(app_context, ANNA), "Zahnarzt", "2026-09-02 10:00", bestaetigen=True, freigabe_code="mangled")
    assert done["success"], done
    assert len(clients["anna@example.ch"].creates("calendar.event")) == 1
    # Two pending actions -> ambiguity needs the real code.
    c1 = plugin.termin_buchen(Ctx(app_context, ANNA), "A", "2026-09-03 10:00")["freigabe_code"]
    plugin.termin_buchen(Ctx(app_context, ANNA), "B", "2026-09-04 10:00")
    ambiguous = plugin.termin_buchen(Ctx(app_context, ANNA), "A", "2026-09-03 10:00", bestaetigen=True)
    assert ambiguous["success"] is False and "Mehrere offene Freigaben" in ambiguous["error"]
    assert plugin.termin_buchen(Ctx(app_context, ANNA), "A", "2026-09-03 10:00", bestaetigen=True, freigabe_code=c1)["success"]


def test_termin_buchen_rejects_end_before_start_and_missing_identity(workflow_env):
    app_context, _ = workflow_env
    bad = plugin.termin_buchen(Ctx(app_context, ANNA), "X", "2026-09-02 10:00", stop="2026-09-02 09:00")
    assert bad["success"] is False and "stop muss nach start" in bad["error"]
    closed = plugin.termin_buchen(Ctx(app_context, None), "X", "2026-09-02 10:00")
    assert closed["success"] is False and "identity" in closed["error"].lower()


# --- other tools -----------------------------------------------------------------------


def test_create_partner_flow(workflow_env):
    app_context, clients = workflow_env
    ctx = Ctx(app_context, ANNA)
    card = plugin.create_partner(ctx, "Neue Firma AG", email="info@neu.ch", art="kunde")
    assert card["status"] == "BESTAETIGUNG_ERFORDERLICH"
    done = plugin.create_partner(ctx, "Neue Firma AG", email="info@neu.ch", art="kunde", bestaetigen=True, freigabe_code=card["freigabe_code"])
    assert done["success"]
    assert clients["anna@example.ch"].creates("res.partner") == [{"name": "Neue Firma AG", "is_company": True, "customer_rank": 1, "email": "info@neu.ch"}]


def test_auftrag_abschliessen_uses_policy_gated_method(workflow_env, monkeypatch):
    app_context, clients = workflow_env
    ctx = Ctx(app_context, ANNA)
    card = plugin.auftrag_abschliessen(ctx, 5)
    assert card["karte"]["details"] == {"auftrag": "FO005", "id": 5, "stufe_vorher": "New", "stufe_nachher": "Completed"}
    done = plugin.auftrag_abschliessen(ctx, 5, bestaetigen=True, freigabe_code=card["freigabe_code"])
    assert done["success"], done
    assert ("action_complete", "fsm.order", ([5],), {}) in clients["anna@example.ch"].calls
    # Without the allowlist the core refuses and the plugin passes the refusal on verbatim.
    monkeypatch.setenv("ODOO_MCP_ALLOWED_SIDE_EFFECT_METHODS", "")
    card2 = plugin.auftrag_abschliessen(ctx, 5)
    refused = plugin.auftrag_abschliessen(ctx, 5, bestaetigen=True, freigabe_code=card2["freigabe_code"])
    assert refused["success"] is False and "Unreviewed side-effect methods are blocked" in refused["error"]


def test_auftrag_monteur_zuweisen_creates_person_and_writes_assignment(workflow_env):
    app_context, clients = workflow_env
    ctx = Ctx(app_context, ANNA)
    card = plugin.auftrag_monteur_zuweisen(ctx, 5, "Hans Muster")
    assert card["karte"]["details"]["vorher"] == "keine Zuteilung"
    done = plugin.auftrag_monteur_zuweisen(ctx, 5, "Hans Muster", bestaetigen=True, freigabe_code=card["freigabe_code"])
    assert done["success"], done
    odoo = clients["anna@example.ch"]
    assert odoo.creates("res.partner") == [{"name": "Hans Muster"}]
    assert len(odoo.creates("fsm.person")) == 1
    writes = [call for call in odoo.calls if call[0] == "write" and call[1] == "fsm.order"]
    assert writes and writes[0][2][1]["person_id"] == done["person_id"]


def test_bericht_link_uses_public_url(workflow_env):
    app_context, _ = workflow_env
    result = plugin.bericht_link(Ctx(app_context, ANNA), "fsm.order", 5)
    assert result["success"] and result["url"] == "http://localhost:8071/report/pdf/fieldservice_customer_report.report_fsm_customer/5"
    assert result["bericht"] == "Einsatzrapport Kunde"


def test_create_invoice_applies_swiss_vat_and_stays_draft(workflow_env):
    app_context, clients = workflow_env
    ctx = Ctx(app_context, ANNA)
    lines = [{"description": "Arbeit", "quantity": 2, "price_unit": 100}, {"description": "Ohne", "quantity": 1, "price_unit": 50, "tax_percent": 0}]
    card = plugin.create_invoice(ctx, 3, lines)
    assert card["karte"]["details"]["betrag_chf"] == 250.0 and "8.1% MWST" in card["karte"]["details"]["mwst"]
    done = plugin.create_invoice(ctx, 3, lines, bestaetigen=True, freigabe_code=card["freigabe_code"])
    assert done["success"], done
    created = clients["anna@example.ch"].creates("account.move")[0]
    assert created["move_type"] == "out_invoice" and created["partner_id"] == 3
    assert created["invoice_line_ids"][0][2]["tax_ids"] == [[6, 0, [11]]]
    assert created["invoice_line_ids"][1][2]["tax_ids"] == [[6, 0, []]]
    assert "NICHT gebucht" in done["summary"]


def test_post_and_pay_invoice_flows(workflow_env):
    app_context, clients = workflow_env
    ctx = Ctx(app_context, ANNA)
    card = plugin.post_journal_entry(ctx, 7)
    assert card["karte"]["aktion"] == "Beleg BUCHEN"
    posted = plugin.post_journal_entry(ctx, 7, bestaetigen=True, freigabe_code=card["freigabe_code"])
    assert posted["success"] and posted["state"] == "posted"
    not_posted = plugin.pay_invoice(ctx, 7) if clients["anna@example.ch"].records[("account.move", 7)]["state"] != "posted" else plugin.pay_invoice(ctx, 8)
    assert not_posted["status"] == "BESTAETIGUNG_ERFORDERLICH"
    paid = plugin.pay_invoice(ctx, 8, bestaetigen=True, freigabe_code=not_posted["freigabe_code"])
    assert paid["success"], paid
    calls = clients["anna@example.ch"].calls
    assert any(call[0] == "create" and call[1] == "account.payment.register" for call in calls)
    assert any(call[0] == "action_create_payments" for call in calls)


def test_einsatzrapport_creates_all_records_through_the_gate(workflow_env):
    app_context, clients = workflow_env
    ctx = Ctx(app_context, ANNA)
    args = dict(kunde={"name": "Muster Kunde AG", "city": "Luzern"}, standort={"name": "Baustelle Zug"}, beschreibung="Rohrbruch", zeiten=[{"monteur": "Hans Muster", "typ": "Arbeit", "stunden": 2}], material=[{"produkt": "Rohr", "menge": 3, "preis": 12.5}], zuschlaege=["Notfall"])
    card = plugin.einsatzrapport_erstellen(ctx, **args)
    assert card["karte"]["details"]["zeitzeilen"] == 1 and card["karte"]["details"]["monteur"] == "Hans Muster"
    done = plugin.einsatzrapport_erstellen(ctx, **args, bestaetigen=True, freigabe_code=card["freigabe_code"])
    assert done["success"], done
    odoo = clients["anna@example.ch"]
    for model in ("res.partner", "fsm.location", "fsm.person", "fsm.order", "hr.employee", "project.time.type", "account.analytic.line", "product.product", "fsm.material.line", "fsm.gav.tag", "fsm.gav.line"):
        assert odoo.creates(model), model
    assert odoo.creates("fsm.order")[0]["description"] == "Rohrbruch"
    assert done["rapport_url"].startswith("http://localhost:8071/report/pdf/")


def test_aktuelles_datum_reports_zurich_time_and_relative_days(workflow_env):
    app_context, _ = workflow_env
    result = plugin.aktuelles_datum(Ctx(app_context, ANNA))
    assert result["success"] and result["zeitzone"] == "Europe/Zurich"
    assert result["morgen"]["datum"] > result["heute"]["datum"]
    assert result["heute"]["wochentag"] in plugin.WEEKDAYS_DE
    assert result["utc_offset"] in {"+0100", "+0200"}


def test_register_uses_plugin_api_and_destructive_annotations():
    registered = []

    class Api:
        READ_ONLY_TOOL = "read-only"

        def tool(self, **kwargs):
            def decorator(fn):
                registered.append((fn.__name__, kwargs["annotations"]))
                return fn

            return decorator

    plugin.register(Api())
    names = [name for name, _ in registered]
    assert names == ["aktuelles_datum", "get_account_by_code", "bericht_link", "termin_buchen", "create_partner", "auftrag_monteur_zuweisen", "auftrag_abschliessen", "create_invoice", "post_journal_entry", "pay_invoice", "einsatzrapport_erstellen"]
    assert dict(registered)["termin_buchen"] != "read-only"
    assert dict(registered)["get_account_by_code"] == "read-only"
