from scripts.verify_live_cache import compare_cached_history


def test_live_verification_allows_retained_history(populated):
    db, source = populated
    assert compare_cached_history(db, source) == (1, 1)
    source.events = []  # Plex may prune events already retained in the cache.
    assert compare_cached_history(db, source) == (0, 0)
    assert db.status(14400)["viewing_events"] == 1


def test_live_verification_detects_missing_or_changed_events(populated):
    db, source = populated
    source.events[0].viewed_at = "2026-09-16T23:00:00+00:00"
    assert compare_cached_history(db, source) == (1, 0)
    source.events[0].event_id = "uncached-event"
    assert compare_cached_history(db, source) == (1, 0)


def test_live_verification_does_not_accept_an_unselected_cached_event(populated):
    db, source = populated
    with db.connect() as conn:
        conn.execute("UPDATE viewing_events SET section_id=999")
    assert compare_cached_history(db, source) == (1, 0)
