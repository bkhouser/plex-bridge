import pytest

from plex_advisor.catalog import Catalog
from plex_advisor.models import Search
from plex_advisor.synchronization import bind_configuration, synchronize


def test_outage_safe_cache_no_secret_leaks(populated, config, caplog):
    db, source = populated
    source.offline = True
    result = synchronize(db, config, source)
    assert result["status"] == "failed"
    assert result["freshness"]["stale"]
    assert Catalog(db).search(Search())["total_copies"] == 7
    assert "PRIVATE-DO-NOT-LEAK" not in str(result) + caplog.text


def test_partial_section_no_false_removals(populated, config):
    db, source = populated
    source.fail_section = 1
    source.items[0].title = "Should roll back"
    assert synchronize(db, config, source)["status"] == "partial"
    assert Catalog(db).details("server-A:1")["title"] == "Edge of Tomorrow"
    assert Catalog(db).search(Search())["total_copies"] == 7


def test_database_identity_cannot_be_reused(populated, config):
    for update in [{"plex_account_id": 999}, {"library_sections": [1]}, {"plex_username": "other"}]:
        with pytest.raises(ValueError, match="different configuration"):
            bind_configuration(populated[0], config.model_copy(update=update))


def test_state_rating_changes_without_metadata_timestamp(populated, config):
    db, source = populated
    source.items[0].personal_rating = 8
    source.items[0].watched = False
    assert synchronize(db, config, source)["changes"] == 1
    details = Catalog(db).details("server-A:1")
    assert details["personal_rating"] == 8
    assert details["watched"] is False
