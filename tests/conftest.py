from copy import deepcopy

import pytest
from pydantic import SecretStr

from plex_advisor.config import Settings
from plex_advisor.database import Database
from plex_advisor.models import Media, ViewingEvent
from plex_advisor.synchronization import synchronize


class FakeSource:
    server_id = "server-A"
    history_account_id = 7  # Deliberately different from cloud account 123.

    def __init__(self):
        self.offline = False
        self.fail_section = None
        self.fail_history = False
        self.items = [
            Media(
                server_id=self.server_id,
                rating_key="1",
                section_id=1,
                canonical_id="canon-edge",
                media_type="movie",
                title="Edge of Tomorrow",
                year=2014,
                runtime_minutes=113,
                genres=["Action", "Science Fiction", "Thriller"],
                summary="A soldier relives the same battle.",
                watched=True,
                view_count=1,
                personal_rating=7,
                people={"actor": ["Tom Cruise"], "director": ["Doug Liman"]},
            ),
            Media(
                server_id=self.server_id,
                rating_key="2",
                section_id=1,
                canonical_id="canon-fugitive",
                media_type="movie",
                title="The Fugitive",
                year=1993,
                runtime_minutes=130,
                watched=True,
                view_count=4,
                personal_rating=9,
            ),
            Media(
                server_id=self.server_id,
                rating_key="3",
                section_id=2,
                canonical_id="canon-ronin",
                media_type="movie",
                title="Ronin",
                year=1998,
                runtime_minutes=119,
                genres=["Action", "Thriller"],
                watched=False,
                view_count=0,
            ),
            Media(
                server_id=self.server_id,
                rating_key="4",
                section_id=1,
                canonical_id="canon-thing-old",
                media_type="movie",
                title="The Thing",
                year=1982,
                watched=False,
                view_count=0,
                genres=["Science Fiction"],
            ),
            Media(
                server_id=self.server_id,
                rating_key="5",
                section_id=2,
                canonical_id="canon-thing-new",
                media_type="movie",
                title="The Thing",
                year=2011,
                watched=False,
                view_count=0,
                genres=["Science Fiction"],
            ),
            Media(
                server_id=self.server_id,
                rating_key="6",
                section_id=2,
                canonical_id="canon-ronin",
                media_type="movie",
                title="Ronin",
                year=1998,
                edition="Alternate",
                runtime_minutes=119,
                genres=["Action", "Thriller"],
                watched=False,
                view_count=0,
            ),
            Media(
                server_id=self.server_id,
                rating_key="7",
                section_id=1,
                canonical_id="canon-unknown",
                media_type="movie",
                title="Uncatalogued",
                watched=None,
            ),
        ]
        self.events = [
            ViewingEvent(
                event_id="h-1",
                server_id=self.server_id,
                account_id=7,
                rating_key="3",
                title="Ronin",
                media_type="movie",
                viewed_at="2026-09-15T23:00:00+00:00",
            )
        ]

    def connect(self):
        if self.offline:
            raise RuntimeError("X-Plex-Token=PRIVATE-DO-NOT-LEAK")
        return {"verified": True, "catalog": True, "history_identity_verified": True}

    def media(self, section_id):
        for item in self.items:
            if item.section_id == section_id:
                yield deepcopy(item)
                if self.fail_section == section_id:
                    raise RuntimeError("Partial response")

    def history(self, since):
        yield from deepcopy(self.events)
        if self.fail_history:
            raise RuntimeError("History truncated")


@pytest.fixture
def config(tmp_path):
    return Settings(
        _env_file=None,
        database_path=tmp_path / "test.sqlite",
        plex_token=SecretStr("PRIVATE-DO-NOT-LEAK"),
        plex_account_id=123,
        plex_username="owner",
        library_sections=[1, 2],
        plex_server_id="server-A",
        plex_history_account_id=7,
        plex_history_account_name="owner",
        history_binding_confirmed=True,
        local_bearer_token=SecretStr("test-owner-secret-" + "x" * 32),
    )


@pytest.fixture
def populated(config):
    db = Database(config.database_path)
    source = FakeSource()
    assert synchronize(db, config, source)["status"] == "success"
    return db, source
