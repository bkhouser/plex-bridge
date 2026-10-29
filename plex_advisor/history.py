from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from plex_advisor.database import Database, historical_scope, history_scope
from plex_advisor.models import HistoryQuery


def watch_history(db: Database, query: HistoryQuery, timezone: str) -> dict[str, Any]:
    clauses, args = [history_scope()], []
    for value, expression in [
        (query.start, "e.viewed_at>=?"),
        (query.end, "e.viewed_at<?"),
        (query.media_type, "e.media_type=?"),
        (query.media_id, "e.server_id || ':' || e.rating_key=?"),
        (query.show_id, "e.server_id || ':' || e.grandparent_key=?"),
        (
            query.season_number,
            "COALESCE(e.season_number,json_extract(h.metadata,'$.season_number'))=?",
        ),
        (
            query.episode_number,
            "COALESCE(e.episode_number,json_extract(h.metadata,'$.episode_number'))=?",
        ),
    ]:
        if value is not None:
            clauses.append(expression)
            args.append(value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value)
    where = " AND ".join(clauses)
    zone = ZoneInfo(query.timezone or timezone)
    tables = (
        "FROM viewing_events e LEFT JOIN historical_event_evidence h "
        "ON h.server_id=e.server_id AND h.event_id=e.event_id AND h.account_id=e.account_id "
        f"AND {historical_scope()} "
        "LEFT JOIN historical_library_mappings hm ON hm.id=h.mapping_id "
    )
    with db.connect() as conn:
        count = conn.execute(f"SELECT COUNT(*) {tables} WHERE {where}", args).fetchone()[0]
        rows = conn.execute(
            "SELECT e.*,h.metadata AS historical_metadata,h.mapping_id,"
            "h.source_section_id AS historical_section_id,hm.target_section_id "
            f"{tables} WHERE {where} ORDER BY e.viewed_at DESC,e.event_id LIMIT ? OFFSET ?",
            [*args, query.limit, query.offset],
        ).fetchall()
    import json

    items = []
    for row in rows:
        item = dict(row)
        evidence = item.pop("historical_metadata")
        source_section = item.pop("historical_section_id")
        selected_section = item.pop("target_section_id")
        item["selected_section_id"] = selected_section or item["section_id"]
        item["history_provenance"] = (
            "owner_confirmed_historical_library" if evidence else "plex_selected_library"
        )
        if evidence:
            metadata = json.loads(evidence)
            item["section_id"] = source_section
            for field in ["parent_title", "grandparent_title", "season_number", "episode_number"]:
                if item[field] is None:
                    item[field] = metadata.get(field)
            item["originally_available_at"] = metadata.get("originally_available_at")
            item["current_item_identity"] = "unlinked; availability not established"
        item["viewed_at_local"] = (
            datetime.fromisoformat(item["viewed_at"]).astimezone(zone).isoformat()
        )
        items.append(item)
    return {
        "events": items,
        "total": count,
        "timezone": str(zone),
        "next_offset": query.offset + len(items) if query.offset + len(items) < count else None,
        "completeness": "Only retrievable playback events; not a complete lifetime viewing record",
        "scope": (
            "Configured account and selected libraries, including explicitly enrolled "
            "owner-confirmed historical events; other legacy events excluded"
        ),
    }
