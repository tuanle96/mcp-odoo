"""Algorithma intent tools, ported from A-Odoo-MCP 3.3.x onto the vNext core.

Same tool names as before (termin_buchen, create_partner, get_account_by_code,
bericht_link, auftrag_monteur_zuweisen, auftrag_abschliessen, create_invoice,
post_journal_entry, pay_invoice, einsatzrapport_erstellen) and the same
two-call confirmation UX the Algorithma agent knows: the first call returns a
German confirmation card plus a ``freigabe_code``; only a second call with
``bestaetigen=true`` and that code executes.

What changed underneath - nothing bypasses the core pipeline:

    REQUEST -> IDENTITY -> INSTANCE -> POLICY -> PREVIEW -> VALIDATION -> APPROVAL
            -> ODOO ACL -> EXECUTE -> AUDIT

* every create/write goes through ``validate_write`` -> ``execute_approved_write``
  (live fields_get validation, approval bound to the requesting user and
  instance, JSONL audit, Odoo ACL as the last word);
* every method call goes through ``execute_method`` and its side-effect policy
  (``fsm.order.action_complete``, ``account.move.action_post`` ... must be
  allowlisted by the deployment);
* the ``freigabe_code`` is bound to the user who previewed, single-use and
  expires after ten minutes - Bob cannot spend Anna's code;
* the plugin never opens its own Odoo connection: ``api.resolve_odoo(ctx)``
  returns the client of the requesting user.

Times: users speak Europe/Zurich, Odoo stores UTC. ``termin_buchen`` converts.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import Context

PLUGIN_NAME = "algorithma_workflows"
PENDING_TTL_SECONDS = 10 * 60
PENDING_MAX = 256
DEFAULT_TZ = "Europe/Zurich"


def _core() -> Any:
    """Late import: the core surface is fully registered when plugins load."""
    from odoo_mcp import server

    return server


def _server_core() -> Any:
    from odoo_mcp import server_core

    return server_core


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("ALGORITHMA_TZ", DEFAULT_TZ))


def chf_schwelle() -> float:
    try:
        return float(os.environ.get("CHF_SCHWELLE", "5000"))
    except ValueError:
        return 5000.0


def public_url(odoo: Any) -> str:
    """Browser-facing Odoo URL (PDF links); falls back to the instance URL."""
    return (os.environ.get("ODOO_PUBLIC_URL") or getattr(odoo, "url", "") or "").rstrip("/")


# --- time handling ------------------------------------------------------------


def parse_local_datetime(text: str) -> datetime:
    """Parse '2026-09-02 10:00', '2026-09-02T10:00:00', or with offset/Z.

    Naive values are interpreted in the local business timezone
    (``ALGORITHMA_TZ``, default Europe/Zurich).
    """
    raw = (text or "").strip().replace("T", " ")
    if not raw:
        raise ValueError("Zeitangabe fehlt.")
    parsed: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(
            f"Zeitangabe {text!r} nicht verstanden - erwartet 'YYYY-MM-DD HH:MM' (Schweizer Zeit)."
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_local_tz())
    return parsed


def to_odoo_utc(value: datetime) -> str:
    """Odoo datetime string (UTC, no tzinfo)."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def format_local(value: datetime) -> str:
    local = value.astimezone(_local_tz())
    return local.strftime("%a %d.%m.%Y %H:%M")


# --- confirmation store (identity-bound, single-use, TTL) ---------------------

