import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from plex_advisor.catalog import Catalog
from plex_advisor.models import Feedback
from plex_advisor.preferences import Preferences
from plex_advisor.synchronization import synchronize


def test_feedback_independent_survives_removal_and_correction(populated, config):
    db, source = populated
    prefs = Preferences(db)
    data = json.loads((Path(__file__).parent / "fixtures/edge_review.json").read_text())
    result = prefs.record(Feedback(**data))
    assert Catalog(db).details("server-A:1")["personal_rating"] == 7
    assert prefs.list()["feedback"][0]["rating"] is None
    source.items = [x for x in source.items if x.rating_key != "1"]
    synchronize(db, config, source)
    assert Catalog(db).details("server-A:1")["available"] is False
    assert prefs.list()["total"] == 1
    prefs.record(
        Feedback(**{**data, "feedback_id": result["feedback_id"], "comments": "Corrected"})
    )
    assert prefs.list()["feedback"][0]["comments"] == "Corrected"
    assert prefs.delete(result["feedback_id"], True)["deleted"]
    assert prefs.list()["total"] == 0


def test_feedback_requires_explicit_authorization():
    with pytest.raises(ValidationError):
        Feedback(media_id="a", comments="casual opinion", authorized=False)
    with pytest.raises(ValidationError):
        Feedback(media_id="a", comments="casual opinion")
