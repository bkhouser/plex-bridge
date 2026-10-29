from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement, fromstring

import pytest

from plex_advisor.catalog import Catalog
from plex_advisor.database import Database
from plex_advisor.models import Search
from plex_advisor.plex_client import PlexSource, parse_media
from plex_advisor.synchronization import synchronize


class MockPMS:
    machineIdentifier = "server-A"
    friendlyName = "Fixture Server"

    def __init__(self, count=1207):
        self.count = count
        self.requests = []
        self.cloud_id = 123
        self.username = "owner"
        self.system = [SimpleNamespace(id=7, name="owner"), SimpleNamespace(id=9, name="other")]
        self.bad_page = False

    def myPlexAccount(self):
        return SimpleNamespace(id=self.cloud_id, username=self.username)

    def systemAccounts(self):
        return self.system

    def query(self, path, params=None, headers=None):
        params, headers = params or {}, headers or {}
        self.requests.append((path, params, headers))
        if path == "/library/sections":
            return fromstring(
                '<MediaContainer><Directory key="1" title="Movies" type="movie"/>'
                '<Directory key="2" title="More movies" type="movie"/></MediaContainer>'
            )
        start = int(headers.get("X-Plex-Container-Start", 0))
        size = int(headers.get("X-Plex-Container-Size", 200))
        history = path.startswith("/status")
        total = (
            (407 if params["librarySectionID"] == 1 else 0)
            if history
            else (self.count if "/1/" in path else 3)
        )
        root = Element("MediaContainer", totalSize=str(total), offset=str(start))
        for index in range(start, min(start + size, total)):
            if history:
                account_id = 9 if self.bad_page and start else 7
                SubElement(
                    root,
                    "Video",
                    accountID=str(account_id),
                    historyKey=f"/status/sessions/history/{index}",
                    ratingKey=str(index),
                    type="movie",
                    title=f"Film {index}",
                    viewedAt=str(1750000000 + index),
                    librarySectionID="1",
                )
            else:
                section = 1 if "/1/" in path else 2
                video = SubElement(
                    root,
                    "Video",
                    ratingKey=f"{section}{index:06}",
                    title=f"Film {section}-{index}",
                    type="movie",
                    year="2015",
                    duration="6900000",
                    guid=f"plex://movie/{section}-{index}",
                    viewCount="0",
                )
                SubElement(video, "Genre", tag="Science Fiction")
        return root


def source_for(config, monkeypatch, server):
    source = PlexSource(config)
    monkeypatch.setattr(source, "_server", lambda token: server)
    return source


def test_large_library_and_history_pagination(config, monkeypatch):
    server = MockPMS()
    source = source_for(config, monkeypatch, server)
    db = Database(config.database_path)
    result = synchronize(db, config, source)
    assert result["status"] == "success"
    assert result["freshness"]["available_copies"] == 1210
    assert result["new_events"] == 407
    assert Catalog(db).search(Search(limit=5, offset=1200))["total_titles"] == 1210
    calls = [x for x in server.requests if x[0] == "/library/sections/1/all"]
    assert [int(x[2]["X-Plex-Container-Start"]) for x in calls] == [
        0,
        200,
        400,
        600,
        800,
        1000,
        1200,
    ]
    assert all(x[1]["accountID"] == 7 for x in server.requests if x[0].startswith("/status"))
    assert {x[1]["librarySectionID"] for x in server.requests if x[0].startswith("/status")} == {
        1,
        2,
    }
    assert synchronize(db, config, source)["new_events"] == 0


def test_cross_user_page_rolls_back_history(config, monkeypatch):
    server = MockPMS(2)
    server.bad_page = True
    db = Database(config.database_path)
    result = synchronize(db, config, source_for(config, monkeypatch, server))
    assert result["status"] == "partial"
    assert result["freshness"]["viewing_events"] == 0
    assert db.state("history_watermark") is None


@pytest.mark.parametrize("mutation", ["wrong_name", "wrong_server", "missing_username"])
def test_identity_fail_closed(config, monkeypatch, mutation):
    server = MockPMS(2)
    if mutation == "wrong_name":
        server.username = "other"
    elif mutation == "wrong_server":
        server.machineIdentifier = "wrong-server"
    else:
        server.username = None
    source = source_for(config, monkeypatch, server)
    with pytest.raises(ValueError):
        source.connect()
    assert not server.requests


