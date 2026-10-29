import json
import sqlite3
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring

import pytest

from plex_advisor import cli
from plex_advisor.catalog import Catalog
from plex_advisor.database import MIGRATIONS, Database
from plex_advisor.errors import CacheCompatibilityError, PlexCompatibilityError
from plex_advisor.mcp_server import create_server
from plex_advisor.models import Search
from plex_advisor.plex_client import PlexSource
from plex_advisor.synchronization import bind_configuration, synchronize
from tests.test_plex_client import MockPMS, source_for


@pytest.mark.parametrize(
    "xml",
    [
        "<MediaContainer/>",
        '<MediaContainer totalSize="-1"/>',
        '<MediaContainer totalSize="zero"/>',
        '<MediaContainer totalSize="0"><Video ratingKey="1"/></MediaContainer>',
        '<MediaContainer totalSize="1" size="0"><Video ratingKey="1"/></MediaContainer>',
        '<MediaContainer totalSize="1" offset="2"><Video ratingKey="1"/></MediaContainer>',
        "<Error/>",
    ],
)
@pytest.mark.parametrize("target", ["catalog", "history"])
def test_api_drift_preserves_affected_cache(config, monkeypatch, xml, target):
    pms = MockPMS(2)
    source = source_for(config, monkeypatch, pms)
    db = Database(config.database_path)
    assert synchronize(db, config, source)["status"] == "success"
    previous_success = db.state("last_success")
    previous_watermark = db.state("history_watermark")
    query = pms.query

    def changed(path, **kwargs):
        if (target == "catalog" and path == "/library/sections/1/all") or (
            target == "history" and path.startswith("/status/")
        ):
            return fromstring(xml)
        return query(path, **kwargs)

    monkeypatch.setattr(pms, "query", changed)
    result = synchronize(db, config, source)
    assert result["status"] == "partial"
    assert "plex_api_incompatible" in str(result["errors"])
    assert "update Plex Bridge" in result["compatibility_action"]
    assert result["freshness"]["stale"]
    assert db.state("last_success") == previous_success
    assert Catalog(db).search(Search())["total_copies"] == 5
    assert db.status(14400)["viewing_events"] == 407
    if target == "history":
        assert db.state("history_watermark") == previous_watermark


@pytest.mark.parametrize("mutation", ["type", "title", "duration", "rating", "duplicate"])
def test_changed_media_contract_rolls_back(config, monkeypatch, mutation):
    pms = MockPMS(2)
    source = source_for(config, monkeypatch, pms)
    db = Database(config.database_path)
    synchronize(db, config, source)
    query = pms.query

    def changed(path, **kwargs):
        root = query(path, **kwargs)
        if path == "/library/sections/1/all":
            if mutation == "type":
                root[0].set("type", "episode")
            elif mutation == "title":
                del root[0].attrib["title"]
            elif mutation == "duplicate":
                root[1].set("ratingKey", root[0].get("ratingKey"))
            else:
                root[0].set(mutation, "NaN" if mutation == "rating" else "Infinity")
        return root

    monkeypatch.setattr(pms, "query", changed)
    result = synchronize(db, config, source)
    assert result["status"] == "partial"
    assert "plex_api_incompatible" in str(result["errors"])
    assert Catalog(db).search(Search())["total_copies"] == 5


def test_empty_library_requires_explicit_completion(config):
    source = PlexSource(config)
    for xml in ['<MediaContainer totalSize="0"/>', '<MediaContainer size="0"/>']:
        assert (
            list(
                source._pages(
                    SimpleNamespace(query=lambda *a, xml=xml, **kw: fromstring(xml)), "/", {}
                )
            )
            == []
        )


def test_missing_totals_and_nonconsecutive_duplicates_stop_pagination(config):
    source = PlexSource(config)
    for second in [
        '<MediaContainer><Video ratingKey="2"/></MediaContainer>',
        '<MediaContainer totalSize="3"><Video ratingKey="1"/></MediaContainer>',
    ]:
        pages = iter(
            [
                fromstring('<MediaContainer totalSize="3"><Video ratingKey="1"/></MediaContainer>'),
                fromstring(second),
            ]
        )
        with pytest.raises(PlexCompatibilityError):
            list(
                source._pages(
                    SimpleNamespace(query=lambda *a, pages=pages, **kw: next(pages)), "/", {}
                )
            )


