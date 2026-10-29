import hashlib
from typing import Any

from pydantic import Field

from plex_advisor.catalog import Catalog
from plex_advisor.database import Database
from plex_advisor.models import Contract, MediaType, Search
from plex_advisor.preferences import Preferences


class CandidateQuery(Contract):
    filters: Search = Field(default_factory=lambda: Search(watched=False))
    keywords: list[str] = Field(default_factory=list, max_length=10)
    exclude_media_ids: list[str] = Field(default_factory=list, max_length=100)
    diversity_seed: str = Field(default="", max_length=100)
    limit: int = Field(default=30, ge=1, le=50)


def candidates(db: Database, query: CandidateQuery) -> dict[str, Any]:
    if any(len(k) > 80 for k in query.keywords):
        raise ValueError("Keywords must be at most 80 characters")
    catalog = Catalog(db)
    filters = query.filters.model_copy(update={"limit": 100, "offset": 0, "media_type": "movie"})
    # Scan bounded pages, retain only the best few. No AI calls and no whole-library result.
    best: dict[str, dict[str, Any]] = {}
    excluded_canonical = set(query.exclude_media_ids)
    if query.exclude_media_ids:
        placeholders = ",".join("?" for _ in query.exclude_media_ids)
        with db.connect() as conn:
            excluded_canonical.update(
                row[0]
                for row in conn.execute(
                    f"SELECT canonical_id FROM media_items WHERE id IN ({placeholders})",
                    query.exclude_media_ids,
                )
            )
    matching_exclusions: set[str] = set()
    total = 0
    while True:
        page = catalog.search(filters)
        total = page["total_titles"]
        for item in page["items"]:
            if item["canonical_id"] in excluded_canonical:
                matching_exclusions.add(item["canonical_id"])
                continue
            text = (
                item["title"] + " " + (item["synopsis"] or "") + " " + " ".join(item["genres"])
            ).casefold()
            matches = [k for k in query.keywords if k.casefold() in text]
            feedback = Preferences(db).list(item["media_id"], limit=5)["feedback"]
            score = len(matches) * 2.0
            evidence = [f"Metadata keyword: {k}" for k in matches]
            if item["personal_rating"] is not None:
                score += (item["personal_rating"] - 5) / 2
                evidence.append("Explicit Plex personal rating")
            for review in feedback:
                if review["sentiment"] == "negative":
                    score -= 4
                elif review["sentiment"] == "positive":
                    score += 2
                if review["rating"] is not None:
                    score += (review["rating"] - 5) / 2
            if feedback:
                evidence.append("Explicit local feedback included for interpretation")
            item.update(
                score=score,
                match_evidence=evidence or ["Matches supplied metadata filters"],
                qualitative_feedback=feedback,
            )
            item["tie_break"] = hashlib.sha256(
                (query.diversity_seed + item["canonical_id"]).encode()
            )
            item["tie_break"] = item["tie_break"].hexdigest()
            canonical = item["canonical_id"]
            if canonical not in best or score > best[canonical]["score"]:
                best[canonical] = item
            if len(best) > query.limit * 2:
                best = {
                    x["canonical_id"]: x
                    for x in sorted(best.values(), key=lambda x: (-x["score"], x["tie_break"]))[
                        : query.limit
                    ]
                }
        if page["next_offset"] is None:
            break
        filters.offset = page["next_offset"]
    items = sorted(best.values(), key=lambda x: (-x["score"], x["tie_break"]))[: query.limit]
    for item in items:
        item.pop("tie_break")
    return {
        "candidates": items,
        "matching_titles": total - len(matching_exclusions),
        "matching_titles_before_exclusions": total,
        "method": "Metadata filters, keywords and personal evidence; no public-rating boost",
        "interpretation": "Mood and writing quality need assistant judgment; keywords are weak",
        "diversity": "Canonical deduplication, caller seed/exclusions; no hidden tracking",
    }


def taste_profile(db: Database) -> dict[str, Any]:
    catalog = Catalog(db)
    with db.connect() as conn:
        sample = conn.execute(
            "SELECT COUNT(*),COUNT(personal_rating),SUM(watched=1) FROM user_media_state s "
            "JOIN media_items m ON m.id=s.media_id WHERE m.available=1"
        ).fetchone()
        genres = conn.execute(
            "SELECT g.genre,COUNT(*) titles,SUM(s.view_count) current_play_count "
            "FROM media_genres g JOIN user_media_state s ON g.media_id=s.media_id "
            "WHERE s.watched=1 GROUP BY g.genre ORDER BY titles DESC LIMIT 15"
        ).fetchall()
        people = conn.execute(
            "SELECT p.role,p.name,COUNT(*) titles FROM media_people p "
            "JOIN user_media_state s ON p.media_id=s.media_id WHERE s.watched=1 "
            "AND p.role IN ('actor','director') GROUP BY p.role,p.name "
            "ORDER BY titles DESC LIMIT 20"
        ).fetchall()
    media_types: list[MediaType] = ["movie", "show", "season", "episode"]
    high, low = [], []
    for media_type in media_types:
        high.extend(
            catalog.search(
                Search(
                    media_type=media_type, personal_rating_min=8, sort="personal_rating", limit=15
                )
            )["items"]
        )
        # Query the complete low-rated set in pages so the lowest values win globally.
        filters = Search(media_type=media_type, personal_rating_max=4, limit=100)
        while True:
            page = catalog.search(filters)
            low.extend(page["items"])
            low = sorted(low, key=lambda x: (x["personal_rating"], x["media_id"]))[:15]
            if page["next_offset"] is None:
                break
            filters.offset = page["next_offset"]
    return {
        "sample_sizes": {"cached_states": sample[0], "rated": sample[1], "watched": sample[2] or 0},
        "high_personal_ratings": sorted(high, key=lambda x: (-x["personal_rating"], x["media_id"]))[
            :15
        ],
        "low_personal_ratings": low,
        "personal_rating_scope": "Available movies, shows, seasons and episodes; Plex userRating",
        "rewatches": [
            x
            for x in catalog.search(Search(sort="view_count", limit=15))["items"]
            if (x["view_count"] or 0) > 1
        ],
        "qualitative_feedback": Preferences(db).list(limit=30),
        "behavioral_genres": [dict(r) for r in genres],
        "behavioral_people": [dict(r) for r in people],
        "caution": "Rewatching is a weak signal, not proof of liking; sparse samples are uncertain",
    }
