from plex_advisor.catalog import Catalog
from plex_advisor.models import AvailabilityTitle, Search


def test_unwatched_scifi_since_2010(populated):
    result = Catalog(populated[0]).search(
        Search(genre="Science Fiction", year_min=2010, watched=False)
    )
    assert result["total_titles"] == 1
    assert result["items"][0]["title"] == "The Thing"


def test_manual_watched_has_no_invented_history(populated):
    item = Catalog(populated[0]).details("server-A:2")
    assert item["watched"] is True
    assert item["personal_rating"] == 9
    assert item["history"]["retrievable_events"] == 0
    assert item["history"]["last_event_at"] is None
    assert item["last_viewed_at"] is None


def test_history_does_not_override_current_unwatched(populated):
    item = Catalog(populated[0]).details("server-A:3")
    assert item["watched"] is False
    assert item["history"]["retrievable_events"] == 1
    assert item["personal_rating"] is None


def test_disambiguation_and_editions(populated):
    catalog = Catalog(populated[0])
    results = catalog.availability(
        [
            AvailabilityTitle(title="THE THING"),
            AvailabilityTitle(title="The Thing", year=1982),
            AvailabilityTitle(title="Ronin!"),
        ]
    )["results"]
    assert results[0]["status"] == "ambiguous"
    assert results[1]["status"] == "present"
    assert results[2]["status"] == "present"
    assert len(results[2]["matches"]) == 2


def test_filters_and_missing_metadata(populated):
    catalog = Catalog(populated[0])
    assert catalog.search(Search(actor="Tom Cruise"))["total_copies"] == 1
    assert catalog.search(Search(director="Doug Liman", personal_rating_min=7))["total_copies"] == 1
    assert catalog.search(Search(max_runtime=120, watched=False))["total_copies"] == 2
    assert catalog.search(Search(query="' OR 1=1 --"))["total_copies"] == 0
    assert "Uncatalogued" not in [
        r["title"] for r in catalog.search(Search(watched=False))["items"]
    ]
    assert catalog.search(Search(query="relives"))["items"][0]["title"] == "Edge of Tomorrow"


def test_statistics(populated):
    stats = Catalog(populated[0]).statistics()
    assert stats["movie_copies"] == 7
    assert stats["watched"] == 2
    assert stats["unwatched"] == 4
    assert stats["unknown"] == 1
    assert stats["recent_activity"][0]["events"] == 1