@pytest.mark.parametrize("kind", ["duplicate_library", "wrong_library_root", "missing_account"])
def test_changed_enrollment_contract_fails_closed(config, monkeypatch, kind):
    pms = MockPMS(2)
    if kind == "missing_account":
        pms.myPlexAccount = lambda: SimpleNamespace(username="owner")
    else:
        xml = (
            '<MediaContainer><Directory key="1" title="A" type="movie"/>'
            '<Directory key="1" title="B" type="movie"/></MediaContainer>'
            if kind == "duplicate_library"
            else "<Error/>"
        )
        pms.query = lambda *a, **kw: fromstring(xml)
    with pytest.raises(PlexCompatibilityError):
        source_for(config, monkeypatch, pms).connect()


@pytest.mark.parametrize("version", [-1, len(MIGRATIONS) + 1])
def test_unsupported_database_version_is_not_modified(tmp_path, version):
    path = tmp_path / "unsupported.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
    before = path.read_bytes()
    with pytest.raises(CacheCompatibilityError):
        Database(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


@pytest.mark.parametrize("mutation", ["table", "column", "identity_json"])
def test_changed_cache_schema_or_identity_is_rejected(populated, mutation):
    db, _ = populated
    with db.connect() as conn:
        if mutation == "table":
            conn.execute("DROP TABLE sync_state")
        elif mutation == "column":
            conn.execute("ALTER TABLE media_items RENAME COLUMN title TO changed_title")
        else:
            conn.execute("UPDATE identity SET binding='not-json'")
    with sqlite3.connect(db.path) as conn:
        before = list(conn.iterdump())
    with pytest.raises(CacheCompatibilityError):
        Database(db.path)
    with sqlite3.connect(db.path) as conn:
        assert list(conn.iterdump()) == before


def test_orphaned_cache_cannot_be_reassigned(populated, config):
    db, _ = populated
    with db.connect() as conn:
        conn.execute("DELETE FROM identity")
    with pytest.raises(CacheCompatibilityError, match="identity"):
        bind_configuration(db, config.model_copy(update={"plex_account_id": 999}))
    with db.connect() as conn:
        assert not conn.execute("SELECT * FROM identity").fetchall()
        assert conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0] == 7


@pytest.mark.parametrize("mutation", ["uniqueness", "column_order"])
def test_cache_key_constraints_and_positional_columns_are_verified(tmp_path, mutation):
    schema = MIGRATIONS[0]
    if mutation == "uniqueness":
        schema = schema.replace("UNIQUE(server_id,rating_key)", "CHECK(1)")
    else:
        schema = schema.replace(
            "title TEXT NOT NULL, normalized_title TEXT NOT NULL",
            "normalized_title TEXT NOT NULL, title TEXT NOT NULL",
        )
    path = tmp_path / "changed.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(schema + "PRAGMA user_version=1;")
    before = path.read_bytes()
    with pytest.raises(CacheCompatibilityError):
        Database(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("version", [1, 2])
def test_orphaned_legacy_cache_is_rejected_before_migration(tmp_path, version):
    path = tmp_path / "orphan.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript("\n".join(MIGRATIONS[:version]))
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute("INSERT INTO canonical_media VALUES('retained-personal-identity')")
    before = path.read_bytes()
    with pytest.raises(CacheCompatibilityError, match="identity"):
        Database(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


@pytest.mark.parametrize("corrupt", [False, True])
def test_foreign_or_corrupt_database_is_not_rebuilt(tmp_path, corrupt):
    path = tmp_path / "foreign.sqlite"
    if corrupt:
        path.write_bytes(b"This is not a database")
    else:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE foreign_data(value TEXT)")
    before = path.read_bytes()
    with pytest.raises((CacheCompatibilityError, sqlite3.DatabaseError)):
        Database(path)
    assert path.read_bytes() == before


def test_cli_database_error_is_actionable_and_redacted(config, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Settings", lambda: config)
    monkeypatch.setattr("sys.argv", ["plex-advisor", "status"])

    def changed(*args):
        raise sqlite3.DatabaseError("PRIVATE-TOKEN-AND-PATH")

    monkeypatch.setattr(cli, "Database", changed)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 3
    output = capsys.readouterr().err
    assert "PRIVATE" not in output
    assert "backup" in json.loads(output)["action"]


async def test_live_mcp_cache_change_returns_safe_error(populated, config):
    db, _ = populated
    server = create_server(config, db)
    with db.connect() as conn:
        conn.execute("DROP TABLE sync_state")
    response = await server.call_tool("get_plex_status", {})
    serialized = str(response)
    assert "cache_unavailable_or_incompatible" in serialized
    assert "no such table" not in serialized
