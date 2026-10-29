import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

from plex_advisor.errors import CacheCompatibilityError
from plex_advisor.models import utc_now
from plex_advisor.ratings import upgrade_public_ratings

# Versioned, additive schema. No Plex database access. All paths are operator configured.
MIGRATIONS = [
    """
CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), binding TEXT NOT NULL);
CREATE TABLE canonical_media (id TEXT PRIMARY KEY);
CREATE TABLE media_items (
 id TEXT PRIMARY KEY, server_id TEXT NOT NULL, rating_key TEXT NOT NULL,
 section_id INTEGER NOT NULL, canonical_id TEXT NOT NULL REFERENCES canonical_media(id),
 media_type TEXT NOT NULL, title TEXT NOT NULL, normalized_title TEXT NOT NULL,
 year INTEGER, runtime REAL, available INTEGER NOT NULL DEFAULT 1,
 metadata TEXT NOT NULL, seen_run TEXT NOT NULL, fingerprint TEXT NOT NULL,
 UNIQUE(server_id,rating_key));
CREATE INDEX media_filter ON media_items(available,media_type,year,runtime);
CREATE INDEX media_title ON media_items(normalized_title,year);
CREATE INDEX media_canonical ON media_items(canonical_id);
CREATE TABLE media_guids (media_id TEXT REFERENCES media_items(id), guid TEXT,
 PRIMARY KEY(media_id,guid));
CREATE INDEX guid_lookup ON media_guids(guid);
CREATE TABLE media_genres (media_id TEXT REFERENCES media_items(id), genre TEXT,
 PRIMARY KEY(media_id,genre));
CREATE INDEX genre_lookup ON media_genres(genre COLLATE NOCASE);
CREATE TABLE media_people (media_id TEXT REFERENCES media_items(id), role TEXT, name TEXT,
 PRIMARY KEY(media_id,role,name));
CREATE INDEX people_lookup ON media_people(role,name COLLATE NOCASE);
CREATE TABLE user_media_state (
 media_id TEXT PRIMARY KEY REFERENCES media_items(id), watched INTEGER, view_count INTEGER,
 last_viewed_at TEXT, personal_rating REAL, resume_ms INTEGER,
 provenance TEXT NOT NULL DEFAULT 'plex_user_session');
CREATE TABLE viewing_events (
 server_id TEXT NOT NULL, event_id TEXT NOT NULL, account_id INTEGER NOT NULL,
 rating_key TEXT, viewed_at TEXT NOT NULL, media_type TEXT NOT NULL, title TEXT,
 PRIMARY KEY(server_id,event_id,account_id));
CREATE INDEX event_date ON viewing_events(viewed_at DESC);
CREATE INDEX event_media ON viewing_events(server_id,rating_key);
CREATE TABLE personal_feedback (
 id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_id TEXT NOT NULL REFERENCES canonical_media(id),
 rating REAL, sentiment TEXT NOT NULL, comments TEXT NOT NULL, positive_attributes TEXT NOT NULL,
 negative_attributes TEXT NOT NULL, recorded_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 source TEXT NOT NULL);
CREATE INDEX feedback_media ON personal_feedback(canonical_id);
CREATE TABLE sync_runs (
 id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
 changes INTEGER NOT NULL DEFAULT 0, new_events INTEGER NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
""",
    """
ALTER TABLE viewing_events ADD COLUMN section_id INTEGER;
ALTER TABLE viewing_events ADD COLUMN parent_key TEXT;
ALTER TABLE viewing_events ADD COLUMN grandparent_key TEXT;
ALTER TABLE viewing_events ADD COLUMN parent_title TEXT;
ALTER TABLE viewing_events ADD COLUMN grandparent_title TEXT;
ALTER TABLE viewing_events ADD COLUMN season_number INTEGER;
ALTER TABLE viewing_events ADD COLUMN episode_number INTEGER;
CREATE INDEX event_section ON viewing_events(section_id,viewed_at DESC);
""",
    """
CREATE TABLE historical_library_mappings (
 id TEXT PRIMARY KEY, server_id TEXT NOT NULL, account_id INTEGER NOT NULL,
 source_section_id INTEGER NOT NULL, target_section_id INTEGER NOT NULL,
 media_type TEXT NOT NULL, first_viewed_at TEXT NOT NULL, last_viewed_at TEXT NOT NULL,
 confirmed_by TEXT NOT NULL, confirmation TEXT NOT NULL, confirmed_at TEXT NOT NULL,
 evidence_sha256 TEXT NOT NULL, revoked_at TEXT, revocation_reason TEXT);
CREATE TABLE historical_event_evidence (
 server_id TEXT NOT NULL, event_id TEXT NOT NULL, account_id INTEGER NOT NULL,
 mapping_id TEXT NOT NULL REFERENCES historical_library_mappings(id),
 source_section_id INTEGER NOT NULL, viewed_at TEXT NOT NULL, media_type TEXT NOT NULL,
 rating_key TEXT, metadata TEXT NOT NULL, observed_at TEXT NOT NULL,
 PRIMARY KEY(server_id,event_id,account_id),
 FOREIGN KEY(server_id,event_id,account_id)
 REFERENCES viewing_events(server_id,event_id,account_id));
CREATE INDEX historical_mapping_events ON historical_event_evidence(mapping_id);
""",
]


