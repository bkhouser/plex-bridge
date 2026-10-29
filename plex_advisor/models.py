import re
import unicodedata
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

MediaType = Literal["movie", "show", "season", "episode"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicRating(Contract):
    plex_field: Literal["rating", "audienceRating"]
    value: float
    source: str | None = None
    provider: str
    metric: Literal["critic", "audience", "unknown"]
    scale: Literal["0-10"] = "0-10"


class Media(Contract):
    server_id: str
    rating_key: str
    section_id: int
    canonical_id: str
    guid: str | None = None
    media_type: MediaType
    title: str
    original_title: str | None = None
    year: int | None = None
    release_date: str | None = None
    runtime_minutes: float | None = None
    summary: str | None = None
    tagline: str | None = None
    studio: str | None = None
    content_rating: str | None = None
    critic_rating: float | None = None
    critic_source: str | None = None
    audience_rating: float | None = None
    audience_source: str | None = None
    public_ratings: list[PublicRating] = Field(default_factory=list)
    added_at: str | None = None
    updated_at: str | None = None
    edition: str | None = None
    versions: list[dict[str, str | None]] = Field(default_factory=list)
    external_ids: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    people: dict[str, list[str]] = Field(default_factory=dict)
    parent_key: str | None = None
    grandparent_key: str | None = None
    parent_title: str | None = None
    grandparent_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    watched: bool | None = None
    view_count: int | None = None
    last_viewed_at: str | None = None
    personal_rating: float | None = Field(default=None, ge=0, le=10)
    resume_ms: int | None = None


class ViewingEvent(Contract):
    event_id: str
    server_id: str
    rating_key: str | None = None
    account_id: int
    viewed_at: str
    media_type: str
    title: str | None = None
    section_id: int | None = None
    parent_key: str | None = None
    grandparent_key: str | None = None
    parent_title: str | None = None
    grandparent_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None


class Search(Contract):
    query: str | None = Field(default=None, max_length=200)
    media_type: MediaType = "movie"
    genre: str | None = Field(default=None, max_length=80)
    genres_all: list[str] = Field(default_factory=list, max_length=10)
    actor: str | None = Field(default=None, max_length=120)
    director: str | None = Field(default=None, max_length=120)
    year_min: int | None = Field(default=None, ge=1800, le=2200)
    year_max: int | None = Field(default=None, ge=1800, le=2200)
    max_runtime: float | None = Field(default=None, gt=0, le=2000)
    watched: bool | None = None
    min_view_count: int | None = Field(default=None, ge=0)
    personal_rating_min: float | None = Field(default=None, ge=0, le=10)
    personal_rating_max: float | None = Field(default=None, ge=0, le=10)
    section_id: int | None = Field(default=None, gt=0)
    show_id: str | None = Field(default=None, max_length=300)
    season_number: int | None = Field(default=None, ge=0)
    episode_number: int | None = Field(default=None, ge=0)
    sort: Literal[
        "title",
        "year",
        "personal_rating",
        "critic_rating",
        "audience_rating",
        "view_count",
        "recently_added",
    ] = "title"
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000000)

    @model_validator(mode="after")
    def ranges(self) -> Self:
        if any(len(g) > 80 for g in self.genres_all):
            raise ValueError("Genre names must be at most 80 characters")
        for lower, upper in [
            (self.year_min, self.year_max),
            (self.personal_rating_min, self.personal_rating_max),
        ]:
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("Minimum must not exceed maximum")
        return self


class HistoryQuery(Contract):
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    media_type: MediaType | None = None
    media_id: str | None = Field(default=None, max_length=300)
    show_id: str | None = Field(default=None, max_length=300)
    season_number: int | None = Field(default=None, ge=0)
    episode_number: int | None = Field(default=None, ge=0)
    timezone: str | None = Field(default=None, max_length=80)
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000000)

    @model_validator(mode="after")
    def dates(self) -> Self:
        if self.start and self.end and self.start >= self.end:
            raise ValueError("start must precede end; end is exclusive")
        return self


class AvailabilityTitle(Contract):
    title: str = Field(min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=1800, le=2200)


class Feedback(Contract):
    media_id: str = Field(min_length=1, max_length=300)
    rating: float | None = Field(default=None, ge=0, le=10)
    sentiment: Literal["positive", "negative", "mixed", "neutral", "unspecified"] = "unspecified"
    comments: str = Field(min_length=1, max_length=4000)
    positive_attributes: list[str] = Field(default_factory=list, max_length=20)
    negative_attributes: list[str] = Field(default_factory=list, max_length=20)
    source: str = Field(default="explicit user instruction", min_length=1, max_length=200)
    authorized: Literal[True]
    feedback_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def bounded_attributes(self) -> Self:
        if any(len(x) > 120 for x in self.positive_attributes + self.negative_attributes):
            raise ValueError("Attributes must be at most 120 characters")
        return self
