import copy
import json
from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement

import pytest

from plex_advisor import historical_recovery as recovery
from plex_advisor.catalog import Catalog
from plex_advisor.database import Database
from plex_advisor.history import watch_history
from plex_advisor.models import HistoryQuery


class HistoricalPMS:
    def __init__(self, approved):
        self.approved = approved
        self.calls = []
        self.alter = lambda root: None

    def query(self, path, params, headers):
        assert path == "/status/sessions/history/all"
        assert params["accountID"] == 7
        assert params["viewedAt<"] == 1577836800
        assert "type" not in params  # History's type parameter is not a media-type filter.
        old = params["librarySectionID"]
        start, size = int(headers["X-Plex-Container-Start"]), int(headers["X-Plex-Container-Size"])
        self.calls.append((old, start, size))
        _, kind, count = self.approved[old]
        root = Element("MediaContainer", totalSize=str(count), offset=str(start))
        for index in range(start, min(start + size, count)):
            a = dict(
                historyKey=f"/status/sessions/history/{old}-{index}",
                librarySectionID=str(old),
                accountID="7",
                type=kind,
                viewedAt=str(1451606400 + index),
            )
            if kind == "episode":
                a.update(
                    grandparentTitle="Ambiguous Show",
                    parentIndex="1",
                    index="1",
                    originallyAvailableAt="2015-01-01",
                )
            else:
                a["title"] = "The Thing"  # Two catalog remakes; never infer which one.
            SubElement(root, "Video", **a)
        self.alter(root)
        return root


@pytest.fixture
def cohort(populated, config):
    approved = {3: (1, "movie", 2), 4: (2, "episode", 3)}
    db, _ = populated
    with db.connect() as conn:
        for old, (_, kind, count) in approved.items():
            for index in range(count):
                conn.execute(
                    "INSERT INTO viewing_events(server_id,event_id,account_id,viewed_at,"
                    "media_type,title) VALUES(?,?,?,?,?,?)",
                    (
                        "server-A",
                        f"/status/sessions/history/{old}-{index}",
                        7,
                        recovery.timestamp(str(1451606400 + index)),
                        kind,
                        "The Thing" if kind == "movie" else None,
                    ),
                )
        for old, kind in [(9, "episode"), (6, "movie"), (7, "track"), (5, "movie"), (11, "track")]:
            conn.execute(
                "INSERT INTO viewing_events(server_id,event_id,account_id,viewed_at,"
                "media_type,title) VALUES(?,?,?,?,?,?)",
                ("server-A", f"excluded-{old}", 7, "2016-01-01T00:00:00+00:00", kind, "The Thing"),
            )
    pms = HistoricalPMS(approved)
    source = SimpleNamespace(
        server_id="server-A",
        history_account_id=7,
        sections={1: "movie", 2: "show", 5: "movie", 11: "artist"},
        owner=pms,
        connect=lambda: {"history_identity_verified": True},
    )
    return db, config, source, pms


def approval_for(cohort):
    return recovery.RecoveryApproval(
        mappings=tuple((old, *spec) for old, spec in cohort[3].approved.items()),
        before_epoch=1577836800,
        confirmed_by="Synthetic owner",
        confirmation="Explicitly approve these synthetic retired-library cohorts.",
    )


def plan_for(cohort):
    db, config, source, _ = cohort
    with db.read_snapshot() as conn:
        return recovery.prepare(conn, config, source, approval=approval_for(cohort))


def resign(plan):
    plan["sha256"] = recovery.digest({k: v for k, v in plan.items() if k != "sha256"})


def dump_existing(db):
    with db.read_snapshot() as conn:
        return {
            t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
            for t in [
                "viewing_events",
                "user_media_state",
                "personal_feedback",
                "identity",
                "media_items",
                "media_guids",
                "sync_runs",
                "sync_state",
            ]
        }