def historical_scope(alias: str = "e") -> str:
    """Exact enrolled events only; never infer item identity or grant a whole library."""
    return (
        "EXISTS(SELECT 1 FROM historical_event_evidence h "
        "JOIN historical_library_mappings hm ON hm.id=h.mapping_id "
        "JOIN identity hi ON hi.singleton=1 "
        "JOIN json_each(hi.binding,'$.sections') hs "
        "ON hs.value=hm.target_section_id "
        f"WHERE h.server_id={alias}.server_id AND h.event_id={alias}.event_id "
        f"AND h.account_id={alias}.account_id AND h.viewed_at={alias}.viewed_at "
        f"AND h.media_type={alias}.media_type AND h.rating_key IS {alias}.rating_key "
        f"AND ({alias}.section_id IS NULL OR {alias}.section_id=h.source_section_id) "
        "AND hm.server_id=h.server_id AND hm.account_id=h.account_id "
        "AND hm.source_section_id=h.source_section_id AND hm.media_type=h.media_type "
        "AND h.viewed_at BETWEEN hm.first_viewed_at AND hm.last_viewed_at "
        "AND hm.revoked_at IS NULL "
        "AND json_extract(hi.binding,'$.server_id')=h.server_id "
        "AND json_extract(hi.binding,'$.history_account_id')=h.account_id "
        "AND json_extract(hi.binding,'$.history_binding_confirmed')=1)"
    )


def history_scope(alias: str = "e") -> str:
    """Internal SQL predicate; callers supply only hard-coded table aliases."""
    direct = (
        "EXISTS(SELECT 1 FROM identity i,json_each(i.binding,'$.sections') selected "
        f"WHERE i.singleton=1 AND selected.value={alias}.section_id "
        f"AND json_extract(i.binding,'$.server_id')={alias}.server_id "
        f"AND json_extract(i.binding,'$.history_account_id')={alias}.account_id "
        "AND json_extract(i.binding,'$.history_binding_confirmed')=1)"
    )
    return f"({direct} OR {historical_scope(alias)})"


