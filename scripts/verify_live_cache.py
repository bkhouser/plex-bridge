"""Compare the enrolled cache with fresh, read-only Plex API responses.

The report contains personal metadata but never credentials. Keep it under data/.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from plex_advisor.catalog import Catalog
from plex_advisor.config import Settings
from plex_advisor.database import Database, history_scope
from plex_advisor.history import watch_history
from plex_advisor.models import AvailabilityTitle, HistoryQuery, Search, utc_now
from plex_advisor.plex_client import PlexSource
from plex_advisor.security import configure_logging


def compare_cached_history(db: Database, source: PlexSource) -> tuple[int, int]:
    live_events = matched_events = 0
    # Retained/recovered cache history can legitimately exceed today's Plex history.
    # Validate retrievable selected-library events, not an unscoped all-library total.
    with db.connect() as conn:
        for event in source.history(None):
            live_events += 1
            cached_event = conn.execute(
                "SELECT e.* FROM viewing_events e WHERE e.server_id=? "
                "AND e.event_id=? AND e.account_id=? AND " + history_scope(),
                (event.server_id, event.event_id, event.account_id),
            ).fetchone()
            if cached_event and all(
                cached_event[key] == getattr(event, key)
                for key in ("viewed_at", "rating_key", "media_type")
            ):
                matched_events += 1
    return live_events, matched_events


def verify(expected_email: str | None) -> dict[str, Any]:
    config = Settings()
    configure_logging("ERROR")
    source = PlexSource(config)
    capabilities = source.connect()
    account = source.owner.myPlexAccount()
    if expected_email and account.email.casefold() != expected_email.casefold():
        raise ValueError("Account email mismatch")
    db = Database(config.database_path)
    catalog = Catalog(db)
    checks: list[dict[str, Any]] = []
    for section_id in config.library_sections:
        types = (
            [(1, "movie")]
            if source.sections[section_id] == "movie"
            else [(2, "show"), (3, "season"), (4, "episode")]
        )
        for type_id, media_type in types:
            root = source.server.query(
                f"/library/sections/{section_id}/all",
                params={"type": type_id},
                headers={"X-Plex-Container-Start": "0", "X-Plex-Container-Size": "1"},
            )
            live_total = int(root.attrib["totalSize"])
            with db.connect() as conn:
                cached = conn.execute(
                    "SELECT COUNT(*) FROM media_items WHERE section_id=? "
                    "AND media_type=? AND available=1",
                    (section_id, media_type),
                ).fetchone()[0]
            checks.append(
                {
                    "section_id": section_id,
                    "media_type": media_type,
                    "live_count": live_total,
                    "cached_count": cached,
                    "matches": live_total == cached,
                }
            )
    with db.connect() as conn:
        states = conn.execute(
            "SELECT m.media_type,COUNT(*) entries,SUM(s.watched=1) watched,"
            "SUM(s.watched=0) unwatched,COUNT(s.personal_rating) personally_rated "
            "FROM media_items m JOIN user_media_state s ON s.media_id=m.id "
            "WHERE m.available=1 GROUP BY m.media_type"
        ).fetchall()
        identities = conn.execute(
            "SELECT account_id,COUNT(*) events FROM viewing_events GROUP BY account_id"
        ).fetchall()
    if any(r["account_id"] != source.history_account_id for r in identities):
        raise ValueError("Unexpected cached history identity")
    rated_samples = []
    for section_id in config.library_sections:
        if source.sections[section_id] != "movie":
            continue
        root = source.server.query(
            f"/library/sections/{section_id}/all",
            params={"type": 1, "sort": "userRating:desc"},
            headers={"X-Plex-Container-Start": "0", "X-Plex-Container-Size": "5"},
        )
        for node in root:
            raw = node.attrib
            detail = catalog.details(f"{source.server_id}:{raw['ratingKey']}")
            raw_rating = float(raw["userRating"]) if "userRating" in raw else None
            raw_count = int(raw.get("viewCount", "0"))
            rated_samples.append(
                {
                    "title": raw["title"],
                    "year": detail["year"],
                    "live_personal_rating": raw_rating,
                    "cached_personal_rating": detail["personal_rating"],
                    "live_view_count": raw_count,
                    "cached_view_count": detail["view_count"],
                    "watched": detail["watched"],
                    "critic_rating": detail["critic_rating"],
                    "audience_rating": detail["audience_rating"],
                    "matches": raw_rating == detail["personal_rating"]
                    and raw_count == detail["view_count"]
                    and detail["watched"] == (raw_count > 0),
                }
            )
    if source.history_account_id is None:
        raise ValueError("History identity is not verified")
    history = watch_history(db, HistoryQuery(limit=5), config.timezone)
    live_events, matched_events = compare_cached_history(db, source)
    capabilities["history_access"] = "verified"
    availability = catalog.availability(
        [AvailabilityTitle(title=x) for x in ["The Fugitive", "Ronin", "Edge of Tomorrow"]]
    )
    example = catalog.search(Search(genre="Science Fiction", year_min=2010, watched=False, limit=1))
    report = {
        "checked_at": utc_now(),
        "username": config.plex_username,
        "email_matches_expected": True if expected_email else None,
        "cloud_account_id": config.plex_account_id,
        "history_account_id": source.history_account_id,
        "capabilities": capabilities,
        "catalog_counts": checks,
        "current_states": [dict(x) for x in states],
        "rating_samples": rated_samples,
        "history": {
            "live_selected_events": live_events,
            "cached_selected_and_recovered_events": history["total"],
            "matched_live_events": matched_events,
            "all_retrievable_events_match": matched_events == live_events,
            "note": "Retained/recovered cache events need not remain retrievable from Plex",
            "account_counts": [dict(x) for x in identities],
            "recent_events": [
                {k: e[k] for k in ["title", "media_type", "viewed_at_local"]}
                for e in history["events"]
            ],
        },
        "availability": [
            {
                "title": r["requested"]["title"],
                "status": r["status"],
                "matches": [
                    {k: m[k] for k in ["title", "year", "watched", "personal_rating"]}
                    for m in r["matches"]
                ],
            }
            for r in availability["results"]
        ],
        "unwatched_science_fiction_since_2010": {
            "copies": example["total_copies"],
            "titles": example["total_titles"],
        },
        "freshness": db.status(config.sync_interval_seconds),
    }
    report["all_comparisons_passed"] = all(x["matches"] for x in checks + rated_samples) and (
        live_events == matched_events
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-email")
    args = parser.parse_args()
    try:
        report = verify(args.expected_email)
        destination = Path("data/live-verification.json")
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "all_comparisons_passed": report["all_comparisons_passed"],
                    "private_report": str(destination),
                },
                indent=2,
            )
        )
        sys.exit(0 if report["all_comparisons_passed"] else 1)
    except Exception as exc:
        print(json.dumps({"error": "Live verification failed", "category": type(exc).__name__}))
        sys.exit(1)