_PENDING: Dict[str, Dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()


def _identity_binding(ctx: Any, instance_name: str) -> Optional[str]:
    identity = _server_core().current_identity(ctx)
    return None if identity is None else identity.approval_binding(instance_name)


def _principal(ctx: Any) -> Optional[str]:
    identity = _server_core().current_identity(ctx)
    return None if identity is None else identity.principal


def register_pending(ctx: Any, instance_name: str, tool: str, payload: Dict[str, Any]) -> str:
    """Store a previewed action; returns the code the user's 'ja' unlocks."""
    now = time.time()
    material = json.dumps(
        {"tool": tool, "instance": instance_name, "principal": _principal(ctx), "payload": payload, "at": now},
        sort_keys=True,
        default=str,
    )
    code = "algorithma:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    with _PENDING_LOCK:
        expired = [key for key, item in _PENDING.items() if item["expires_at"] < now]
        for key in expired:
            _PENDING.pop(key, None)
        while len(_PENDING) >= PENDING_MAX:
            _PENDING.pop(next(iter(_PENDING)), None)
        _PENDING[code] = {
            "tool": tool,
            "instance": instance_name,
            "binding": _identity_binding(ctx, instance_name),
            "payload": payload,
            "expires_at": now + PENDING_TTL_SECONDS,
        }
    return code


def take_pending(ctx: Any, instance_name: str, tool: str, code: Optional[str]) -> Dict[str, Any]:
    """Consume a code: same tool, same instance, same user, unexpired, once.

    Models sometimes drop or mangle the code between the card and the "ja".
    When the code is missing (or unknown) and this user has exactly one live
    pending action for this tool and instance, that action is used - the
    identity binding and single-use guarantees are unchanged; only the
    lookup key is relaxed. Ambiguity (two pending actions) still needs the code.
    """
    code = (code or "").strip() or None
    binding = _identity_binding(ctx, instance_name)
    with _PENDING_LOCK:
        now = time.time()
        if code is None or code not in _PENDING:
            candidates = [key for key, item in _PENDING.items() if item["tool"] == tool and item["instance"] == instance_name and item["binding"] == binding and item["expires_at"] >= now]
            if len(candidates) == 1:
                code = candidates[0]
            elif not candidates:
                raise ValueError("Keine offene Freigabe fuer diese Aktion - zuerst ohne bestaetigen aufrufen, Karte zeigen, dann bestaetigen.")
            else:
                raise ValueError("Mehrere offene Freigaben - bitte den freigabe_code aus der zuletzt gezeigten Karte angeben.")
        item = _PENDING.get(code)
        if item is None:
            raise ValueError("Unbekannter oder bereits verwendeter freigabe_code - Vorschau wiederholen.")
        if item["expires_at"] < time.time():
            _PENDING.pop(code, None)
            raise ValueError("Der freigabe_code ist abgelaufen (10 Minuten) - Vorschau wiederholen.")
        if item["tool"] != tool or item["instance"] != instance_name:
            raise ValueError("Der freigabe_code gehoert zu einer anderen Aktion oder Instanz.")
        if item["binding"] != _identity_binding(ctx, instance_name):
            raise ValueError("Der freigabe_code wurde von einem anderen Benutzer erzeugt - Vorschau als aktueller Benutzer wiederholen.")
        _PENDING.pop(code, None)
    return dict(item["payload"])


def _card(tool: str, aktion: str, details: Dict[str, Any], frage: str, code: str) -> Dict[str, Any]:
    return {
        "success": True,
        "tool": tool,
        "status": "BESTAETIGUNG_ERFORDERLICH",
        "karte": {"aktion": aktion, "details": details, "frage": frage},
        "freigabe_code": code,
        "anweisung": (
            "Zeige dem Benutzer diese Karte mit allen Details. Fuehre die Aktion NUR "
            "aus, wenn er ausdruecklich zustimmt - dann denselben Aufruf mit "
            f"bestaetigen=true und freigabe_code={code!r} wiederholen. Bei Ablehnung: nichts tun."
        ),
    }


def _fail(tool: str, error: Any, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "tool": tool, "error": str(error), **extra}


# --- gated primitives (all writes pass the core pipeline) ---------------------


def gated_create(ctx: Any, model: str, values: Dict[str, Any], *, instance: Optional[str] = None, context: Optional[Dict[str, Any]] = None) -> int:
    core = _core()
    report = core.validate_write(ctx, model, "create", values=values, context=context, instance=instance)
    if not report.get("success") or not (report.get("approval_status") or {}).get("stored"):
        issues = report.get("issues") or []
        detail = "; ".join(str(issue.get("message")) for issue in issues if isinstance(issue, dict)) or report.get("error")
        raise ValueError(f"{model} anlegen abgelehnt: {detail}")
    result = core.execute_approved_write(ctx, report["approval"], confirm=True)
    if not result.get("success"):
        raise ValueError(f"{model} anlegen fehlgeschlagen: {result.get('error')}")
    created = result.get("result")
    if isinstance(created, list) and created:
        created = created[0]
    return int(created)


def gated_write(ctx: Any, model: str, record_ids: List[int], values: Dict[str, Any], *, instance: Optional[str] = None) -> None:
    core = _core()
    report = core.validate_write(ctx, model, "write", values=values, record_ids=record_ids, instance=instance)
    if not report.get("success") or not (report.get("approval_status") or {}).get("stored"):
        issues = report.get("issues") or []
        detail = "; ".join(str(issue.get("message")) for issue in issues if isinstance(issue, dict)) or report.get("error")
        raise ValueError(f"{model} aendern abgelehnt: {detail}")
    result = core.execute_approved_write(ctx, report["approval"], confirm=True)
    if not result.get("success"):
        raise ValueError(f"{model} aendern fehlgeschlagen: {result.get('error')}")


def gated_method(ctx: Any, model: str, method: str, args: List[Any], kwargs: Optional[Dict[str, Any]] = None, *, instance: Optional[str] = None) -> Any:
    """Side-effect methods go through execute_method and its policy allowlist."""
    result = _core().execute_method(ctx, model, method, args=args, kwargs=kwargs, instance=instance)
    if not result.get("success"):
        raise ValueError(f"{model}.{method} nicht ausgefuehrt: {result.get('error')}")
    return result.get("result")


def find_or_create(ctx: Any, odoo: Any, model: str, domain: List[Any], values: Dict[str, Any], *, instance: Optional[str] = None) -> int:
    rows = odoo.search_read(model, domain, fields=["id"], limit=1)
    if rows:
        return int(rows[0]["id"])
    return gated_create(ctx, model, values, instance=instance)


def fsm_person_id(ctx: Any, odoo: Any, name: str, *, instance: Optional[str] = None) -> int:
    """fsm.person for a technician name; created if missing (see old MCP note)."""
    rows = odoo.search_read("fsm.person", [["name", "=", name]], fields=["id"], limit=1)
    if rows:
        return int(rows[0]["id"])
    partner_id = find_or_create(ctx, odoo, "res.partner", [["name", "=", name]], {"name": name}, instance=instance)
    return find_or_create(ctx, odoo, "fsm.person", [["partner_id", "=", partner_id]], {"partner_id": partner_id}, instance=instance)


# --- tools -----------------------------------------------------------------------

WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def aktuelles_datum(ctx: Context) -> Dict[str, Any]:
    """Aktuelles Datum und Uhrzeit in Schweizer Zeit (Europe/Zurich) inkl. Wochentag, «morgen» und «uebermorgen».

    Fuer relative Angaben wie «morgen», «naechsten Montag» oder «in einer Woche» immer zuerst
    dieses Werkzeug aufrufen statt den Benutzer nach dem Datum zu fragen.
    """
    tool = "aktuelles_datum"
    try:
        now = datetime.now(_local_tz())
        def day(offset: int) -> Dict[str, str]:
            d = now + timedelta(days=offset)
            return {"datum": d.strftime("%Y-%m-%d"), "wochentag": WEEKDAYS_DE[d.weekday()]}
        return {
            "success": True,
            "tool": tool,
            "jetzt": now.strftime("%Y-%m-%d %H:%M"),
            "zeitzone": str(_local_tz().key),
            "utc_offset": now.strftime("%z"),
            "heute": day(0),
            "morgen": day(1),
            "uebermorgen": day(2),
            "kalenderwoche": now.isocalendar()[1],
        }
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def termin_buchen(
    ctx: Context,
    titel: str,
    start: str,
    stop: Optional[str] = None,
    beschreibung: Optional[str] = None,
    ort: Optional[str] = None,
    teilnehmer_ids: Optional[List[int]] = None,
    bestaetigen: bool = False,
    freigabe_code: Optional[str] = None,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Legt einen Kalender-Termin (calendar.event) fuer den angemeldeten Benutzer an.

    start/stop in Schweizer Zeit, z.B. "2026-09-02 10:00"; stop optional (Standard: +1 h).
    Erster Aufruf: Karte + freigabe_code. Nach dem «ja» des Benutzers: derselbe Aufruf mit
    bestaetigen=true und dem freigabe_code. Die Zeiten werden fuer Odoo nach UTC umgerechnet.
    """
    tool = "termin_buchen"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        begin = parse_local_datetime(start)
        end = parse_local_datetime(stop) if stop else begin + timedelta(hours=1)
        if end <= begin:
            raise ValueError("stop muss nach start liegen.")
        values: Dict[str, Any] = {
            "name": titel.strip(),
            "start": to_odoo_utc(begin),
            "stop": to_odoo_utc(end),
            "allday": False,
        }
        if beschreibung:
            values["description"] = beschreibung
        if ort:
            values["location"] = ort
        if teilnehmer_ids:
            values["partner_ids"] = [[6, 0, [int(pid) for pid in teilnehmer_ids]]]
        anzeige = {
            "titel": values["name"],
            "von": format_local(begin),
            "bis": format_local(end),
            "zeitzone": str(_local_tz().key),
            "odoo_utc": {"start": values["start"], "stop": values["stop"]},
        }
        if not bestaetigen:
            report = api.validate_write(ctx, "calendar.event", "create", values=values, instance=instance_name)
            if not report.get("success"):
                return _fail(tool, "Termin-Daten ungueltig", issues=report.get("issues"), details=anzeige)
            code = register_pending(ctx, instance_name, tool, {"values": values, "anzeige": anzeige})
            return _card(
                tool,
                "Termin ANLEGEN",
                anzeige,
                f"Termin '{values['name']}' {anzeige['von']}–{format_local(end).split(' ')[-1]} anlegen?",
                code,
            )
        payload = take_pending(ctx, instance_name, tool, freigabe_code)
        event_id = gated_create(ctx, "calendar.event", payload["values"], instance=instance_name)
        shown = payload["anzeige"]
        return {
            "success": True,
            "tool": tool,
            "event_id": event_id,
            "termin": shown,
            "summary": f"Termin '{shown['titel']}' {shown['von']}–{shown['bis'].split(' ')[-1]} angelegt (ID {event_id}).",
        }
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _fail(tool, exc)


def create_partner(
    ctx: Context,
    name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    street: Optional[str] = None,
    city: Optional[str] = None,
    zip: Optional[str] = None,  # noqa: A002 - kept for compatibility with the old tool
    is_company: bool = True,
    art: str = "kunde",
    bestaetigen: bool = False,
    freigabe_code: Optional[str] = None,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Legt einen Kontakt (res.partner) an; art: 'kunde' oder 'lieferant'.

    Erster Aufruf zeigt die Karte, zweiter Aufruf mit bestaetigen=true + freigabe_code legt an.
    """
    tool = "create_partner"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        values: Dict[str, Any] = {"name": name.strip(), "is_company": bool(is_company)}
        if art == "kunde":
            values["customer_rank"] = 1
        elif art == "lieferant":
            values["supplier_rank"] = 1
        for key, value in (("email", email), ("phone", phone), ("street", street), ("city", city), ("zip", zip)):
            if value:
                values[key] = value
        if not bestaetigen:
            existing = odoo.search_read("res.partner", [["name", "=", values["name"]]], fields=["id", "email", "city"], limit=3)
            report = api.validate_write(ctx, "res.partner", "create", values=values, instance=instance_name)
            if not report.get("success"):
                return _fail(tool, "Kontakt-Daten ungueltig", issues=report.get("issues"))
            code = register_pending(ctx, instance_name, tool, {"values": values})
            details = dict(values)
            if existing:
                details["hinweis_bestehende"] = existing
            return _card(tool, "Kontakt ANLEGEN", details, f"Kontakt '{values['name']}' ({art}) anlegen?", code)
        payload = take_pending(ctx, instance_name, tool, freigabe_code)
        partner_id = gated_create(ctx, "res.partner", payload["values"], instance=instance_name)
        return {"success": True, "tool": tool, "partner_id": partner_id, "summary": f"Kontakt '{payload['values']['name']}' angelegt (ID {partner_id})."}
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def get_account_by_code(ctx: Context, code: str, instance: Optional[str] = None) -> Dict[str, Any]:
    """Schlaegt Konten im Schweizer KMU-Kontenrahmen nach (z.B. '1020', '3000'). Nur lesen."""
    tool = "get_account_by_code"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        rows = odoo.search_read("account.account", [["code", "=like", str(code).strip() + "%"]], fields=["code", "name", "account_type"], limit=10)
        rows, redacted = api.get_field_policy().redact_records(instance_name, "account.account", list(rows))
        return {"success": True, "tool": tool, "model": "account.account", "count": len(rows), "result": rows, "redacted_fields": redacted}
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def bericht_link(ctx: Context, model: str, record_id: int, report_name: Optional[str] = None, instance: Optional[str] = None) -> Dict[str, Any]:
    """Liefert die Adresse des PDF-Berichts zu einem Datensatz (nur den Link, keine Datei).

    Beispiel Einsatzrapport: model="fsm.order", record_id=3. Ohne report_name: erster PDF-Bericht.
    """
    tool = "bericht_link"
    try:
        api = _core()
        api.validate_model_name(model)
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        rows = odoo.search_read(model, [["id", "=", int(record_id)]], fields=["display_name"], limit=1)
        if not rows:
            raise ValueError(f"Kein Datensatz {record_id} in '{model}' gefunden - oder keine Leseberechtigung.")
        reports = odoo.search_read("ir.actions.report", [["model", "=", model], ["report_type", "=", "qweb-pdf"]], fields=["report_name", "name"], limit=5)
        if not reports:
            raise ValueError(f"Fuer '{model}' ist kein PDF-Bericht hinterlegt.")
        if report_name:
            chosen = next((r for r in reports if r["report_name"] == report_name or r["name"] == report_name), None)
            if chosen is None:
                raise ValueError(f"Kein PDF-Bericht '{report_name}' fuer '{model}'. Verfuegbar: " + ", ".join(r["name"] for r in reports))
        else:
            chosen = reports[0]
        others = [r["name"] for r in reports if r is not chosen]
        result: Dict[str, Any] = {
            "success": True,
            "tool": tool,
            "model": model,
            "record_id": int(record_id),
            "datensatz": rows[0].get("display_name"),
            "bericht": chosen["name"],
            "url": f"{public_url(odoo)}/report/pdf/{chosen['report_name']}/{int(record_id)}",
            "hinweis": "Der Link oeffnet das PDF in Odoo; dort muss man angemeldet sein, die Rechte werden erneut geprueft.",
        }
        if others:
            result["weitere_berichte"] = others
        return result
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def auftrag_monteur_zuweisen(
    ctx: Context,
    order_id: int,
    monteur: str,
    bestaetigen: bool = False,
    freigabe_code: Optional[str] = None,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Teilt einem Field-Service-Auftrag (fsm.order) einen Monteur zu (person_id = 'Assigned To')."""
    tool = "auftrag_monteur_zuweisen"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        rows = odoo.read_records("fsm.order", [int(order_id)], fields=["name", "person_id"])
        if not rows:
            raise ValueError(f"Auftrag #{order_id} gibt es nicht.")
        current = rows[0].get("person_id")
        if not bestaetigen:
            code = register_pending(ctx, instance_name, tool, {"order_id": int(order_id), "monteur": monteur, "auftrag": rows[0].get("name")})
            return _card(
                tool,
                "Monteur ZUTEILEN",
                {"auftrag": rows[0].get("name"), "id": int(order_id), "vorher": current[1] if current else "keine Zuteilung", "nachher": monteur},
                f"Soll {monteur} dem Auftrag {rows[0].get('name')} zugeteilt werden?",
                code,
            )
        payload = take_pending(ctx, instance_name, tool, freigabe_code)
        person_id = fsm_person_id(ctx, odoo, payload["monteur"], instance=instance_name)
        gated_write(ctx, "fsm.order", [payload["order_id"]], {"person_id": person_id, "person_ids": [[4, person_id]]}, instance=instance_name)
        return {"success": True, "tool": tool, "auftrag": payload["auftrag"], "auftrag_id": payload["order_id"], "person_id": person_id, "summary": f"{payload['monteur']} ist dem Auftrag {payload['auftrag']} zugeteilt."}
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def auftrag_abschliessen(
    ctx: Context,
    order_id: int,
    bestaetigen: bool = False,
    freigabe_code: Optional[str] = None,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Schliesst einen Field-Service-Auftrag ab - ueber fsm.order.action_complete, nie ueber stage_id."""
    tool = "auftrag_abschliessen"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        rows = odoo.read_records("fsm.order", [int(order_id)], fields=["name", "stage_id"])
        if not rows:
            raise ValueError(f"Auftrag #{order_id} gibt es nicht.")
        stage = rows[0].get("stage_id")
        if not bestaetigen:
            code = register_pending(ctx, instance_name, tool, {"order_id": int(order_id), "auftrag": rows[0].get("name")})
            return _card(
                tool,
                "Auftrag ABSCHLIESSEN",
                {"auftrag": rows[0].get("name"), "id": int(order_id), "stufe_vorher": stage[1] if stage else "-", "stufe_nachher": "Completed"},
                f"Soll der Auftrag {rows[0].get('name')} abgeschlossen werden?",
                code,
            )
        payload = take_pending(ctx, instance_name, tool, freigabe_code)
        gated_method(ctx, "fsm.order", "action_complete", [[payload["order_id"]]], instance=instance_name)
        after = odoo.read_records("fsm.order", [payload["order_id"]], fields=["stage_id"])
        new_stage = after[0].get("stage_id") if after else None
        return {"success": True, "tool": tool, "auftrag": payload["auftrag"], "auftrag_id": payload["order_id"], "stufe": new_stage[1] if new_stage else "?", "summary": f"Auftrag {payload['auftrag']} ist abgeschlossen."}
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def _find_tax_id(odoo: Any, percent: float, move_type: str) -> Optional[int]:
    use = "sale" if move_type in ("out_invoice", "out_refund") else "purchase"
    rows = odoo.search_read("account.tax", [["type_tax_use", "=", use], ["amount", "=", percent], ["active", "=", True]], fields=["id"], limit=1)
    return int(rows[0]["id"]) if rows else None


def create_invoice(
    ctx: Context,
    partner_id: int,
    lines: List[Dict[str, Any]],
    invoice_date: Optional[str] = None,
    move_type: str = "out_invoice",
    bestaetigen: bool = False,
    freigabe_code: Optional[str] = None,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Erstellt eine Rechnung als ENTWURF (nicht gebucht).

    lines: [{"description", "quantity", "price_unit", "account_code"?, "tax_percent"?}].
    Ohne tax_percent bekommt jede Zeile den Schweizer Normalsatz 8.1 %; tax_percent 0 = ohne MwSt.
    Immer: Karte zuerst, dann bestaetigen=true + freigabe_code.
    """
    tool = "create_invoice"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        if move_type not in ("out_invoice", "in_invoice", "out_refund"):
            raise ValueError("move_type muss out_invoice, in_invoice oder out_refund sein.")
        if not lines:
            raise ValueError("Mindestens eine Rechnungszeile noetig.")
        total = 0.0
        invoice_lines: List[Any] = []
        tax_notes: set[str] = set()
        tax_cache: Dict[float, Optional[int]] = {}
        for line in lines:
            qty = float(line.get("quantity", 1) or 1)
            price = float(line.get("price_unit", 0) or 0)
            total += qty * price
            lvals: Dict[str, Any] = {"name": str(line.get("description", "Position")), "quantity": qty, "price_unit": price}
            code = line.get("account_code")
            if code:
                acc = odoo.search_read("account.account", [["code", "=like", str(code) + "%"]], fields=["id"], limit=1)
                if acc:
                    lvals["account_id"] = int(acc[0]["id"])
            percent = line.get("tax_percent")
            percent = 8.1 if percent is None else float(percent)
            if percent > 0:
                if percent not in tax_cache:
                    tax_cache[percent] = _find_tax_id(odoo, percent, move_type)
                if tax_cache[percent]:
                    lvals["tax_ids"] = [[6, 0, [tax_cache[percent]]]]
                    tax_notes.add(f"{percent}% MWST")
                else:
                    tax_notes.add(f"ACHTUNG: kein {percent}%-Steuersatz in Odoo gefunden - Zeile OHNE MwSt")
            else:
                lvals["tax_ids"] = [[6, 0, []]]
                tax_notes.add("ohne MwSt (explizit)")
            invoice_lines.append([0, 0, lvals])
        partner = odoo.read_records("res.partner", [int(partner_id)], fields=["name"])
        partner_name = partner[0]["name"] if partner else f"ID {partner_id}"
        values = {
            "move_type": move_type,
            "partner_id": int(partner_id),
            "invoice_date": invoice_date or date.today().isoformat(),
            "invoice_line_ids": invoice_lines,
        }
        if not bestaetigen:
            code = register_pending(ctx, instance_name, tool, {"values": values, "partner": partner_name, "total": round(total, 2), "mwst": sorted(tax_notes)})
            return _card(
                tool,
                "Rechnung erstellen (Entwurf)",
                {"partner": partner_name, "betrag_chf": round(total, 2), "zeilen": len(invoice_lines), "mwst": sorted(tax_notes), "schwelle_chf": chf_schwelle(), "ueber_schwelle": total > chf_schwelle()},
                f"Rechnung ueber CHF {total:,.2f} an {partner_name} als Entwurf anlegen?",
                code,
            )
        payload = take_pending(ctx, instance_name, tool, freigabe_code)
        move_id = gated_create(ctx, "account.move", payload["values"], instance=instance_name)
        info = odoo.read_records("account.move", [move_id], fields=["name", "state", "amount_total"])
        info0 = info[0] if info else {"name": "/", "state": "draft", "amount_total": payload["total"]}
        label = info0["name"] if info0.get("name") and info0["name"] != "/" else f"#{move_id}"
        return {
            "success": True,
            "tool": tool,
            "move_id": move_id,
            "state": info0.get("state"),
            "amount_total": info0.get("amount_total"),
            "mwst": payload["mwst"],
            "summary": f"Rechnungs-ENTWURF {label} ueber CHF {float(info0.get('amount_total') or 0):,.2f} an {payload['partner']} erstellt. Noch NICHT gebucht.",
        }
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def post_journal_entry(ctx: Context, move_id: int, bestaetigen: bool = False, freigabe_code: Optional[str] = None, instance: Optional[str] = None) -> Dict[str, Any]:
    """Bucht einen Beleg (Entwurf -> gebucht) ueber account.move.action_post. Immer mit Karte."""
    tool = "post_journal_entry"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        rows = odoo.read_records("account.move", [int(move_id)], fields=["name", "state", "amount_total", "partner_id", "move_type"])
        if not rows:
            raise ValueError(f"Beleg {move_id} nicht gefunden.")
        info = rows[0]
        if info.get("state") == "posted":
            return {"success": True, "tool": tool, "move_id": int(move_id), "state": "posted", "summary": f"{info.get('name')} ist bereits gebucht."}
        if not bestaetigen:
            code = register_pending(ctx, instance_name, tool, {"move_id": int(move_id), "name": info.get("name")})
            return _card(
                tool,
                "Beleg BUCHEN",
                {"beleg": info.get("name"), "betrag_chf": info.get("amount_total"), "partner": info["partner_id"][1] if info.get("partner_id") else "-", "typ": info.get("move_type")},
                f"Bist du sicher, dass {info.get('name')} ueber CHF {float(info.get('amount_total') or 0):,.2f} GEBUCHT werden soll?",
                code,
            )
        payload = take_pending(ctx, instance_name, tool, freigabe_code)
        gated_method(ctx, "account.move", "action_post", [[payload["move_id"]]], instance=instance_name)
        after = odoo.read_records("account.move", [payload["move_id"]], fields=["name", "state"])
        state = after[0].get("state") if after else "?"
        return {"success": True, "tool": tool, "move_id": payload["move_id"], "state": state, "summary": f"{(after[0].get('name') if after else payload['name'])} wurde gebucht."}
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def pay_invoice(ctx: Context, move_id: int, bestaetigen: bool = False, freigabe_code: Optional[str] = None, instance: Optional[str] = None) -> Dict[str, Any]:
    """Registriert die Zahlung einer gebuchten Rechnung (Zahlungsassistent). Immer mit Karte."""
    tool = "pay_invoice"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        rows = odoo.read_records("account.move", [int(move_id)], fields=["name", "state", "amount_residual", "partner_id"])
        if not rows:
            raise ValueError(f"Beleg {move_id} nicht gefunden.")
        info = rows[0]
        if info.get("state") != "posted":
            raise ValueError(f"{info.get('name')} ist nicht gebucht - erst buchen, dann zahlen.")
        if not bestaetigen:
            code = register_pending(ctx, instance_name, tool, {"move_id": int(move_id), "name": info.get("name")})
            return _card(
                tool,
                "Rechnung ZAHLEN",
                {"beleg": info.get("name"), "offen_chf": info.get("amount_residual"), "partner": info["partner_id"][1] if info.get("partner_id") else "-"},
                f"Bist du sicher, dass fuer {info.get('name')} eine Zahlung ueber CHF {float(info.get('amount_residual') or 0):,.2f} registriert werden soll?",
                code,
            )
        payload = take_pending(ctx, instance_name, tool, freigabe_code)
        wizard_ctx = {"active_model": "account.move", "active_ids": [payload["move_id"]]}
        # The gate refuses empty create payloads; the wizard derives everything
        # else from the context, so the payment date is the one explicit value.
        wizard_id = gated_create(ctx, "account.payment.register", {"payment_date": date.today().isoformat()}, instance=instance_name, context=wizard_ctx)
        gated_method(ctx, "account.payment.register", "action_create_payments", [[wizard_id]], {"context": wizard_ctx}, instance=instance_name)
        after = odoo.read_records("account.move", [payload["move_id"]], fields=["name", "payment_state"])
        return {"success": True, "tool": tool, "move_id": payload["move_id"], "payment_state": after[0].get("payment_state") if after else "?", "summary": f"Zahlung fuer {(after[0].get('name') if after else payload['name'])} registriert."}
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


def einsatzrapport_erstellen(
    ctx: Context,
    kunde: Dict[str, Any],
    standort: Dict[str, Any],
    beschreibung: str,
    zeiten: Optional[List[Dict[str, Any]]] = None,
    material: Optional[List[Dict[str, Any]]] = None,
    zuschlaege: Optional[List[str]] = None,
    monteur: Optional[str] = None,
    bestaetigen: bool = False,
    freigabe_code: Optional[str] = None,
    instance: Optional[str] = None,
) -> Dict[str, Any]:
    """Legt einen vollstaendigen Field-Service-Einsatz an (Kunde, Standort, Auftrag, Zeiten, Material, Zuschlaege).

    kunde {"name","street","zip","city","email","phone"} - standort {"name","street","zip","city"}
    zeiten [{"monteur","typ","stunden","datum","text"}] - material [{"produkt","menge","edv_nr","preis"}]
    zuschlaege ["25% Zuschlag", ...] - monteur: Name (Standard: erster Monteur aus zeiten).
    Ein bestaetigter Aufruf legt alles an; fehlende Stammdaten werden angelegt.
    """
    tool = "einsatzrapport_erstellen"
    try:
        api = _core()
        instance_name, odoo = api._resolve_odoo(ctx, instance)
        plan = {"kunde": kunde or {}, "standort": standort or {}, "beschreibung": beschreibung, "zeiten": zeiten or [], "material": material or [], "zuschlaege": zuschlaege or [], "monteur": monteur}
        if not plan["kunde"].get("name") or not plan["standort"].get("name"):
            raise ValueError("kunde.name und standort.name sind Pflicht.")
        if not bestaetigen:
            code = register_pending(ctx, instance_name, tool, plan)
            return _card(
                tool,
                "Einsatzrapport ANLEGEN",
                {"kunde": plan["kunde"].get("name"), "standort": plan["standort"].get("name"), "beschreibung": beschreibung, "zeitzeilen": len(plan["zeiten"]), "materialzeilen": len(plan["material"]), "zuschlaege": plan["zuschlaege"], "monteur": monteur or next((z.get("monteur") for z in plan["zeiten"] if z.get("monteur")), None)},
                "Soll dieser Einsatz mit allen Zeilen angelegt werden?",
                code,
            )
        plan = take_pending(ctx, instance_name, tool, freigabe_code)
        protocol: List[str] = []
        k = plan["kunde"]
        kunde_id = find_or_create(ctx, odoo, "res.partner", [["name", "=", k["name"]]], {key: value for key, value in {"name": k.get("name"), "street": k.get("street"), "zip": k.get("zip"), "city": k.get("city"), "email": k.get("email"), "phone": k.get("phone"), "is_company": True}.items() if value}, instance=instance_name)
        protocol.append(f"Kunde {k['name']} (#{kunde_id})")
        s = plan["standort"]
        loc = odoo.search_read("fsm.location", [["name", "=", s["name"]]], fields=["id", "partner_id"], limit=1)
        if loc:
            ort_id = int(loc[0]["id"])
        else:
            ort_id = gated_create(ctx, "fsm.location", {key: value for key, value in {"name": s.get("name"), "owner_id": kunde_id, "street": s.get("street"), "zip": s.get("zip"), "city": s.get("city")}.items() if value}, instance=instance_name)
            fresh = odoo.search_read("fsm.location", [["id", "=", ort_id]], fields=["partner_id"], limit=1)
            if fresh and fresh[0].get("partner_id"):
                gated_write(ctx, "res.partner", [int(fresh[0]["partner_id"][0])], {"parent_id": kunde_id}, instance=instance_name)
        protocol.append(f"Standort {s['name']} (#{ort_id})")
        order_values: Dict[str, Any] = {"location_id": ort_id, "description": plan["beschreibung"]}
        monteur_name = plan.get("monteur") or next((z.get("monteur") for z in plan["zeiten"] if z.get("monteur")), None)
        if monteur_name:
            person_id = fsm_person_id(ctx, odoo, monteur_name, instance=instance_name)
            order_values["person_id"] = person_id
            order_values["person_ids"] = [[4, person_id]]
        order_id = gated_create(ctx, "fsm.order", order_values, instance=instance_name)
        order = odoo.search_read("fsm.order", [["id", "=", order_id]], fields=["name"], limit=1)
        number = order[0]["name"] if order else str(order_id)
        protocol.append(f"Einsatz {number} (#{order_id})")
        for z in plan["zeiten"]:
            employee_id = find_or_create(ctx, odoo, "hr.employee", [["name", "=", z.get("monteur")]], {"name": z.get("monteur")}, instance=instance_name)
            line = {"name": z.get("text") or z.get("typ") or "Arbeit", "employee_id": employee_id, "unit_amount": float(z.get("stunden") or 0), "date": z.get("datum") or date.today().isoformat(), "fsm_order_id": order_id}
            if z.get("typ"):
                line["time_type_id"] = find_or_create(ctx, odoo, "project.time.type", [["name", "=", z["typ"]]], {"name": z["typ"]}, instance=instance_name)
            gated_create(ctx, "account.analytic.line", line, instance=instance_name)
        if plan["zeiten"]:
            protocol.append(f"{len(plan['zeiten'])} Zeitzeilen")
        for m in plan["material"]:
            product_id = find_or_create(ctx, odoo, "product.product", [["name", "=", m.get("produkt")]], {"name": m.get("produkt"), "type": "consu", "list_price": float(m.get("preis") or 0)}, instance=instance_name)
            line = {"order_id": order_id, "product_id": product_id, "quantity": float(m.get("menge") or 1)}
            if m.get("edv_nr"):
                line["edv_number"] = str(m["edv_nr"])
            if m.get("preis"):
                line["price_unit"] = float(m["preis"])
            gated_create(ctx, "fsm.material.line", line, instance=instance_name)
        if plan["material"]:
            protocol.append(f"{len(plan['material'])} Materialzeilen")
        for tag in plan["zuschlaege"]:
            tag_id = find_or_create(ctx, odoo, "fsm.gav.tag", [["name", "=", tag]], {"name": tag}, instance=instance_name)
            gated_create(ctx, "fsm.gav.line", {"order_id": order_id, "tag_id": tag_id}, instance=instance_name)
        if plan["zuschlaege"]:
            protocol.append(f"{len(plan['zuschlaege'])} Zuschlaege")
        return {
            "success": True,
            "tool": tool,
            "auftrag": number,
            "auftrag_id": order_id,
            "angelegt": protocol,
            "rapport_url": f"{public_url(odoo)}/report/pdf/fieldservice_customer_report.report_fsm_customer/{order_id}",
            "summary": f"Einsatz {number} angelegt: " + ", ".join(protocol),
        }
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, exc)


# --- registration --------------------------------------------------------------

READ_TOOLS: List[tuple[Callable[..., Any], str]] = [
    (aktuelles_datum, "Heutiges Datum/Uhrzeit in Schweizer Zeit mit Wochentag, morgen, uebermorgen - fuer relative Datumsangaben zuerst aufrufen"),
    (get_account_by_code, "Konto im Schweizer KMU-Kontenrahmen nachschlagen (nur lesen)"),
    (bericht_link, "Adresse des PDF-Berichts zu einem Datensatz liefern (nur lesen, kein Download)"),
]
WRITE_TOOLS: List[tuple[Callable[..., Any], str]] = [
    (termin_buchen, "Kalender-Termin anlegen: 1. Aufruf = Karte + freigabe_code, 2. Aufruf mit bestaetigen=true + freigabe_code = anlegen. Zeiten in Schweizer Zeit."),
    (create_partner, "Kontakt (Kunde/Lieferant) anlegen: Karte, dann bestaetigen=true + freigabe_code."),
    (auftrag_monteur_zuweisen, "Field-Service-Auftrag einem Monteur zuteilen: Karte, dann bestaetigen=true + freigabe_code."),
    (auftrag_abschliessen, "Field-Service-Auftrag abschliessen (action_complete): Karte, dann bestaetigen=true + freigabe_code."),
    (create_invoice, "Rechnung als Entwurf anlegen (CH-MwSt 8.1 % Standard): Karte, dann bestaetigen=true + freigabe_code."),
    (post_journal_entry, "Beleg/Rechnung buchen (action_post): Karte, dann bestaetigen=true + freigabe_code."),
    (pay_invoice, "Zahlung fuer eine gebuchte Rechnung registrieren: Karte, dann bestaetigen=true + freigabe_code."),
    (einsatzrapport_erstellen, "Kompletten Field-Service-Einsatz anlegen (Kunde, Standort, Auftrag, Zeiten, Material, Zuschlaege): Karte, dann bestaetigen=true + freigabe_code."),
]


def register(api: Any) -> None:
    """Entry point ``odoo_mcp.tools`` -> registers the Algorithma tools on the shared server."""
    from odoo_mcp.server_core import DESTRUCTIVE_TOOL

    for fn, description in READ_TOOLS:
        api.tool(description=description, annotations=api.READ_ONLY_TOOL, structured_output=True)(fn)
    for fn, description in WRITE_TOOLS:
        api.tool(description=description, annotations=DESTRUCTIVE_TOOL, structured_output=True)(fn)