@pytest.mark.parametrize(
    "system",
    [
        [],
        [SimpleNamespace(id=7, name="other")],
        [SimpleNamespace(id=7, name="owner"), SimpleNamespace(id=9, name="owner")],
    ],
)
def test_history_mapping_unavailable_does_not_guess(config, monkeypatch, system):
    server = MockPMS(2)
    server.system = system
    source = source_for(config, monkeypatch, server)
    assert source.connect()["history_identity_verified"] is False
    with pytest.raises(ValueError):
        list(source.history(None))
    assert not any(path.startswith("/status") for path, _, _ in server.requests)


def test_missing_token_never_falls_back_to_plexapi_config(config):
    for token in ["", " "]:
        with pytest.raises(ValueError, match="token"):
            PlexSource(config)._server(token)


def test_realistic_xml_provenance_no_filesystem_leak():
    node = fromstring("""<Video ratingKey="5" type="movie" title="Edge of Tomorrow" year="2014"
      guid="plex://movie/abc" duration="6780000" viewCount="1" userRating="7.5"
      rating="9.1" ratingImage="rottentomatoes://image.rating.ripe" audienceRating="9"
      audienceRatingImage="rottentomatoes://image.rating.upright" lastViewedAt="1750000000">
      <Guid id="imdb://tt1631867"/><Genre tag="Action"/><Director tag="Doug Liman"/>
      <Role tag="Tom Cruise"/><Media id="42" videoResolution="1080">
      <Part file="C:/private/movie.mkv"/>
      </Media></Video>""")
    item = parse_media(node, "server-A", 1)
    assert item.personal_rating == 7.5
    assert item.critic_rating == 9.1
    assert item.last_viewed_at.endswith("+00:00")
    assert item.external_ids == ["imdb://tt1631867"]
    assert "private" not in item.model_dump_json()
    minimal = parse_media(fromstring('<Video ratingKey="6" type="movie" title="Minimal"/>'), "s", 1)
    assert minimal.personal_rating is None
    assert minimal.watched is False  # Plex omits zero viewCount.
    assert minimal.guid is None


def test_repeated_and_incomplete_pages_rejected(config, monkeypatch):
    source = PlexSource(config)
    root = fromstring('<MediaContainer totalSize="300"><Video ratingKey="1"/></MediaContainer>')
    with pytest.raises(ValueError, match="repeated"):
        list(source._pages(SimpleNamespace(query=lambda *a, **kw: root), "/test", {}))
    empty = fromstring('<MediaContainer totalSize="300"/>')
    with pytest.raises(ValueError, match="Incomplete"):
        list(source._pages(SimpleNamespace(query=lambda *a, **kw: empty), "/test", {}))


@pytest.mark.parametrize("bad_token", [None, "", " "])
def test_switch_token_missing_denies_before_new_session(config, monkeypatch, bad_token):
    server = MockPMS(2)
    shared = Element("MediaContainer")
    entry = SubElement(shared, "SharedServer", userID="123")
    if bad_token is not None:
        entry.set("accessToken", bad_token)
    account = SimpleNamespace(
        id=555,
        username="admin",
        users=lambda: [SimpleNamespace(id=123, username="owner")],
        FRIENDINVITE="/share/{machineId}",
        query=lambda path: shared,
    )
    server.myPlexAccount = lambda: account
    calls = []
    source = PlexSource(config)

    def construct(token):
        calls.append(token)
        return server

    monkeypatch.setattr(source, "_server", construct)
    with pytest.raises(ValueError, match="share token"):
        source.connect()
    assert len(calls) == 1


def test_expired_credentials_preserve_cache(config, populated, monkeypatch):
    source = PlexSource(config)

    def unauthorized(token):
        raise RuntimeError("401 Unauthorized X-Plex-Token=PRIVATE-DO-NOT-LEAK")

    monkeypatch.setattr(source, "_server", unauthorized)
    result = synchronize(populated[0], config, source)
    assert result["status"] == "failed"
    assert result["freshness"]["available_copies"] == 7
    assert "PRIVATE-DO-NOT-LEAK" not in str(result)
