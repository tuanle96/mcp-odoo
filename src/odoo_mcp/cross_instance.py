"""Pure fan-out planning and result-merging for cross-instance queries.

The MCP surface (``tools_cross_instance``) handles concurrency, RPC, field
ACL, rate limiting, and audit; this module is the testable core: which
instances to query, how to tag and merge their results, and how to combine
additive aggregates. No MCP or Odoo imports.

v1 scope is deliberately fan-out + merge over already-configured instances —
no warehouse, no data sync. Read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Union

DEFAULT_LIMIT_PER_INSTANCE = 50
MAX_LIMIT_PER_INSTANCE = 100
INSTANCE_TAG_KEY = "_instance"


@dataclass(frozen=True)
class InstanceMeta:
    name: str
    tags: FrozenSet[str]
    cross_instance: bool


@dataclass(frozen=True)
class Selection:
    selected: List[str]
    skipped_opt_out: List[str]
    unknown: List[str]


def parse_instances_meta(
    summary: Mapping[str, Mapping[str, Any]],
) -> Dict[str, InstanceMeta]:
    """Build InstanceMeta from a list_configured_instances-style summary.

    ``cross_instance`` defaults to True (opt-out, not opt-in); ``tags``
    defaults to empty.
    """
    metas: Dict[str, InstanceMeta] = {}
    for name, entry in summary.items():
        raw_tags = entry.get("tags") or []
        tags = frozenset(str(t).strip() for t in raw_tags if str(t).strip())
        cross = entry.get("cross_instance")
        metas[name] = InstanceMeta(
            name=name,
            tags=tags,
            cross_instance=True if cross is None else bool(cross),
        )
    return metas


def select_instances(
    requested: Union[None, str, Sequence[str], Mapping[str, Any]],
    metas: Mapping[str, InstanceMeta],
) -> Selection:
    """Resolve the target instance set, honoring opt-out and tags.

    ``requested`` may be:
    - ``None`` or ``"all"`` -> every opted-in instance.
    - a list of names -> those names (an explicitly named opted-out instance
      is reported under ``skipped_opt_out``, an unknown name under ``unknown``).
    - ``{"tags": [...]}`` -> opted-in instances matching any tag.
    """
    opted_in = {name for name, m in metas.items() if m.cross_instance}

    if requested is None or requested == "all":
        return Selection(sorted(opted_in), [], [])

    if isinstance(requested, Mapping):
        wanted_tags = {
            str(t).strip() for t in (requested.get("tags") or []) if str(t).strip()
        }
        by_tag = sorted(
            name for name in opted_in if metas[name].tags & wanted_tags
        )
        return Selection(by_tag, [], [])

    # Explicit list of names.
    names = [str(n) for n in requested]
    selected: List[str] = []
    skipped: List[str] = []
    unknown: List[str] = []
    for name in names:
        meta = metas.get(name)
        if meta is None:
            unknown.append(name)
        elif not meta.cross_instance:
            skipped.append(name)
        else:
            selected.append(name)
    return Selection(selected, skipped, unknown)


def tag_and_merge(
    results_by_instance: Mapping[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Flatten per-instance record lists into one list, tagging each row."""
    merged: List[Dict[str, Any]] = []
    for name, records in results_by_instance.items():
        for record in records:
            merged.append({**record, INSTANCE_TAG_KEY: name})
    return merged


def combine_aggregate_rows(
    rows_by_instance: Mapping[str, List[Dict[str, Any]]],
    measure_fields: Sequence[str],
) -> Dict[str, Any]:
    """Grand totals of additive measures summed across every instance's rows.

    Returns combined sums for each ``measure_field`` plus a combined record
    count. avg-style measures are NOT combined here (an average of averages
    is wrong without weights) — callers should request sum/count measures for
    cross-instance totals and read per-instance rows for drill-down.
    """
    totals: Dict[str, float] = {field: 0.0 for field in measure_fields}
    combined_count = 0
    for rows in rows_by_instance.values():
        for row in rows:
            count = row.get("__count")
            if isinstance(count, (int, float)):
                combined_count += int(count)
            for field in measure_fields:
                value = row.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[field] += float(value)
    return {
        "combined_count": combined_count,
        "combined_measures": {k: round(v, 2) for k, v in totals.items()},
    }


def combine_bucket_reports(
    reports_by_instance: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Sum aging-bucket totals across instances (the AR/AP sweep use case)."""
    combined_buckets: Dict[str, float] = {}
    combined_outstanding = 0.0
    for report in reports_by_instance.values():
        for bucket, amount in (report.get("buckets") or {}).items():
            if isinstance(amount, (int, float)):
                combined_buckets[bucket] = combined_buckets.get(bucket, 0.0) + float(
                    amount
                )
        total = report.get("total_outstanding")
        if isinstance(total, (int, float)):
            combined_outstanding += float(total)
    return {
        "combined_buckets": {k: round(v, 2) for k, v in combined_buckets.items()},
        "combined_total_outstanding": round(combined_outstanding, 2),
    }


def envelope(
    results: Dict[str, Any],
    errors: Dict[str, str],
    selection: Selection,
    elapsed_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Shape the partial-failure response contract shared by all fan-out tools."""
    payload: Dict[str, Any] = {
        "success": True,
        "instances_queried": sorted(results) + sorted(errors),
        "instance_count": len(results) + len(errors),
        "errors": errors,
        "results": results,
    }
    if selection.skipped_opt_out:
        payload["skipped_opt_out"] = selection.skipped_opt_out
    if selection.unknown:
        payload["unknown_instances"] = selection.unknown
    if elapsed_ms is not None:
        payload["elapsed_ms"] = round(elapsed_ms, 1)
    return payload