def test_recovery_pagination_provenance_missing_titles_and_idempotence(cohort):
    db, _, _, _ = cohort
    before = dump_existing(db)
    plan = plan_for(cohort)
    first = recovery.apply(db, plan, approval=approval_for(cohort))
    assert recovery.apply(db, plan, approval=approval_for(cohort)) == first
    assert dump_existing(db) == before
    assert db.status(14400)["viewing_events"] == 6
    assert db.status(14400)["excluded_history_events"] == 5
    events, offset = [], 0
    while True:
        page = watch_history(db, HistoryQuery(limit=2, offset=offset), "America/New_York")
        assert page["total"] == 6
        events.extend(page["events"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len({e["event_id"] for e in events}) == 6
    old = [e for e in events if e["mapping_id"]]
    assert len(old) == 5
    assert all(e["rating_key"] is None and e["grandparent_key"] is None for e in old)
    assert all(e["history_provenance"] == "owner_confirmed_historical_library" for e in old)
    episodes = [e for e in old if e["media_type"] == "episode"]
    assert all(e["title"] is None and e["grandparent_title"] == "Ambiguous Show" for e in episodes)
    assert all(e["section_id"] == 4 and e["selected_section_id"] == 2 for e in episodes)
    assert watch_history(db, HistoryQuery(season_number=1, episode_number=1), "UTC")["total"] == 3
    assert watch_history(db, HistoryQuery(media_id="server-A:4"), "UTC")["total"] == 0
    assert Catalog(db).details("server-A:4")["history"]["retrievable_events"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("accountID", "9"),
        ("librarySectionID", "9"),
        ("type", "track"),
        ("ratingKey", "4"),
        ("viewedAt", "1750000000"),
        ("title", "changed"),
        ("historyKey", "/status/sessions/history/unknown"),
    ],
)
def test_prepare_rejects_conflicting_event(cohort, field, value):
    db, _, _, pms = cohort
    before = dump_existing(db)
    pms.alter = lambda root: root[0].set(field, value)
    with pytest.raises(ValueError):
        plan_for(cohort)
    assert dump_existing(db) == before


@pytest.mark.parametrize(
    "fault", ["offset", "total", "empty", "duplicate", "reused", "server", "account"]
)
def test_prepare_rejects_pagination_and_identity_faults(cohort, fault):
    _, _, source, pms = cohort
    if fault == "offset":
        pms.alter = lambda root: root.set("offset", "100")
    elif fault == "total":
        pms.alter = lambda root: root.set("totalSize", "999")
    elif fault == "empty":
        pms.alter = lambda root: root.remove(root[0])
    elif fault == "duplicate":
        pms.alter = lambda root: root[1].set("historyKey", root[0].get("historyKey"))
    elif fault == "reused":
        source.sections[3] = "movie"
    elif fault == "server":
        source.server_id = "other-server"
    else:
        source.history_account_id = 9
    with pytest.raises(ValueError):
        plan_for(cohort)


def test_revoke_excludes_preserves_evidence_and_prevents_replay(cohort):
    db = cohort[0]
    plan = plan_for(cohort)
    result = recovery.apply(db, plan, approval=approval_for(cohort))
    recovery.revoke(db, result["mapping_ids"][0], "Owner withdrew confirmation")
    assert watch_history(db, HistoryQuery(), "UTC")["total"] == 4
    with pytest.raises(ValueError, match="Revoked"):
        recovery.apply(db, plan, approval=approval_for(cohort))
    changed = copy.deepcopy(plan)
    changed["observed_at"] = "2026-10-14T00:00:00+00:00"
    resign(changed)
    with pytest.raises(ValueError, match="Conflicting"):
        recovery.apply(db, changed, approval=approval_for(cohort))
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM historical_event_evidence").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM historical_library_mappings").fetchone()[0] == 2


