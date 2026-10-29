"""Regressions for the independent review. Synthetic data and temporary DBs only."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring

import pytest
from conftest import FakeSource
from starlette.testclient import TestClient

from plex_advisor.catalog import Catalog
from plex_advisor.database import MIGRATIONS, Database
from plex_advisor.history import watch_history
from plex_advisor.mcp_server import create_app
from plex_advisor.models import Feedback, HistoryQuery, Media, Search, ViewingEvent
from plex_advisor.plex_client import PlexSource, parse_media
from plex_advisor.preferences import Preferences
from plex_advisor.recommendations import CandidateQuery, candidates, taste_profile
from plex_advisor.synchronization import save_media, synchronize


def test_unselected_and_unknown_history_excluded_from_every_summary(config):
    config = config.model_copy(update={"library_sections": [1]})
    source = FakeSource()
    source.events.append(
        source.events[0].model_copy(update={"event_id": "known", "rating_key": "1"})
    )
    db = Database(config.database_path)
    result = synchronize(db, config, source)
    assert result["status"] == "success"
    assert Catalog(db).search(Search(query="Ronin"))["total_copies"] == 0
    history = watch_history(db, HistoryQuery(), "UTC")
    assert [e["event_id"] for e in history["events"]] == ["known"]
    assert history["total"] == 1 and history["next_offset"] is None
    assert Catalog(db).statistics()["recent_activity"][0]["events"] == 1
    assert Catalog(db).details("server-A:1")["history"]["retrievable_events"] == 1
    status = db.status(config.sync_interval_seconds)
    assert status["viewing_events"] == 1
    assert status["excluded_history_events"] == 1
    assert status["warning"] and "excluded" in status["warning"]
    # Preserve the unmatched record, but never count it as selected-library history.
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM viewing_events").fetchone()[0] == 2


def test_explicit_wrong_section_rolls_back_entire_history(config, populated):
    db, source = populated
    previous = db.state("history_watermark")
    source.events.extend(
        [
            source.events[0].model_copy(update={"event_id": "new-valid", "section_id": 1}),
            source.events[0].model_copy(update={"event_id": "wrong-section", "section_id": 99}),
        ]
    )
    assert synchronize(db, config, source)["status"] == "partial"
    assert watch_history(db, HistoryQuery(), "UTC")["total"] == 1
    assert db.state("history_watermark") == previous


def history_source(config, xml):
    calls = []

    def query(path, **kwargs):
        calls.append(kwargs)
        if kwargs["params"]["librarySectionID"] == 2:
            return fromstring('<MediaContainer totalSize="0" offset="0"/>')
        return fromstring(xml)

    source = PlexSource(config)
    source.server_id = "server-A"
    source.history_account_id = 7
    source.owner = SimpleNamespace(query=query)
    return source, calls


EPISODE_EVENT = """<MediaContainer totalSize="1" offset="0"><Video accountID="7"
    historyKey="/status/sessions/history/1" ratingKey="ep1" librarySectionID="1"
    type="episode" title="Pilot" grandparentTitle="Fargo" parentTitle="Season 1"
    grandparentRatingKey="show1" parentRatingKey="season1" parentIndex="1" index="1"
    viewedAt="1750000000" /></MediaContainer>"""


def test_history_adapter_scopes_requests_and_preserves_episode_identity(config):
    source, calls = history_source(config, EPISODE_EVENT)
    event = list(source.history(None))[0]
    assert [c["params"]["librarySectionID"] for c in calls] == [1, 2]
    assert all(c["params"]["accountID"] == 7 for c in calls)
    assert (event.section_id, event.grandparent_title, event.parent_title) == (
        1,
        "Fargo",
        "Season 1",
    )
    assert (event.parent_key, event.grandparent_key) == ("season1", "show1")
    assert (event.season_number, event.episode_number) == (1, 1)


def test_history_adapter_rejects_wrong_section_and_does_not_guess_missing_scope(config):
    source, _ = history_source(
        config, EPISODE_EVENT.replace('librarySectionID="1"', 'librarySectionID="99"')
    )
    with pytest.raises(ValueError, match="section"):
        list(source.history(None))
    source, _ = history_source(config, EPISODE_EVENT.replace('librarySectionID="1"', ""))
    assert list(source.history(None))[0].section_id is None


def test_tv_catalog_and_history_search_use_show_identity_and_numbers(config):
    episode = parse_media(
        fromstring("""<Video ratingKey="ep1" type="episode" title="Pilot"
        guid="plex://episode/ep1" grandparentRatingKey="show1" parentRatingKey="season1"
        grandparentTitle="Fargo" parentTitle="Season 1" parentIndex="1" index="1"
        userRating="2" viewOffset="300000" />"""),
        "server-A",
        1,
    )
    season = parse_media(
        fromstring("""<Directory ratingKey="season1" type="season"
        title="Season 1" parentRatingKey="show1" parentTitle="Fargo" index="1" />"""),
        "server-A",
        1,
    )
    source = FakeSource()
    source.items = [episode, season]
    source.events = [
        ViewingEvent(
            event_id="episode-event",
            server_id="server-A",
            rating_key="ep1",
            account_id=7,
            viewed_at="2026-09-15T23:00:00+00:00",
            media_type="episode",
            title="Pilot",
        )
    ]
    db = Database(config.database_path)
    synchronize(db, config, source)
    catalog = Catalog(db)
    item = catalog.search(Search(media_type="episode", query="Fargo"))["items"][0]
    assert (item["parent_title"], item["grandparent_title"]) == ("Season 1", "Fargo")
    assert (item["season_number"], item["episode_number"]) == (1, 1)
    assert (
        catalog.search(
            Search(
                media_type="episode", show_id="server-A:show1", season_number=1, episode_number=1
            )
        )["total_copies"]
        == 1
    )
    assert (
        catalog.search(Search(media_type="season", show_id="server-A:show1", season_number=1))[
            "total_copies"
        ]
        == 1
    )
    assert (
        catalog.search(Search(media_type="episode", show_id="server-B:show1"))["total_copies"] == 0
    )
    assert catalog.search(Search(media_type="episode", episode_number=2))["total_copies"] == 0
    details = catalog.details("server-A:ep1")
    assert details["grandparent_key"] == "show1"
    assert details["episode_number"] == 1
    history = watch_history(
        db, HistoryQuery(show_id="server-A:show1", season_number=1, episode_number=1), "UTC"
    )
    assert history["total"] == 1
    assert history["events"][0]["grandparent_title"] == "Fargo"
    assert history["events"][0]["section_id"] == 1
    # When later removed, exact historical identity remains available.
    source.items = []
    source.events = []
    synchronize(db, config, source)
    assert watch_history(db, HistoryQuery(show_id="server-A:show1"), "UTC")["total"] == 1


def test_canonical_exclusions_cover_copies_and_removed_ids(populated, config):
    db, source = populated
    filters = Search(query="Ronin", watched=False)
    first = candidates(db, CandidateQuery(filters=filters))["candidates"][0]
    for excluded in [first["media_id"], first["canonical_id"]]:
        result = candidates(db, CandidateQuery(filters=filters, exclude_media_ids=[excluded]))
        assert result["candidates"] == []
        assert result["matching_titles"] == 0
        assert result["matching_titles_before_exclusions"] == 1
    source.items = [m for m in source.items if f"{m.server_id}:{m.rating_key}" != first["media_id"]]
    synchronize(db, config, source)
    assert (
        candidates(db, CandidateQuery(filters=filters, exclude_media_ids=[first["media_id"]]))[
            "candidates"
        ]
        == []
    )
    assert (
        len(
            candidates(db, CandidateQuery(filters=filters, exclude_media_ids=["nonexistent"]))[
                "candidates"
            ]
        )
        == 1
    )


def test_taste_profile_includes_all_explicit_tv_rating_types(config):
    source = FakeSource()
    source.events = []
    source.items = [
        parse_media(
            fromstring('<Video ratingKey="m1" type="movie" title="Fargo" userRating="9"/>'),
            "server-A",
            1,
        ),
        parse_media(
            fromstring('<Directory ratingKey="s1" type="show" title="Fargo" userRating="10"/>'),
            "server-A",
            2,
        ),
        parse_media(
            fromstring(
                '<Directory ratingKey="season1" type="season" title="Season 1" userRating="3"/>'
            ),
            "server-A",
            2,
        ),
        parse_media(
            fromstring('<Video ratingKey="ep1" type="episode" title="Pilot" userRating="2"/>'),
            "server-A",
            2,
        ),
    ]
    db = Database(config.database_path)
    synchronize(db, config, source)
    profile = taste_profile(db)
    assert profile["sample_sizes"]["rated"] == 4
    assert [r["personal_rating"] for r in profile["high_personal_ratings"]] == [10, 9]
    assert [r["media_type"] for r in profile["high_personal_ratings"]] == ["show", "movie"]
    assert [r["media_type"] for r in profile["low_personal_ratings"]] == ["episode", "season"]
    assert [r["personal_rating"] for r in profile["low_personal_ratings"]] == [2, 3]


@pytest.mark.parametrize(
    ("source", "provider", "metric"),
    [
        ("imdb://image.rating", "IMDb", "audience"),
        ("tmdb://image.rating", "TMDB", "audience"),
        ("thetvdb://image.rating", "TVDB", "audience"),
        ("rottentomatoes://image.rating.ripe", "Rotten Tomatoes", "critic"),
        ("rottentomatoes://image.rating.upright", "Rotten Tomatoes", "audience"),
        ("unknown://image.rating", "unknown", "unknown"),
    ],
)
def test_public_rating_classification_preserves_raw_slots_and_personal(source, provider, metric):
    item = parse_media(
        fromstring(f'''<Video ratingKey="1" type="movie" title="Fixture"
        rating="8.1" ratingImage="{source}" userRating="10" />'''),
        "server-A",
        1,
    )
    rating = item.public_ratings[0]
    assert (rating.plex_field, rating.value, rating.provider, rating.metric) == (
        "rating",
        8.1,
        provider,
        metric,
    )
    assert item.personal_rating == 10
    assert item.critic_rating == (8.1 if metric == "critic" else None)
    assert item.audience_rating == (8.1 if metric == "audience" else None)


def test_missing_public_provider_and_distinct_rating_resume_semantics():
    unknown = parse_media(
        fromstring('<Video ratingKey="1" type="movie" title="Fixture" rating="8.1"/>'),
        "server-A",
        1,
    )
    assert unknown.public_ratings[0].metric == "unknown"
    assert unknown.critic_rating is None and unknown.audience_rating is None
    assert unknown.personal_rating is None
    item = parse_media(
        fromstring("""<Video ratingKey="1" type="movie" title="Fargo"
        rating="8.1" ratingImage="rottentomatoes://image.rating.ripe"
        audienceRating="9.0" audienceRatingImage="rottentomatoes://image.rating.upright"
        userRating="10" viewOffset="1800000" />"""),
        "server-A",
        1,
    )
    assert (item.personal_rating, item.critic_rating, item.audience_rating) == (10, 8.1, 9)
    assert item.watched is False and item.resume_ms == 1800000


def test_mcp_data_and_freshness_share_snapshot(config, populated, monkeypatch):
    db, source = populated
    original_search = Catalog.search
    with db.connect() as conn:
        Database.set_state(conn, "last_success", "2000-01-01T00:00:00+00:00")

    def interleaved(self, filters):
        old_result = original_search(self, filters)
        source.items[0].personal_rating = 10
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(synchronize, db, config, source).result(timeout=15)
            assert result["status"] == "success"
        return old_result

    monkeypatch.setattr(Catalog, "search", interleaved)
    with TestClient(create_app(config, db), base_url="http://localhost:8765") as client:
        result = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer " + config.local_bearer_token.get_secret_value(),
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "search_library",
                    "arguments": {"filters": {"query": "Edge of Tomorrow"}},
                },
            },
        ).json()["result"]["structuredContent"]
    assert result["items"][0]["personal_rating"] == 7
    assert result["freshness"]["stale"] is True
    assert result["freshness"]["last_successful_sync"] == "2000-01-01T00:00:00+00:00"
    assert Catalog(db).details("server-A:1")["personal_rating"] == 10
    assert db.status(config.sync_interval_seconds)["stale"] is False


def test_read_snapshot_blocks_writes_and_releases_after_errors(populated):
    db, _ = populated
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with db.read_snapshot():
            with db.connect() as conn:
                conn.execute("DELETE FROM viewing_events")
    # A later explicitly authorized local feedback write still works normally.
    assert Preferences(db).record(
        Feedback(media_id="server-A:1", comments="Fixture", authorized=True)
    )["saved_locally"]


def test_v1_upgrade_preserves_feedback_personal_ratings_and_quarantines_unknown(config):
    # Build the historical schema directly, never opening the operator's live cache.
    with sqlite3.connect(config.database_path) as conn:
        conn.executescript(MIGRATIONS[0] + "PRAGMA user_version=1;")
        binding = {
            "plex_origin": config.plex_url.rstrip("/"),
            "account_id": config.plex_account_id,
            "username": config.plex_username,
            "server_id": config.plex_server_id,
            "history_account_id": config.plex_history_account_id,
            "history_account_name": config.plex_history_account_name,
            "history_binding_confirmed": True,
            "sections": config.library_sections,
        }
        conn.execute("INSERT INTO identity VALUES(1,?)", (json.dumps(binding, sort_keys=True),))
        item = Media(
            server_id="server-A",
            rating_key="1",
            canonical_id="canon-fargo",
            section_id=1,
            media_type="movie",
            title="Fargo",
            personal_rating=10,
            critic_rating=8.1,
            critic_source="imdb://image.rating",
            watched=True,
        )
        save_media(conn, item, "legacy")
        legacy = item.model_dump()
        legacy.pop("public_ratings")
        conn.execute("UPDATE media_items SET metadata=?", (json.dumps(legacy),))
        for event_id, rating_key, account in [
            ("known", "1", 7),
            ("unknown", "removed", 7),
            ("other-user", "1", 9),
        ]:
            conn.execute(
                "INSERT INTO viewing_events VALUES(?,?,?,?,?,?,?)",
                (
                    "server-A",
                    event_id,
                    account,
                    rating_key,
                    "2026-09-01T00:00:00+00:00",
                    "movie",
                    "Fixture",
                ),
            )
        conn.execute(
            "INSERT INTO personal_feedback VALUES(1,'canon-fargo',NULL,'positive',"
            "'keep me','[]','[]','2026-09-01','2026-09-01','fixture')"
        )
    db = Database(config.database_path)
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert conn.execute("SELECT COUNT(*) FROM viewing_events").fetchone()[0] == 3
    details = Catalog(db).details("server-A:1")
    assert details["personal_rating"] == 10
    assert details["critic_rating"] is None and details["audience_rating"] == 8.1
    assert details["public_ratings"][0]["provider"] == "IMDb"
    assert details["history"]["retrievable_events"] == 1
    assert watch_history(db, HistoryQuery(), "UTC")["total"] == 1
    assert db.status(14400)["excluded_history_events"] == 2
    assert Preferences(db).list()["feedback"][0]["comments"] == "keep me"
    # A full reimport can restore unknown historical scope without duplicating it.
    source = FakeSource()
    source.items = [
        parse_media(
            fromstring("""<Video ratingKey="1" type="movie" title="Fargo"
        guid="plex://movie/fargo" userRating="10" rating="8.1"
        ratingImage="imdb://image.rating" />"""),
            "server-A",
            1,
        ).model_copy(update={"canonical_id": "canon-fargo"})
    ]
    source.events = [
        ViewingEvent(
            event_id="unknown",
            server_id="server-A",
            rating_key="removed",
            account_id=7,
            viewed_at="2026-09-01T00:00:00+00:00",
            media_type="movie",
            title="Fixture",
            section_id=1,
        )
    ]
    assert synchronize(db, config, source, full=True)["new_events"] == 0
    assert watch_history(db, HistoryQuery(), "UTC")["total"] == 2
    assert Preferences(db).list()["feedback"][0]["comments"] == "keep me"
    assert Catalog(db).details("server-A:1")["personal_rating"] == 10


def test_scoped_history_pagination_ignores_unknown_rows(config):
    source = FakeSource()
    source.events = []
    for index in range(5):
        source.events.append(
            ViewingEvent(
                event_id=f"h-{index}",
                server_id="server-A",
                account_id=7,
                rating_key="1" if index % 2 == 0 else f"unknown-{index}",
                viewed_at=f"2026-09-15T23:00:0{index}+00:00",
                media_type="movie",
            )
        )
    db = Database(config.database_path)
    synchronize(db, config, source)
    event_ids, offset = [], 0
    while True:
        page = watch_history(db, HistoryQuery(limit=1, offset=offset), "UTC")
        assert page["total"] == 3
        event_ids.extend(e["event_id"] for e in page["events"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert event_ids == ["h-4", "h-2", "h-0"]


def test_migration_failure_rolls_back_schema_and_metadata(config, monkeypatch):
    import plex_advisor.database as database_module

    with sqlite3.connect(config.database_path) as conn:
        conn.executescript(MIGRATIONS[0] + "PRAGMA user_version=1;")
        binding = {
            "plex_origin": config.plex_url.rstrip("/"),
            "account_id": config.plex_account_id,
            "username": config.plex_username,
            "server_id": config.plex_server_id,
            "history_account_id": config.plex_history_account_id,
            "history_account_name": config.plex_history_account_name,
            "history_binding_confirmed": True,
            "sections": config.library_sections,
        }
        conn.execute("INSERT INTO identity VALUES(1,?)", (json.dumps(binding, sort_keys=True),))
        item = Media(
            server_id="server-A",
            rating_key="1",
            section_id=1,
            canonical_id="c1",
            media_type="movie",
            title="Fixture",
            personal_rating=10,
        )
        save_media(conn, item, "legacy")
        before = conn.execute("SELECT metadata FROM media_items").fetchone()[0]

    def fail(metadata):
        raise ValueError("Synthetic migration failure")

    monkeypatch.setattr(database_module, "upgrade_public_ratings", fail)
    with pytest.raises(ValueError, match="Synthetic"):
        Database(config.database_path)
    with sqlite3.connect(config.database_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "section_id" not in [r[1] for r in conn.execute("PRAGMA table_info(viewing_events)")]
        assert conn.execute("SELECT metadata FROM media_items").fetchone()[0] == before


def test_public_rating_sorts_and_zero_personal_rating(config):
    source = FakeSource()
    source.events = []
    source.items = [
        parse_media(
            fromstring("""<Video ratingKey="imdb" type="movie" title="IMDb fixture"
            rating="9.5" ratingImage="imdb://image.rating" userRating="0"/>"""),
            "server-A",
            1,
        ),
        parse_media(
            fromstring("""<Video ratingKey="critic" type="movie" title="Critic fixture"
            rating="7" ratingImage="rottentomatoes://image.rating.ripe"/>"""),
            "server-A",
            1,
        ),
    ]
    db = Database(config.database_path)
    synchronize(db, config, source)
    catalog = Catalog(db)
    assert catalog.search(Search(sort="critic_rating"))["items"][0]["media_id"] == "server-A:critic"
    assert catalog.search(Search(sort="audience_rating"))["items"][0]["media_id"] == "server-A:imdb"
    assert catalog.details("server-A:imdb")["personal_rating"] == 0
