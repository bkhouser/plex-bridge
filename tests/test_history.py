import pytest
from pydantic import ValidationError

from plex_advisor.history import watch_history
from plex_advisor.models import HistoryQuery
from plex_advisor.synchronization import synchronize


def test_date_range_timezone(populated):
    result = watch_history(
        populated[0],
        HistoryQuery(start="2026-09-01T00:00:00Z", end="2026-10-01T00:00:00Z", media_type="movie"),
        "UTC",
    )
    assert result["total"] == 1
    assert result["events"][0]["viewed_at_local"] == "2026-09-15T23:00:00+00:00"
    assert (
        watch_history(populated[0], HistoryQuery(end="2026-09-15T23:00:00Z"), "UTC")["total"] == 0
    )


def test_caller_cannot_override_identity():
    with pytest.raises(ValidationError):
        HistoryQuery(account_id=999)
    with pytest.raises(ValidationError):
        HistoryQuery(start="2026-09-01")


def test_other_users_rejected_atomically(populated, config):
    db, source = populated
    source.events.append(source.events[0].model_copy(update={"event_id": "evil", "account_id": 9}))
    result = synchronize(db, config, source)
    assert result["status"] == "partial"
    assert watch_history(db, HistoryQuery(), "UTC")["total"] == 1


def test_repeat_sync_deduplicates(populated, config):
    db, source = populated
    result = synchronize(db, config, source)
    assert result["changes"] == 0
    assert result["new_events"] == 0
    assert result["freshness"]["viewing_events"] == 1


def test_history_failure_retains_watermark(populated, config):
    db, source = populated
    previous = db.state("history_watermark")
    source.fail_history = True
    assert synchronize(db, config, source)["status"] == "partial"
    assert db.state("history_watermark") == previous
