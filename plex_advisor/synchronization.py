import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from typing import Any, Protocol

from plex_advisor.config import Settings
from plex_advisor.database import Database
from plex_advisor.errors import PLEX_COMPATIBILITY_HELP, PlexCompatibilityError
from plex_advisor.models import Media, ViewingEvent, normalize_title, utc_now


class Source(Protocol):
    server_id: str
    history_account_id: int | None

    def connect(self) -> dict[str, Any]: ...
    def media(self, section_id: int) -> Iterator[Media]: ...
    def history(self, since: str | None) -> Iterator[ViewingEvent]: ...


def bind_configuration(db: Database, config: Settings) -> None:
    db.bind(
        {
            "plex_origin": config.plex_url.rstrip("/"),
            "account_id": config.plex_account_id,
            "username": config.plex_username,
            "server_id": config.plex_server_id,
            "history_account_id": config.plex_history_account_id,
            "history_account_name": config.plex_history_account_name,
            "history_binding_confirmed": config.history_binding_confirmed,
            "sections": sorted(set(config.library_sections)),
        }
    )


def save_media(conn: sqlite3.Connection, item: Media, run: str) -> int:
    media_id = f"{item.server_id}:{item.rating_key}"
    payload = item.model_dump()
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    previous = conn.execute(
        "SELECT fingerprint,available FROM media_items WHERE id=?", (media_id,)
    ).fetchone()
    if previous and previous[0] == fingerprint and previous[1]:
        conn.execute("UPDATE media_items SET seen_run=? WHERE id=?", (run, media_id))
        return 0
    conn.execute("INSERT OR IGNORE INTO canonical_media VALUES(?)", (item.canonical_id,))
    conn.execute(
        """INSERT INTO media_items VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?)
        ON CONFLICT(id) DO UPDATE SET section_id=excluded.section_id,
        canonical_id=excluded.canonical_id,media_type=excluded.media_type,title=excluded.title,
        normalized_title=excluded.normalized_title,year=excluded.year,runtime=excluded.runtime,
        available=1,metadata=excluded.metadata,seen_run=excluded.seen_run,
        fingerprint=excluded.fingerprint""",
        (
            media_id,
            item.server_id,
            item.rating_key,
            item.section_id,
            item.canonical_id,
            item.media_type,
            item.title,
            normalize_title(item.title),
            item.year,
            item.runtime_minutes,
            json.dumps(payload, allow_nan=False),
            run,
            fingerprint,
        ),
    )
    conn.execute(
        """INSERT INTO user_media_state
        (media_id,watched,view_count,last_viewed_at,personal_rating,resume_ms) VALUES(?,?,?,?,?,?)
        ON CONFLICT(media_id) DO UPDATE SET watched=excluded.watched,view_count=excluded.view_count,
        last_viewed_at=excluded.last_viewed_at,personal_rating=excluded.personal_rating,
        resume_ms=excluded.resume_ms""",
        (
            media_id,
            item.watched,
            item.view_count,
            item.last_viewed_at,
            item.personal_rating,
            item.resume_ms,
        ),
    )
    for table in ["media_guids", "media_genres", "media_people"]:
        conn.execute(f"DELETE FROM {table} WHERE media_id=?", (media_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO media_guids VALUES(?,?)", [(media_id, x) for x in item.external_ids]
    )
    conn.executemany(
        "INSERT OR IGNORE INTO media_genres VALUES(?,?)", [(media_id, x) for x in item.genres]
    )
    conn.executemany(
        "INSERT OR IGNORE INTO media_people VALUES(?,?,?)",
        [(media_id, role, name) for role, names in item.people.items() for name in names],
    )
    return int(previous is None or previous[0] != fingerprint or not previous[1])


def synchronize(
    db: Database, config: Settings, source: Source, *, full: bool = False
) -> dict[str, Any]:
    """One SQLite writer transaction serializes syncs; readers retain their old WAL snapshot.

    A failed section rolls back its savepoint including removals. A failed history page
    rolls back history and its watermark. Feedback is never part of sync writes.
    """
    bind_configuration(db, config)
    run, start = str(uuid.uuid4()), utc_now()
    changes = events = 0
    errors: list[str] = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO sync_runs(id,started_at,status) VALUES(?,?,'running')", (run, start)
        )
        try:
            capabilities = source.connect()
            prior_server = db.state("server_id")
            if prior_server and prior_server != source.server_id:
                raise ValueError("Server identity changed")
            Database.set_state(conn, "server_id", source.server_id)
            Database.set_state(conn, "capabilities", capabilities)
            Database.set_state(conn, "history_account_id", source.history_account_id)
        except PlexCompatibilityError:
            errors.append("connection_plex_api_incompatible_previous_cache_preserved")
        except Exception:
            errors.append("connection_or_identity_verification_failed")
        if not errors:
            for section in config.library_sections:
                conn.execute("SAVEPOINT section")
                section_changes = 0
                try:
                    for item in source.media(section):
                        if item.server_id != source.server_id or item.section_id != section:
                            raise ValueError("Unexpected media scope")
                        section_changes += save_media(conn, item, run)
                    removed = conn.execute(
                        "UPDATE media_items SET available=0 "
                        "WHERE server_id=? AND section_id=? AND seen_run<>? "
                        "AND available=1",
                        (source.server_id, section, run),
                    )
                    section_changes += removed.rowcount
                    conn.execute("RELEASE section")
                    changes += section_changes
                except Exception as exc:
                    conn.execute("ROLLBACK TO section")
                    conn.execute("RELEASE section")
                    reason = (
                        "plex_api_incompatible"
                        if isinstance(exc, PlexCompatibilityError)
                        else "failed"
                    )
                    errors.append(f"section_{section}_{reason}_previous_cache_preserved")
            conn.execute("SAVEPOINT history")
            try:
                if source.history_account_id is None:
                    raise ValueError("History identity unverified")
                since = None if full else db.state("history_watermark")
                for event in source.history(since):
                    if (
                        event.account_id != source.history_account_id
                        or event.server_id != source.server_id
                    ):
                        raise ValueError("History identity mismatch")
                    if (
                        event.section_id is not None
                        and event.section_id not in config.library_sections
                    ):
                        raise ValueError("History section mismatch")
                    payload = event.model_dump()
                    events += conn.execute(
                        "INSERT OR IGNORE INTO viewing_events "
                        "(server_id,event_id,account_id,rating_key,viewed_at,media_type,title,"
                        "section_id,parent_key,grandparent_key,parent_title,grandparent_title,"
                        "season_number,episode_number) VALUES "
                        "(:server_id,:event_id,:account_id,:rating_key,:viewed_at,:media_type,:title,"
                        ":section_id,:parent_key,:grandparent_key,:parent_title,:grandparent_title,"
                        ":season_number,:episode_number)",
                        payload,
                    ).rowcount
                    # Enrich existing stable events on a full rescan without counting them twice.
                    conn.execute(
                        "UPDATE viewing_events SET "
                        "section_id=COALESCE(:section_id,section_id),"
                        "parent_key=COALESCE(:parent_key,parent_key),"
                        "grandparent_key=COALESCE(:grandparent_key,grandparent_key),"
                        "parent_title=COALESCE(:parent_title,parent_title),"
                        "grandparent_title=COALESCE(:grandparent_title,grandparent_title),"
                        "season_number=COALESCE(:season_number,season_number),"
                        "episode_number=COALESCE(:episode_number,episode_number) "
                        "WHERE server_id=:server_id AND event_id=:event_id "
                        "AND account_id=:account_id",
                        payload,
                    )
                Database.resolve_history_metadata(conn)
                Database.set_state(conn, "history_watermark", start)
                capabilities["history_access"] = "verified"
                Database.set_state(conn, "capabilities", capabilities)
                conn.execute("RELEASE history")
            except Exception as exc:
                conn.execute("ROLLBACK TO history")
                conn.execute("RELEASE history")
                events = 0
                reason = (
                    "plex_api_incompatible" if isinstance(exc, PlexCompatibilityError) else "failed"
                )
                errors.append(f"history_{reason}_previous_cache_preserved")
        status = (
            "success"
            if not errors
            else ("failed" if errors[0].startswith("connection") else "partial")
        )
        finish = utc_now()
        conn.execute(
            "UPDATE sync_runs SET finished_at=?,status=?,changes=?,new_events=?,error=? WHERE id=?",
            (finish, status, changes, events, "; ".join(errors) or None, run),
        )
        if status == "success":
            Database.set_state(conn, "last_success", finish)
    return {
        "run_id": run,
        "status": status,
        "changes": changes,
        "new_events": events,
        "errors": errors,
        "compatibility_action": PLEX_COMPATIBILITY_HELP
        if any("plex_api_incompatible" in error for error in errors)
        else None,
        "freshness": db.status(config.sync_interval_seconds),
    }
