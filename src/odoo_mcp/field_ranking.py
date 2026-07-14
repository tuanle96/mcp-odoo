"""Field ranking and smart-field selection helpers for agent-facing tools.

Pure helpers — no network, config, or Odoo client side effects. Server
adapters pass live ``fields_get`` metadata in when they have it.

Extracted from ``agent_tools.py`` (see issue #32) to keep that module
focused on tool orchestration rather than field-scoring heuristics.
"""

from __future__ import annotations

from typing import Any

# Smart field selection — used by search_records / read_record when caller
# does not pass explicit fields. Mirrors the curation idea from competitor
# servers: keep the LLM context small by excluding technical/expensive fields
# and prioritising business-identifier columns.
DEFAULT_MAX_SMART_FIELDS = 15
_TECHNICAL_FIELD_NAMES = {
    "id",
    "create_uid",
    "create_date",
    "write_uid",
    "write_date",
    "__last_update",
    "display_name",
}
_TECHNICAL_FIELD_PREFIXES = (
    "message_",
    "activity_",
    "website_message_",
    "website_meta_",
)
_PRIORITY_FIELD_NAMES: tuple[tuple[str, int], ...] = (
    ("name", 100),
    ("code", 95),
    ("ref", 95),
    ("default_code", 95),
    ("barcode", 90),
    ("login", 90),
    ("email", 90),
    ("phone", 85),
    ("mobile", 85),
    ("state", 85),
    ("status", 85),
    ("stage_id", 85),
    ("kanban_state", 80),
    ("active", 80),
    ("partner_id", 75),
    ("user_id", 75),
    ("employee_id", 75),
    ("company_id", 70),
    ("currency_id", 70),
    ("amount_total", 70),
    ("amount_untaxed", 65),
    ("price_unit", 65),
    ("price_total", 65),
    ("quantity", 60),
    ("product_id", 60),
    ("product_uom_id", 55),
    ("date", 55),
    ("date_order", 55),
    ("date_invoice", 55),
    ("invoice_date", 55),
    ("date_deadline", 55),
    ("date_start", 55),
    ("date_end", 55),
    ("scheduled_date", 55),
)
_PRIORITY_FIELD_LOOKUP = dict(_PRIORITY_FIELD_NAMES)


def _is_technical_field_name(field_name: str) -> bool:
    if field_name in _TECHNICAL_FIELD_NAMES:
        return True
    return field_name.startswith(_TECHNICAL_FIELD_PREFIXES)


def _is_skip_metadata(meta: dict[str, Any]) -> bool:
    if meta.get("automatic"):
        return True
    field_type = str(meta.get("type", ""))
    if field_type == "binary":
        return True
    if meta.get("compute") and not meta.get("store", True):
        return True
    if (
        field_type in {"one2many", "many2many"}
        and meta.get("compute")
        and not meta.get("store", True)
    ):  # pragma: no cover - already handled by the generic compute+!store branch above
        return True
    return False


def _smart_field_score(field_name: str, meta: dict[str, Any]) -> int:
    score = _PRIORITY_FIELD_LOOKUP.get(field_name)
    if score is not None:
        return score
    if meta.get("tracking"):
        return 50
    field_type = str(meta.get("type", ""))
    if field_name.endswith("_id") and field_type == "many2one":
        return 45
    if field_type == "datetime" or field_type == "date" or "date" in field_name:
        return 40
    if field_type in {"selection", "boolean"}:
        return 35
    if field_type in {"char", "text", "html"}:
        return 25
    if field_type in {"integer", "float", "monetary"}:
        return 25
    if field_type in {"one2many", "many2many"}:
        return 5
    return 10


DEFAULT_MAX_RELEVANT_FIELDS = 30


def rank_relevant_fields(
    fields_metadata: dict[str, Any],
    max_fields: int = DEFAULT_MAX_RELEVANT_FIELDS,
) -> list[dict[str, Any]]:
    """Rank fields by business relevance for schema exploration.

    Builds on the smart-field heuristics and boosts fields that are
    required or searchable, so agents inspecting wide models (res.partner
    has hundreds of fields) can start from the ones that matter.
    """
    if max_fields <= 0:
        return []

    scored: list[dict[str, Any]] = []
    for field_name, raw_meta in fields_metadata.items():
        if not isinstance(raw_meta, dict):
            continue
        if _is_technical_field_name(field_name):
            continue
        if _is_skip_metadata(raw_meta):
            continue
        score = _smart_field_score(field_name, raw_meta)
        if raw_meta.get("required"):
            score += 30
        if raw_meta.get("searchable"):
            score += 5
        scored.append({"field": field_name, "score": score})

    scored.sort(key=lambda item: (-item["score"], item["field"]))
    return scored[:max_fields]