def _schema_shape(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {
        row[0]: (
            [tuple(x) for x in conn.execute("SELECT * FROM pragma_table_info(?)", (row[0],))],
            [tuple(x) for x in conn.execute("SELECT * FROM pragma_foreign_key_list(?)", (row[0],))],
            [
                (
                    index[0],
                    [
                        tuple(x)
                        for x in conn.execute("SELECT * FROM pragma_index_info(?)", (index[0],))
                    ],
                )
                for index in conn.execute(
                    'SELECT name FROM pragma_index_list(?) WHERE "unique"=1 ORDER BY name',
                    (row[0],),
                )
            ],
        )
        for row in tables
    }


@lru_cache(maxsize=4)
def _expected_schema(version: int) -> dict[str, Any]:
    conn = sqlite3.connect(":memory:")
    try:
        for migration in MIGRATIONS[:version]:
            conn.executescript(migration)
        return _schema_shape(conn)
    finally:
        conn.close()


def _validate_cache(conn: sqlite3.Connection, version: int) -> None:
    if version < 0 or version > len(MIGRATIONS):
        raise CacheCompatibilityError("Database is newer than this application or unsupported")
    if _schema_shape(conn) != _expected_schema(version):
        raise CacheCompatibilityError("Cache structure does not match its declared version")
    if conn.execute("SELECT json_valid('{}'), json_extract('{\"v\":1}', '$.v')").fetchone()[:] != (
        1,
        1,
    ):
        raise CacheCompatibilityError("Required SQLite JSON functionality is unavailable")
    if version:
        row = conn.execute("SELECT binding FROM identity WHERE singleton=1").fetchone()
        if row:
            try:
                binding = json.loads(row[0])
            except (ValueError, TypeError) as exc:
                raise CacheCompatibilityError("Invalid cache identity binding") from exc
            if not isinstance(binding, dict) or set(binding) != {
                "plex_origin",
                "account_id",
                "username",
                "server_id",
                "history_account_id",
                "history_account_name",
                "history_binding_confirmed",
                "sections",
            }:
                raise CacheCompatibilityError("Invalid cache identity binding")
        else:
            for table in _expected_schema(version):
                if (
                    table != "identity"
                    and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                ):
                    raise CacheCompatibilityError("Nonempty cache has lost its identity binding")
    if conn.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
        raise CacheCompatibilityError("Cache integrity check failed")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._snapshot: ContextVar[sqlite3.Connection | None] = ContextVar(
            "plex_advisor_read_snapshot", default=None
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            # Validate before migrations or WAL mode can modify an existing file.
            _validate_cache(conn, version)
            conn.execute("PRAGMA journal_mode=WAL")
            for index in range(version, len(MIGRATIONS)):
                conn.executescript("BEGIN IMMEDIATE;\n" + MIGRATIONS[index])
                if index == 1:
                    self.resolve_history_metadata(conn)
                    rows = conn.execute("SELECT id,metadata FROM media_items")
                    for row in rows:
                        metadata = upgrade_public_ratings(json.loads(row["metadata"]))
                        conn.execute(
                            "UPDATE media_items SET metadata=? WHERE id=?",
                            (json.dumps(metadata), row["id"]),
                        )
                conn.execute(f"PRAGMA user_version={index + 1}")
                conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        snapshot = self._snapshot.get()
        if snapshot is not None:
            yield snapshot
            return
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def read_snapshot(self) -> Iterator[sqlite3.Connection]:
        """Pin data and freshness to one read-only WAL snapshot for this request."""
        existing = self._snapshot.get()
        if existing is not None:
            yield existing
            return
        with self.connect() as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()
            token = self._snapshot.set(conn)
            try:
                yield conn
            finally:
                self._snapshot.reset(token)

    @staticmethod
    def resolve_history_metadata(conn: sqlite3.Connection) -> None:
        """Resolve missing legacy fields only through exact cached media identity.

        Unmatched records stay NULL and are excluded by history_scope. Retained
        unavailable catalog entries can still establish historical library scope.
        """
        fields = [
            "section_id",
            "parent_key",
            "grandparent_key",
            "parent_title",
            "grandparent_title",
            "season_number",
            "episode_number",
        ]
        assignments = []
        for field in fields:
            value = (
                "m.section_id"
                if field == "section_id"
                else (f"json_extract(m.metadata,'$.{field}')")
            )
            assignments.append(
                f"{field}=COALESCE(e.{field},(SELECT {value} FROM media_items m "
                "WHERE m.server_id=e.server_id AND m.rating_key=e.rating_key "
                "AND m.media_type=e.media_type))"
            )
        conn.execute(
            "UPDATE viewing_events AS e SET "
            + ",".join(assignments)
            + " WHERE e.section_id IS NULL OR (e.media_type IN ('season','episode') AND ("
            + " OR ".join(f"e.{field} IS NULL" for field in fields[1:])
            + "))"
        )

    def bind(self, binding: dict[str, Any]) -> None:
        """Prevent identity/section reconfiguration from disclosing the old cache."""
        encoded = json.dumps(binding, sort_keys=True)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT binding FROM identity WHERE singleton=1").fetchone()
            if not row:
                # Never assign an orphaned personal cache to a new principal.
                for table in _expected_schema(len(MIGRATIONS)):
                    if (
                        table != "identity"
                        and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                    ):
                        raise CacheCompatibilityError(
                            "Nonempty cache has lost its identity binding"
                        )
            if row and row[0] != encoded:
                raise ValueError("Cache belongs to a different configuration; use a separate DB")
            conn.execute("INSERT OR IGNORE INTO identity VALUES(1,?)", (encoded,))

    def state(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO sync_state VALUES(?,?) ON CONFLICT(key) "
            "DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def status(self, interval: int) -> dict[str, Any]:
        from datetime import UTC, datetime

        with self.read_snapshot() as conn:
            last = conn.execute("SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1")
            row = last.fetchone()
            counts = {
                "available_copies": conn.execute(
                    "SELECT COUNT(*) FROM media_items WHERE available=1"
                ).fetchone()[0],
                "viewing_events": conn.execute(
                    f"SELECT COUNT(*) FROM viewing_events e WHERE {history_scope()}"
                ).fetchone()[0],
                "excluded_history_events": conn.execute(
                    f"SELECT COUNT(*) FROM viewing_events e WHERE NOT ({history_scope()})"
                ).fetchone()[0],
            }
            success = self.state("last_success")
        stale = (
            not success
            or (datetime.now(UTC) - datetime.fromisoformat(success)).total_seconds() > interval
            or bool(row and row["status"] != "success")
        )
        return {
            **counts,
            "last_successful_sync": success,
            "last_run": dict(row) if row else None,
            "stale": stale,
            "checked_at": utc_now(),
            "warning": "Cached data may be stale or incomplete"
            if stale
            else (
                "Some cached history is excluded from the approved selected-library scope"
                if counts["excluded_history_events"]
                else None
            ),
            "history_completeness": (
                "Selected-library and owner-confirmed historical events; other events excluded. "
                "Plex history and incremental imports may be incomplete"
            ),
        }
