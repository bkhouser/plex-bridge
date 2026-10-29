import json
import sqlite3
from typing import Any

from plex_advisor.database import Database, history_scope
from plex_advisor.models import AvailabilityTitle, Search, normalize_title


def search_clause(filters: Search) -> tuple[str, list[Any]]:
    clauses = ["m.available=1", "m.media_type=?"]
    args: list[Any] = [filters.media_type]
    for value, clause in [
        (
            filters.query,
            "(instr(lower(m.title),lower(?))>0 OR "
            "instr(lower(json_extract(m.metadata,'$.summary')),lower(?))>0 OR "
            "instr(lower(json_extract(m.metadata,'$.parent_title')),lower(?))>0 OR "
            "instr(lower(json_extract(m.metadata,'$.grandparent_title')),lower(?))>0)",
        ),
        (
            filters.genre,
            "EXISTS(SELECT 1 FROM media_genres g WHERE g.media_id=m.id "
            "AND g.genre=? COLLATE NOCASE)",
        ),
        (
            filters.actor,
            "EXISTS(SELECT 1 FROM media_people p WHERE p.media_id=m.id "
            "AND p.role='actor' AND p.name=? COLLATE NOCASE)",
        ),
        (
            filters.director,
            "EXISTS(SELECT 1 FROM media_people p WHERE p.media_id=m.id "
            "AND p.role='director' AND p.name=? COLLATE NOCASE)",
        ),
        (filters.year_min, "m.year>=?"),
        (filters.year_max, "m.year<=?"),
        (filters.max_runtime, "m.runtime<=?"),
        (filters.watched, "s.watched=?"),
        (filters.min_view_count, "s.view_count>=?"),
        (filters.personal_rating_min, "s.personal_rating>=?"),
        (filters.personal_rating_max, "s.personal_rating<=?"),
        (filters.section_id, "m.section_id=?"),
        (
            filters.show_id,
            "m.server_id || ':' || CASE WHEN m.media_type='season' "
            "THEN json_extract(m.metadata,'$.parent_key') "
            "ELSE json_extract(m.metadata,'$.grandparent_key') END=?",
        ),
        (filters.season_number, "json_extract(m.metadata,'$.season_number')=?"),
        (filters.episode_number, "json_extract(m.metadata,'$.episode_number')=?"),
    ]:
        if value is not None:
            clauses.append(clause)
            args.extend([value] * clause.count("?"))
    for genre in filters.genres_all:
        clauses.append(
            "EXISTS(SELECT 1 FROM media_genres g WHERE g.media_id=m.id "
            "AND g.genre=? COLLATE NOCASE)"
        )
        args.append(genre)
    # An unwatched edition must not hide a watched duplicate of the same verified title.
    if filters.watched is False:
        clauses.append(
            "NOT EXISTS(SELECT 1 FROM media_items other JOIN user_media_state os "
            "ON os.media_id=other.id WHERE other.canonical_id=m.canonical_id "
            "AND os.watched=1)"
        )
    return " AND ".join(clauses), args


def concise(row: sqlite3.Row) -> dict[str, Any]:
    data = json.loads(row["metadata"])
    return {
        "media_id": row["id"],
        "canonical_id": row["canonical_id"],
        "server_id": row["server_id"],
        "section_id": row["section_id"],
        "available": bool(row["available"]),
        "title": row["title"],
        "year": row["year"],
        "media_type": row["media_type"],
        **{
            key: data.get(key)
            for key in [
                "parent_key",
                "grandparent_key",
                "parent_title",
                "grandparent_title",
                "season_number",
                "episode_number",
            ]
        },
        "runtime_minutes": row["runtime"],
        "genres": data["genres"],
        "synopsis": (data["summary"] or "")[:600] or None,
        "people": data["people"],
        "edition": data["edition"],
        "watched": None if row["watched"] is None else bool(row["watched"]),
        "personal_rating": row["personal_rating"],
        "view_count": row["view_count"],
        "last_viewed_at": row["last_viewed_at"],
        "resume_ms": row["resume_ms"],
        "in_progress": bool(row["resume_ms"]) if row["resume_ms"] is not None else None,
        "critic_rating": data["critic_rating"],
        "critic_source": data["critic_source"],
        "audience_rating": data["audience_rating"],
        "audience_source": data["audience_source"],
        "public_ratings": data.get("public_ratings", []),
        "state_provenance": "plex_user_session",
        "missing_fields": [
            k for k in ["year", "runtime_minutes", "summary", "personal_rating"] if data[k] is None
        ],
    }


JOIN = "FROM media_items m LEFT JOIN user_media_state s ON m.id=s.media_id"


