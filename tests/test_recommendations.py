from plex_advisor.models import Feedback, Search
from plex_advisor.preferences import Preferences
from plex_advisor.recommendations import CandidateQuery, candidates, taste_profile


def test_under_two_hours_available_unwatched_deduplicated(populated):
    result = candidates(
        populated[0], CandidateQuery(filters=Search(genre="Action", max_runtime=120, watched=False))
    )
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["title"] == "Ronin"
    assert result["candidates"][0]["match_evidence"]


def test_feedback_included_and_no_inferred_rating(populated):
    db = populated[0]
    Preferences(db).record(
        Feedback(
            media_id="server-A:1",
            comments="Confusing exposition",
            sentiment="mixed",
            authorized=True,
        )
    )
    result = candidates(db, CandidateQuery(filters=Search(watched=True)))
    edge = next(x for x in result["candidates"] if x["title"] == "Edge of Tomorrow")
    assert edge["qualitative_feedback"][0]["rating"] is None
    assert taste_profile(db)["sample_sizes"]["rated"] == 2


def test_watched_duplicate_excluded(populated, config):
    from plex_advisor.synchronization import synchronize

    db, source = populated
    source.items[-2].watched = True
    synchronize(db, config, source)
    assert not candidates(db, CandidateQuery(filters=Search(query="Ronin", watched=False)))[
        "candidates"
    ]