def select_smart_fields(
    fields_metadata: dict[str, Any],
    max_fields: int = DEFAULT_MAX_SMART_FIELDS,
    *,
    always_include: list[str] | None = None,
) -> list[str]:
    """Return a curated subset of fields for LLM-friendly reads.

    The selection drops audit/computed/binary noise and prefers
    business-identifier columns (name, code, state, partner_id, ...).
    Returns at most ``max_fields`` field names; ``id`` is always present
    when the model exposes it.
    """
    if max_fields <= 0:
        return []

    forced: list[str] = ["id"]
    if always_include:
        for name in always_include:
            if name not in forced and name in fields_metadata:
                forced.append(name)

    candidates: list[tuple[int, str]] = []
    for field_name, raw_meta in fields_metadata.items():
        if field_name in forced:
            continue
        if _is_technical_field_name(field_name):
            continue
        if not isinstance(raw_meta, dict):
            # Corrupt metadata entry — skip rather than guess.
            continue
        if _is_skip_metadata(raw_meta):
            continue
        score = _smart_field_score(field_name, raw_meta)
        candidates.append((score, field_name))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = list(forced)
    for _, name in candidates:
        if len(selected) >= max_fields:
            break
        if (
            name in selected
        ):  # pragma: no cover - defensive; forced names are pre-filtered from candidates
            continue
        selected.append(name)
    return selected


DEFAULT_MAX_QUERY_FIELDS = 5

# Identifier-style columns checked first when building a free-text query
# domain; ordered by how often they hold the text a human searches for.
_TEXT_QUERY_PRIORITY_FIELDS = (
    "name",
    "display_name",
    "complete_name",
    "default_code",
    "ref",
    "code",
    "email",
    "phone",
    "mobile",
    "barcode",
    "login",
)


def select_text_query_fields(
    fields_metadata: dict[str, Any],
    max_fields: int = DEFAULT_MAX_QUERY_FIELDS,
) -> list[str]:
    """Pick searchable text fields for a free-text query, identifiers first.

    Only ``char``/``text`` fields that Odoo reports as searchable qualify.
    Known identifier columns (name, ref, email, ...) win; otherwise the
    highest-scoring text fields by the smart-field heuristics are used.
    """
    if max_fields <= 0:
        return []

    searchable: dict[str, dict[str, Any]] = {}
    for field_name, raw_meta in fields_metadata.items():
        if not isinstance(raw_meta, dict):
            continue
        if str(raw_meta.get("type", "")) not in {"char", "text"}:
            continue
        if not raw_meta.get("searchable", raw_meta.get("store", True)):
            continue
        searchable[field_name] = raw_meta

    selected = [name for name in _TEXT_QUERY_PRIORITY_FIELDS if name in searchable]
    if not selected and searchable:
        selected = sorted(
            searchable,
            key=lambda name: (-_smart_field_score(name, searchable[name]), name),
        )
    return selected[:max_fields]


def build_text_query_domain(
    query: str,
    fields_metadata: dict[str, Any] | None = None,
    max_fields: int = DEFAULT_MAX_QUERY_FIELDS,
) -> tuple[list[Any], list[str]]:
    """Build an OR ``ilike`` domain matching ``query`` across text fields.

    Returns ``(domain, fields_used)`` where the domain is in Odoo prefix
    notation (``['|', '|', term, term, term]``) and safe to concatenate
    with another domain under Odoo's implicit AND. Falls back to ``name``
    when no metadata is available (``display_name`` is not searchable on
    Odoo 16). SQL LIKE wildcards (``%``, ``_``) in the query are passed
    through unescaped, matching Odoo's own search-bar behavior.
    """
    cleaned = str(query).strip()
    if not cleaned:
        raise ValueError("query must be a non-empty string")

    field_names = select_text_query_fields(fields_metadata or {}, max_fields)
    if not field_names:
        field_names = ["name"]

    domain: list[Any] = ["|"] * (len(field_names) - 1)
    domain.extend([field_name, "ilike", cleaned] for field_name in field_names)
    return domain, field_names