class Catalog:
    def __init__(self, db: Database):
        self.db = db

    def search(self, filters: Search) -> dict[str, Any]:
        where, args = search_clause(filters)
        sorts = {
            "title": "m.title COLLATE NOCASE",
            "year": "m.year DESC NULLS LAST",
            "personal_rating": "s.personal_rating DESC NULLS LAST",
            "critic_rating": "json_extract(m.metadata,'$.critic_rating') DESC NULLS LAST",
            "audience_rating": "json_extract(m.metadata,'$.audience_rating') DESC NULLS LAST",
            "view_count": "s.view_count DESC NULLS LAST",
            "recently_added": "json_extract(m.metadata,'$.added_at') DESC NULLS LAST",
        }
        with self.db.connect() as conn:
            count = conn.execute(
                f"SELECT COUNT(*),COUNT(DISTINCT m.canonical_id) {JOIN} WHERE {where}", args
            ).fetchone()
            rows = conn.execute(
                f"SELECT m.*,s.* {JOIN} WHERE {where} "
                f"ORDER BY {sorts[filters.sort]},m.id LIMIT ? OFFSET ?",
                [*args, filters.limit, filters.offset],
            ).fetchall()
        return {
            "items": [concise(r) for r in rows],
            "total_copies": count[0],
            "total_titles": count[1],
            "offset": filters.offset,
            "next_offset": filters.offset + len(rows)
            if filters.offset + len(rows) < count[0]
            else None,
            "watched_note": "Plex current state; unwatched does not mean guaranteed never seen",
        }

    def details(self, media_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(f"SELECT m.*,s.* {JOIN} WHERE m.id=?", (media_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown media identifier; search for an unambiguous match")
            events = conn.execute(
                "SELECT COUNT(*),MIN(viewed_at),MAX(viewed_at) FROM viewing_events e "
                f"WHERE server_id=? AND rating_key=? AND {history_scope()}",
                (row["server_id"], row["rating_key"]),
            ).fetchone()
            copies = conn.execute(
                "SELECT id,section_id,available FROM media_items WHERE canonical_id=?",
                (row["canonical_id"],),
            ).fetchall()
        return {
            **json.loads(row["metadata"]),
            **concise(row),
            "history": {
                "retrievable_events": events[0],
                "first_event_at": events[1],
                "last_event_at": events[2],
                "completeness": "not guaranteed",
            },
            "copies": [dict(r) for r in copies[:100]],
        }

    def availability(self, titles: list[AvailabilityTitle]) -> dict[str, Any]:
        if not 1 <= len(titles) <= 50:
            raise ValueError("Supply between 1 and 50 titles")
        results = []
        with self.db.connect() as conn:
            for title in titles:
                name = normalize_title(title.title)
                args: list[Any] = [name]
                where = "m.available=1 AND m.media_type='movie' AND m.normalized_title=?"
                if title.year is not None:
                    where += " AND m.year=?"
                    args.append(title.year)
                rows = conn.execute(
                    f"SELECT m.*,s.* {JOIN} WHERE {where} ORDER BY m.id LIMIT 51", args
                ).fetchall()
                identities = {r["canonical_id"] for r in rows}
                results.append(
                    {
                        "requested": title.model_dump(),
                        "status": "ambiguous"
                        if len(identities) > 1
                        else "present"
                        if rows
                        else "not_found_in_cached_library",
                        "matches": [concise(r) for r in rows[:50]],
                        "truncated": len(rows) > 50,
                    }
                )
        return {"results": results, "scope": "Selected personal Plex libraries; no public catalog"}

    def statistics(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            base = JOIN + " WHERE m.available=1 AND m.media_type='movie'"
            states = conn.execute(f"SELECT s.watched,COUNT(*) count {base} GROUP BY s.watched")
            watched = {str(r[0]): r[1] for r in states}
            years = conn.execute(
                f"SELECT m.year,COUNT(*) count {base} GROUP BY m.year ORDER BY m.year"
            )
            by_year = [dict(r) for r in years]
            decades = conn.execute(
                f"SELECT (m.year/10)*10 decade,COUNT(*) count {base} "
                "GROUP BY decade ORDER BY decade"
            )
            by_decade = [dict(r) for r in decades]
            genres = conn.execute(
                "SELECT g.genre,COUNT(*) count FROM media_genres g "
                "JOIN media_items m ON m.id=g.media_id WHERE m.available=1 "
                "AND m.media_type='movie' GROUP BY g.genre ORDER BY count DESC LIMIT 100"
            )
            by_genre = [dict(r) for r in genres]
            recent = conn.execute(
                "SELECT substr(viewed_at,1,7) month,COUNT(*) events,"
                "AVG(m.year) mean_release_year FROM viewing_events e "
                "LEFT JOIN media_items m ON e.server_id=m.server_id "
                f"AND e.rating_key=m.rating_key WHERE {history_scope()} GROUP BY month "
                "ORDER BY month DESC LIMIT 12"
            ).fetchall()
        return {
            "movie_copies": sum(watched.values()),
            "watched": watched.get("1", 0),
            "unwatched": watched.get("0", 0),
            "unknown": watched.get("None", 0),
            "by_year": by_year,
            "by_decade": by_decade,
            "by_genre": by_genre,
            "highest_personal_ratings": self.search(
                Search(personal_rating_min=0, sort="personal_rating", limit=10)
            )["items"],
            "most_watched": self.search(Search(watched=True, sort="view_count", limit=10))["items"],
            "recently_added": self.search(Search(sort="recently_added", limit=10))["items"],
            "recent_activity": [dict(r) for r in recent],
            "count_unit": "physical library entries; genre totals overlap",
            "unwatched_note": "Current Plex status, not guaranteed never seen",
        }
