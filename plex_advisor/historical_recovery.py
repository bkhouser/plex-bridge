"""Operator-only, bounded historical recovery. No MCP tool or Plex write capability.

Preparation reads Plex and a read-only cache. Application requires a separately verified
backup and writes only provenance sidecars; existing events and user state stay intact.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from plex_advisor.config import Settings
from plex_advisor.database import Database
from plex_advisor.models import utc_now
from plex_advisor.plex_client import PlexSource, hierarchy, timestamp


@dataclass(frozen=True)
class RecoveryApproval:
    """Explicit local-operator approval, supplied independently at prepare and apply.

    Each mapping is (retired section, selected section, media type, exact event count).
    This is not a remote authorization mechanism; filesystem operators are trusted.
    """

    mappings: tuple[tuple[int, int, Literal["movie", "episode"], int], ...]
    before_epoch: int
    confirmed_by: str
    confirmation: str

    def __post_init__(self) -> None:
        if not isinstance(self.mappings, tuple) or any(
            not isinstance(mapping, tuple) for mapping in self.mappings
        ):
            raise ValueError("Approval mappings must be immutable tuples")

    def approved(self) -> dict[int, tuple[int, str, int]]:
        if (
            not self.mappings
            or len(self.mappings) > 100
            or not 0 < self.before_epoch < 253402300799
            or not self.confirmed_by.strip()
            or not self.confirmation.strip()
        ):
            raise ValueError("Explicit bounded owner approval is required")
        result: dict[int, tuple[int, str, int]] = {}
        for old, target, kind, count in self.mappings:
            if (
                old < 1
                or target < 1
                or old == target
                or old in result
                or kind not in {"movie", "episode"}
                or not 1 <= count <= 1000000
            ):
                raise ValueError("Invalid or duplicate recovery mapping")
            result[old] = (target, kind, count)
        return result


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def prepare(
    conn: sqlite3.Connection,
    config: Settings,
    source: PlexSource,
    *,
    approval: RecoveryApproval,
) -> dict[str, Any]:
    """Build a complete dry run or fail; never use fuzzy identity or request other users."""
    approved = approval.approved()
    binding = json.loads(
        conn.execute("SELECT binding FROM identity WHERE singleton=1").fetchone()[0]
    )
    expected = {
        "plex_origin": config.plex_url.rstrip("/"),
        "account_id": config.plex_account_id,
        "username": config.plex_username,
        "server_id": config.plex_server_id,
        "history_account_id": config.plex_history_account_id,
        "history_account_name": config.plex_history_account_name,
        "history_binding_confirmed": config.history_binding_confirmed,
        "sections": sorted(set(config.library_sections)),
    }
    if binding != expected or not binding["history_binding_confirmed"]:
        raise ValueError("Configuration/cache identity mismatch")
    capabilities = source.connect()
    if (
        not capabilities.get("history_identity_verified")
        or source.server_id != binding["server_id"]
        or source.history_account_id != binding["history_account_id"]
    ):
        raise ValueError("History identity mismatch")
    records: list[dict[str, Any]] = []
    observed = utc_now()
    seen: set[str] = set()
    for old, (target, kind, count) in approved.items():
        if (
            old in source.sections
            or target not in config.library_sections
            or source.sections.get(target) != ("show" if kind == "episode" else "movie")
        ):
            raise ValueError("Historical section reused or target library conflicts")
        received = 0
        while received < count:
            size = min(1000, count - received)
            root = source.owner.query(
                "/status/sessions/history/all",
                params={
                    "accountID": source.history_account_id,
                    "librarySectionID": old,
                    "sort": "viewedAt:asc",
                    "viewedAt<": approval.before_epoch,
                },
                headers={
                    "X-Plex-Container-Start": str(received),
                    "X-Plex-Container-Size": str(size),
                },
            )
            if (
                root.tag != "MediaContainer"
                or int(root.attrib.get("totalSize", -1)) != count
                or int(root.attrib.get("offset", -1)) != received
                or len(root) != size
            ):
                raise ValueError("Historical pagination/count conflicts with approved cohort")
            for node in root:
                a = node.attrib
                event_id = a.get("historyKey", "")
                viewed = timestamp(a.get("viewedAt"))
                if (
                    int(a.get("accountID", -1)) != source.history_account_id
                    or int(a.get("librarySectionID", -1)) != old
                    or a.get("type") != kind
                    or not event_id.startswith("/status/sessions/history/")
                    or event_id in seen
                    or viewed is None
                    or not (0 <= int(a["viewedAt"]) < approval.before_epoch)
                ):
                    raise ValueError("Historical event identity or scope conflict")
                seen.add(event_id)
                row = conn.execute(
                    "SELECT * FROM viewing_events "
                    "WHERE server_id=? AND event_id=? AND account_id=?",
                    (source.server_id, event_id, source.history_account_id),
                ).fetchone()
                if row is None:
                    raise ValueError("Recovery may only enroll existing retained events")
                before = dict(row)
                if (
                    before["media_type"] != kind
                    or before["viewed_at"] != viewed
                    or before["section_id"] is not None
                    or before["rating_key"] is not None
                    or a.get("ratingKey") is not None
                    or before["title"] != (a.get("title", "")[:500] or None)
                ):
                    raise ValueError("Historical event changed; review before applying")
                # Display evidence only. IDs are deliberately not inferred from names or positions.
                metadata = {
                    "title": a.get("title", "")[:500] or None,
                    "originally_available_at": a.get("originallyAvailableAt"),
                    **hierarchy(a),
                }
                if metadata["parent_key"] or metadata["grandparent_key"]:
                    raise ValueError("Unexpected item linkage requires a new review")
                records.append({"before": before, "source_section_id": old, "metadata": metadata})
            received += len(root)
    plan = {
        "version": 2,
        "before_epoch": approval.before_epoch,
        "binding": binding,
        "confirmed_by": approval.confirmed_by,
        "confirmation": approval.confirmation,
        "observed_at": observed,
        "approved": {str(k): list(v) for k, v in approved.items()},
        "records": records,
    }
    return {**plan, "sha256": digest(plan)}


def apply(db: Database, plan: dict[str, Any], *, approval: RecoveryApproval) -> dict[str, Any]:
    """Atomically enroll exact audited events. Reapplying is idempotent, never unrevokes."""
    approved = approval.approved()
    body = {k: v for k, v in plan.items() if k != "sha256"}
    if digest(body) != plan["sha256"] or plan["version"] != 2:
        raise ValueError("Recovery plan checksum/version mismatch")
    if (
        plan["confirmed_by"] != approval.confirmed_by
        or plan["confirmation"] != approval.confirmation
        or plan["before_epoch"] != approval.before_epoch
    ):
        raise ValueError("Missing owner confirmation")
    # The plan cannot authorize itself: compare it with separately supplied approval.
    if plan["approved"] != {str(k): list(v) for k, v in approved.items()}:
        raise ValueError("Plan exceeds approved recovery")
    binding = plan["binding"]
    grouped: dict[int, list[dict[str, Any]]] = {old: [] for old in approved}
    for record in plan["records"]:
        grouped[record["source_section_id"]].append(record)
    if any(len(grouped[old]) != spec[2] for old, spec in approved.items()):
        raise ValueError("Recovery count mismatch")
    now = utc_now()
    ids = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if json.loads(conn.execute("SELECT binding FROM identity").fetchone()[0]) != binding:
            raise ValueError("Recovery binding mismatch")
        if not binding["history_binding_confirmed"]:
            raise ValueError("Unconfirmed history owner")
        for old, (target, kind, _) in approved.items():
            if target not in binding["sections"] or old in binding["sections"]:
                raise ValueError("Recovery selection mismatch")
            records = grouped[old]
            mapping_id = f"historical-{old}-{plan['sha256'][:24]}"
            ids.append(mapping_id)
            existing = conn.execute(
                "SELECT revoked_at FROM historical_library_mappings WHERE id=?", (mapping_id,)
            ).fetchone()
            if existing and existing[0] is not None:
                raise ValueError("Revoked mapping cannot be reactivated by replay")
            dates = [r["before"]["viewed_at"] for r in records]
            conn.execute(
                "INSERT OR IGNORE INTO historical_library_mappings VALUES"
                "(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                (
                    mapping_id,
                    binding["server_id"],
                    binding["history_account_id"],
                    old,
                    target,
                    kind,
                    min(dates),
                    max(dates),
                    plan["confirmed_by"],
                    plan["confirmation"],
                    now,
                    plan["sha256"],
                ),
            )
            for record in records:
                before = record["before"]
                key = (before["server_id"], before["event_id"], before["account_id"])
                row = conn.execute(
                    "SELECT * FROM viewing_events "
                    "WHERE server_id=? AND event_id=? AND account_id=?",
                    key,
                ).fetchone()
                if (
                    row is None
                    or dict(row) != before
                    or before["server_id"] != binding["server_id"]
                    or before["account_id"] != binding["history_account_id"]
                    or before["media_type"] != kind
                    or before["section_id"] is not None
                    or before["rating_key"] is not None
                    or not 0
                    <= datetime.fromisoformat(before["viewed_at"]).timestamp()
                    < approval.before_epoch
                    or record["metadata"].get("parent_key")
                    or record["metadata"].get("grandparent_key")
                ):
                    raise ValueError("Audited event no longer matches cache or approved scope")
                evidence = conn.execute(
                    "SELECT * FROM historical_event_evidence "
                    "WHERE server_id=? AND event_id=? AND account_id=?",
                    key,
                ).fetchone()
                values = (
                    *key,
                    mapping_id,
                    old,
                    before["viewed_at"],
                    kind,
                    None,
                    json.dumps(record["metadata"], sort_keys=True),
                    plan["observed_at"],
                )
                if evidence is not None and tuple(evidence) != values:
                    raise ValueError("Conflicting prior evidence; cannot overwrite or un-revoke")
                conn.execute(
                    "INSERT OR IGNORE INTO historical_event_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
        placeholders = ",".join("?" for _ in ids)
        actual = conn.execute(
            f"SELECT COUNT(*) FROM historical_event_evidence WHERE mapping_id IN ({placeholders})",
            ids,
        ).fetchone()[0]
        if actual != sum(spec[2] for spec in approved.values()):
            raise ValueError("Duplicate or missing recovery events")
    return {"enrolled_events": actual, "mapping_ids": ids, "plan_sha256": plan["sha256"]}


def revoke(db: Database, mapping_id: str, reason: str) -> None:
    """Local operator action; retained evidence remains auditable and replay cannot undo it."""
    if not reason.strip():
        raise ValueError("A revocation reason is required")
    with db.connect() as conn:
        row = conn.execute(
            "UPDATE historical_library_mappings SET revoked_at=?,revocation_reason=? "
            "WHERE id=? AND revoked_at IS NULL",
            (utc_now(), reason, mapping_id),
        )
        if row.rowcount != 1:
            raise ValueError("Mapping missing or already revoked")
