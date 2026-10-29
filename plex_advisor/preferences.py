import json
from typing import Any

from plex_advisor.database import Database
from plex_advisor.models import Feedback, utc_now


class Preferences:
    def __init__(self, db: Database):
        self.db = db

    def record(self, feedback: Feedback) -> dict[str, Any]:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            media = conn.execute(
                "SELECT canonical_id FROM media_items WHERE id=?", (feedback.media_id,)
            ).fetchone()
            if not media:
                raise ValueError("Search and confirm an unambiguous media identifier first")
            now = utc_now()
            values = (
                feedback.rating,
                feedback.sentiment,
                feedback.comments,
                json.dumps(feedback.positive_attributes),
                json.dumps(feedback.negative_attributes),
                now,
                feedback.source,
            )
            record_id: int | None
            if feedback.feedback_id:
                changed = conn.execute(
                    "UPDATE personal_feedback SET rating=?,sentiment=?,comments=?,"
                    "positive_attributes=?,negative_attributes=?,updated_at=?,source=? "
                    "WHERE id=? AND canonical_id=?",
                    (*values, feedback.feedback_id, media[0]),
                )
                if not changed.rowcount:
                    raise ValueError("Feedback not found for this title")
                record_id = feedback.feedback_id
            else:
                record_id = conn.execute(
                    "INSERT INTO personal_feedback "
                    "(rating,sentiment,comments,positive_attributes,negative_attributes,updated_at,"
                    "source,canonical_id,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (*values, media[0], now),
                ).lastrowid
        return {"feedback_id": record_id, "saved_locally": True, "plex_modified": False}

    def list(self, media_id: str | None = None, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            raise ValueError("Invalid pagination")
        where, args = "1=1", []
        if media_id:
            where = "canonical_id=(SELECT canonical_id FROM media_items WHERE id=?)"
            args = [media_id]
        with self.db.connect() as conn:
            count = conn.execute(
                f"SELECT COUNT(*) FROM personal_feedback WHERE {where}", args
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM personal_feedback WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            for field in ["positive_attributes", "negative_attributes"]:
                item[field] = json.loads(item[field])
        return {
            "feedback": items,
            "total": count,
            "next_offset": offset + len(items) if offset + len(items) < count else None,
        }

    def delete(self, feedback_id: int, authorized: bool) -> dict[str, Any]:
        if authorized is not True or feedback_id < 1:
            raise ValueError("Explicit authorization and valid feedback ID required")
        with self.db.connect() as conn:
            cursor = conn.execute("DELETE FROM personal_feedback WHERE id=?", (feedback_id,))
        return {"deleted": bool(cursor.rowcount), "plex_modified": False}
