from typing import Any


def public_rating_fields(
    rating: float | None,
    rating_source: str | None,
    audience_rating: float | None,
    audience_source: str | None,
) -> dict[str, Any]:
    """Keep Plex's raw slots while distinguishing the provider's actual metric.

    Plex's generic rating slot is not necessarily a critic score. Unknown sources
    remain inspectable, but are excluded from critic/audience aliases and sorts.
    """
    result: dict[str, Any] = {
        "public_ratings": [],
        "critic_rating": None,
        "critic_source": None,
        "audience_rating": None,
        "audience_source": None,
    }
    for field, value, source in [
        ("rating", rating, rating_source),
        ("audienceRating", audience_rating, audience_source),
    ]:
        if value is None:
            continue
        normalized = (source or "").casefold()
        scheme = normalized.partition("://")[0] if "://" in normalized else "unknown"
        providers = {
            "imdb": "IMDb",
            "tmdb": "TMDB",
            "themoviedb": "TMDB",
            "tvdb": "TVDB",
            "thetvdb": "TVDB",
            "rottentomatoes": "Rotten Tomatoes",
        }
        provider = providers.get(scheme, "unknown")
        metric = "unknown"
        if provider in {"IMDb", "TMDB", "TVDB"}:
            metric = "audience"
        elif provider == "Rotten Tomatoes":
            icon = normalized.partition("://")[2]
            if icon in {"image.rating.ripe", "image.rating.rotten", "image.rating.certified"}:
                metric = "critic"
            elif icon in {"image.rating.upright", "image.rating.spilled"}:
                metric = "audience"
        result["public_ratings"].append(
            {
                "plex_field": field,
                "value": value,
                "source": source,
                "provider": provider,
                "metric": metric,
                "scale": "0-10",
            }
        )
        if metric != "unknown":
            # Prefer the native audience slot if both slots describe audience scores.
            result[f"{metric}_rating"] = value
            result[f"{metric}_source"] = source
    return result


def upgrade_public_ratings(metadata: dict[str, Any]) -> dict[str, Any]:
    """Reclassify v1 cached slots without changing the personal userRating."""
    if "public_ratings" in metadata:
        return metadata
    return {
        **metadata,
        **public_rating_fields(
            metadata.get("critic_rating"),
            metadata.get("critic_source"),
            metadata.get("audience_rating"),
            metadata.get("audience_source"),
        ),
    }