def test_event_id_reuse_or_changes_do_not_inherit_scope(cohort):
    db = cohort[0]
    recovery.apply(db, plan_for(cohort), approval=approval_for(cohort))
    with db.connect() as conn:
        conn.execute(
            "UPDATE viewing_events SET viewed_at='2017-01-01T00:00:00+00:00' "
            "WHERE event_id='/status/sessions/history/3-0'"
        )
        conn.execute(
            "INSERT INTO viewing_events(server_id,event_id,account_id,viewed_at,media_type,"
            "section_id) VALUES('server-A','future-reused-library',7,"
            "'2026-01-01T00:00:00+00:00','movie',3)"
        )
    assert watch_history(db, HistoryQuery(), "UTC")["total"] == 5


@pytest.mark.parametrize("mutation", ["account", "server", "selected", "unconfirmed"])
def test_scope_remains_bound_to_owner_server_and_selection(cohort, mutation):
    db = cohort[0]
    recovery.apply(db, plan_for(cohort), approval=approval_for(cohort))
    with db.connect() as conn:
        binding = json.loads(conn.execute("SELECT binding FROM identity").fetchone()[0])
        if mutation == "account":
            binding["history_account_id"] = 9
        elif mutation == "server":
            binding["server_id"] = "other-server"
        elif mutation == "selected":
            binding["sections"] = []
        else:
            binding["history_binding_confirmed"] = False
        conn.execute("UPDATE identity SET binding=?", (json.dumps(binding),))
    assert watch_history(db, HistoryQuery(), "UTC")["total"] == 0


def test_apply_failure_rolls_back_all_provenance(cohort):
    db = cohort[0]
    plan = plan_for(cohort)
    plan["records"][-1]["before"]["title"] = "bad"
    resign(plan)
    with pytest.raises(ValueError):
        recovery.apply(db, plan, approval=approval_for(cohort))
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM historical_library_mappings").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM historical_event_evidence").fetchone()[0] == 0


def test_multiple_historical_pages_are_bounded(cohort):
    db, config, source, pms = cohort
    approved = {3: (1, "movie", 2), 4: (2, "episode", 1001)}
    pms.approved = approved
    with db.connect() as conn:
        for index in range(3, 1001):
            conn.execute(
                "INSERT INTO viewing_events(server_id,event_id,account_id,viewed_at,"
                "media_type) VALUES(?,?,?,?,?)",
                (
                    "server-A",
                    f"/status/sessions/history/4-{index}",
                    7,
                    recovery.timestamp(str(1451606400 + index)),
                    "episode",
                ),
            )
    plan = plan_for(cohort)
    assert pms.calls == [(3, 0, 2), (4, 0, 1000), (4, 1000, 1)]
    assert recovery.apply(db, plan, approval=approval_for(cohort))["enrolled_events"] == 1003
    # Reopening the migrated cache preserves enrollment.
    assert Database(config.database_path).status(14400)["viewing_events"] == 1004


def test_recovery_requires_separate_explicit_approval(cohort):
    db, config, source, _ = cohort
    with db.read_snapshot() as conn, pytest.raises(TypeError):
        recovery.prepare(conn, config, source)
    plan = plan_for(cohort)
    with pytest.raises(TypeError):
        recovery.apply(db, plan)
    from dataclasses import replace

    wrong = replace(approval_for(cohort), confirmed_by="Another operator")
    with pytest.raises(ValueError, match="confirmation"):
        recovery.apply(db, plan, approval=wrong)
    wrong = replace(approval_for(cohort), mappings=((3, 1, "movie", 1),))
    with pytest.raises(ValueError, match="exceeds"):
        recovery.apply(db, plan, approval=wrong)


@pytest.mark.parametrize("mappings", [[(3, 1, "movie", 1)], ([3, 1, "movie", 1],)])
def test_recovery_approval_rejects_mutable_mappings(mappings):
    with pytest.raises(ValueError, match="immutable"):
        recovery.RecoveryApproval(mappings, 1577836800, "Synthetic owner", "Confirmed")
