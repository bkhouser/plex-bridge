import math
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, cast
from xml.etree.ElementTree import Element

from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer

from plex_advisor.config import Settings
from plex_advisor.errors import PlexCompatibilityError
from plex_advisor.models import Media, MediaType, ViewingEvent
from plex_advisor.ratings import public_rating_fields
from plex_advisor.security import ReadOnlySession


def response_contract[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    """Translate changed SDK/XML fields without retaining private response text."""

    @wraps(function)
    def checked(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except (AttributeError, KeyError, TypeError, OverflowError) as exc:
            raise PlexCompatibilityError("Unsupported Plex response fields") from exc

    return checked


def container(root: Any) -> Element:
    if not isinstance(root, Element) or root.tag != "MediaContainer":
        raise PlexCompatibilityError("Unexpected Plex container")
    return root


def sections_response(root: Any) -> Element:
    root = container(root)
    keys: set[int] = set()
    for node in root:
        a = node.attrib
        if (
            node.tag != "Directory"
            or not a.get("key", "").isdigit()
            or int(a["key"]) < 1
            or int(a["key"]) in keys
            or not a.get("title")
            or not a.get("type")
        ):
            raise PlexCompatibilityError("Unsupported library directory response")
        keys.add(int(a["key"]))
    return cast(Element, root)


def timestamp(value: str | None) -> str | None:
    return datetime.fromtimestamp(int(value), UTC).isoformat() if value is not None else None


def optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise PlexCompatibilityError("Nonfinite Plex numeric field")
    return number


def hierarchy(a: dict[str, str]) -> dict[str, Any]:
    season = (
        a.get("parentIndex")
        if a.get("type") == "episode"
        else (a.get("index") if a.get("type") == "season" else None)
    )
    return {
        "parent_key": a.get("parentRatingKey"),
        "grandparent_key": a.get("grandparentRatingKey"),
        "parent_title": a.get("parentTitle"),
        "grandparent_title": a.get("grandparentTitle"),
        "season_number": int(season) if season is not None else None,
        "episode_number": int(a["index"]) if a.get("type") == "episode" and "index" in a else None,
    }


@response_contract
def parse_media(node: Element, server_id: str, section: int) -> Media:
    a = node.attrib
    key, media_type = a["ratingKey"], a["type"]
    if media_type not in {"movie", "show", "season", "episode"}:
        raise PlexCompatibilityError("Unsupported media type")
    if node.tag != ("Video" if media_type in {"movie", "episode"} else "Directory"):
        raise PlexCompatibilityError("Unexpected media element")
    if not key or not a["title"]:
        raise PlexCompatibilityError("Missing media identity or title")
    if "librarySectionID" in a and int(a["librarySectionID"]) != section:
        raise PlexCompatibilityError("Media returned from an unexpected library")
    guid = a.get("guid")
    # Canonical Plex GUID is shared by editions/copies; legacy/local agent GUIDs may collide.
    canonical = (
        f"{server_id}:{guid}" if guid and guid.startswith("plex://") else f"{server_id}:item:{key}"
    )
    duration = optional_float(a.get("duration"))
    # PMS omits zero viewCount. This follows Python-PlexAPI's documented media semantics.
    views = int(a.get("viewCount", "0"))
    if views < 0 or (duration is not None and duration < 0) or int(a.get("viewOffset", "0")) < 0:
        raise PlexCompatibilityError("Negative Plex state or duration")
    if media_type in {"show", "season"}:
        leaves = a.get("leafCount")
        watched = (
            (int(a.get("viewedLeafCount", "0")) >= int(leaves)) if leaves and int(leaves) else None
        )
    else:
        watched = views > 0
    return Media(
        server_id=server_id,
        rating_key=key,
        section_id=section,
        canonical_id=canonical,
        guid=guid,
        media_type=cast(MediaType, media_type),
        title=a["title"][:500],
        original_title=a.get("originalTitle"),
        year=int(a["year"]) if "year" in a else None,
        release_date=a.get("originallyAvailableAt"),
        runtime_minutes=duration / 60000 if duration else None,
        summary=a.get("summary", "")[:12000] or None,
        tagline=a.get("tagline"),
        studio=a.get("studio"),
        content_rating=a.get("contentRating"),
        **public_rating_fields(
            optional_float(a.get("rating")),
            a.get("ratingImage"),
            optional_float(a.get("audienceRating")),
            a.get("audienceRatingImage"),
        ),
        added_at=timestamp(a.get("addedAt")),
        updated_at=timestamp(a.get("updatedAt")),
        edition=a.get("editionTitle"),
        external_ids=[x.attrib["id"] for x in node.findall("Guid") if "id" in x.attrib][:30],
        genres=[x.attrib["tag"][:100] for x in node.findall("Genre") if "tag" in x.attrib][:50],
        people={
            role: [x.attrib["tag"][:200] for x in node.findall(tag) if "tag" in x.attrib][:30]
            for role, tag in [("actor", "Role"), ("director", "Director"), ("writer", "Writer")]
        },
        versions=[
            {
                k: x.attrib.get(k)
                for k in ["id", "videoResolution", "videoCodec", "audioCodec", "container"]
            }
            for x in node.findall("Media")
        ][:20],
        **hierarchy(a),
        watched=watched,
        view_count=views,
        last_viewed_at=timestamp(a.get("lastViewedAt")),
        personal_rating=optional_float(a.get("userRating")),
        resume_ms=int(a["viewOffset"]) if "viewOffset" in a else 0,
    )


class PlexSource:
    def __init__(self, config: Settings):
        self.config = config
        self.server_id = ""
        self.history_account_id: int | None = None
        self.server: Any = None
        self.owner: Any = None
        self.sections: dict[int, str] = {}

    def _server(self, token: str) -> Any:
        if not token or not token.strip():
            raise ValueError("Missing verified session token")
        return PlexServer(
            self.config.plex_url.rstrip("/"),
            token=token,  # type: ignore[no-untyped-call]
            session=ReadOnlySession(self.config),
            timeout=self.config.timeout_seconds,
        )

    @response_contract
    def discover(self) -> dict[str, Any]:
        self.config.require_plex(identity=False)
        self.owner = self._server(self.config.plex_token.get_secret_value())
        account = self.owner.myPlexAccount()
        sections = sections_response(self.owner.query("/library/sections"))
        result: dict[str, Any] = {
            "server_name": self.owner.friendlyName,
            "server_id": self.owner.machineIdentifier,
            "token_account": {"plex_account_id": account.id, "username": account.username},
            "libraries": [
                {
                    "section_id": int(x.attrib["key"]),
                    "title": x.attrib["title"],
                    "type": x.attrib["type"],
                }
                for x in sections
            ],
            "users": [],
            "system_accounts": [],
        }
        try:
            result["users"] = [
                {
                    "plex_account_id": u.id,
                    "username": u.username,
                    "display_name": u.title,
                    "managed": bool(u.restricted),
                }
                for u in account.users()
            ]
        except Exception:
            result["user_discovery"] = "unavailable"
        try:
            result["system_accounts"] = [
                {"history_account_id": u.id, "name": u.name} for u in self.owner.systemAccounts()
            ]
        except Exception:
            result["history_identity_discovery"] = "unavailable"
        return result

    @response_contract
    def connect(self) -> dict[str, Any]:
        self.config.require_plex()
        if not self.config.library_sections:
            raise ValueError("Select movie or TV library sections first")
        self.owner = self._server(self.config.plex_token.get_secret_value())
        account = self.owner.myPlexAccount()
        self.server_id = str(self.owner.machineIdentifier)
        if self.server_id != self.config.plex_server_id:
            raise ValueError("Server identifier does not match configured server")
        if account.id == self.config.plex_account_id:
            if account.username != self.config.plex_username:
                raise ValueError("Configured account identity does not match")
            self.server = self.owner
        else:
            users = [
                u
                for u in account.users()
                if u.id == self.config.plex_account_id and u.username == self.config.plex_username
            ]
            if len(users) != 1:
                raise ValueError("Target account not unambiguously identified")
            # Same token endpoint as switchUser; reject missing or ambiguous tokens first.
            shares = account.query(account.FRIENDINVITE.format(machineId=self.server_id))
            tokens = [
                x.attrib.get("accessToken")
                for x in shares
                if x.attrib.get("userID") == str(self.config.plex_account_id)
            ]
            if len(tokens) != 1 or not tokens[0] or not tokens[0].strip():
                raise ValueError("No unique target-user share token")
            token = tokens[0]
            self.server = self._server(token)
            verified = MyPlexAccount(
                token=token,
                session=ReadOnlySession(self.config),  # type: ignore[no-untyped-call]
                timeout=self.config.timeout_seconds,
            )
            if (
                verified.id != self.config.plex_account_id
                or verified.username != self.config.plex_username
            ):
                raise ValueError("Switched user session could not be verified")
        if str(self.server.machineIdentifier) != self.server_id:
            raise ValueError("Unexpected switched server")
        # Map local history identity independently; never equate a cloud ID to history accountID.
        try:
            accounts = self.owner.systemAccounts()
            matching = [u for u in accounts if u.id == self.config.plex_history_account_id]
            names = [u for u in accounts if u.name == self.config.plex_history_account_name]
            self.history_account_id = (
                int(matching[0].id)
                if self.config.history_binding_confirmed
                and len(matching) == 1
                and len(names) == 1
                and matching[0].name == self.config.plex_history_account_name
                else None
            )
        except Exception:
            self.history_account_id = None
        libraries = sections_response(self.server.query("/library/sections"))
        self.sections = {int(x.attrib["key"]): x.attrib["type"] for x in libraries}
        if any(self.sections.get(x) not in {"movie", "show"} for x in self.config.library_sections):
            raise ValueError("Selected section missing or not a movie/TV section")
        return {
            "verified": True,
            "plex_server_version": (
                version if re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{0,63}", version) else "unknown"
            )
            if isinstance(version := getattr(self.server, "version", None), str)
            else "unknown",
            "catalog": True,
            "user_session_verified": True,
            "history_identity_verified": self.history_account_id is not None,
            "history_binding": "operator-selected local ID, revalidated against API account row",
            "history_access": "not_yet_probed",
            "ratings": "per-user; null if absent",
            "watched_state": "per-user Plex viewCount semantics",
        }

    def _pages(self, server: Any, path: str, params: dict[str, Any]) -> Iterator[Element]:
        offset = 0
        total: int | None = None
        seen: set[str] = set()
        while True:
            root = server.query(
                path,
                params=params,
                headers={
                    "X-Plex-Container-Start": str(offset),
                    "X-Plex-Container-Size": str(self.config.page_size),
                },
            )
            root = container(root)
            items = list(root)
            try:
                page_total = int(root.attrib["totalSize"]) if "totalSize" in root.attrib else None
                size = int(root.attrib["size"]) if "size" in root.attrib else None
                reported_offset = int(root.attrib.get("offset", offset))
            except (TypeError, ValueError) as exc:
                raise PlexCompatibilityError("Invalid pagination fields") from exc
            if (
                (page_total is not None and page_total < 0)
                or (size is not None and size != len(items))
                or (not items and page_total is None and size is None)
            ):
                raise PlexCompatibilityError("Missing or inconsistent page size")
            if total is None:
                total = page_total
            elif page_total != total:
                raise PlexCompatibilityError("Plex collection changed during pagination")
            if reported_offset != offset:
                raise PlexCompatibilityError("Plex ignored pagination offset")
            for item in items:
                key = item.attrib.get("historyKey", item.attrib.get("ratingKey"))
                if not key or key in seen:
                    raise PlexCompatibilityError("Plex repeated or omitted a stable item identity")
                seen.add(key)
            if not items:
                if total is not None and offset < total:
                    raise PlexCompatibilityError("Incomplete page")
                break
            if len(items) > self.config.page_size or (
                total is not None and offset + len(items) > total
            ):
                raise PlexCompatibilityError("Plex ignored requested page size or total")
            yield from items
            offset += len(items)
            if total is not None and offset >= total:
                break

    def media(self, section_id: int) -> Iterator[Media]:
        types = [1] if self.sections[section_id] == "movie" else [2, 3, 4]
        for item_type in types:
            for node in self._pages(
                self.server,
                f"/library/sections/{section_id}/all",
                {"type": item_type, "includeGuids": 1, "sort": "id:asc"},
            ):
                try:
                    item = parse_media(node, self.server_id, section_id)
                    if (
                        item.media_type
                        != {1: "movie", 2: "show", 3: "season", 4: "episode"}[item_type]
                    ):
                        raise PlexCompatibilityError("Plex ignored requested media type")
                    yield item
                except (TypeError, ValueError, OverflowError) as exc:
                    raise PlexCompatibilityError("Unsupported media response") from exc

    def history(self, since: str | None) -> Iterator[ViewingEvent]:
        if self.history_account_id is None:
            raise ValueError("History identity unavailable")
        params: dict[str, Any] = {
            "accountID": self.history_account_id,
            "sort": "viewedAt:asc",
            "viewedAt<": int(datetime.now(UTC).timestamp()) + 1,
        }
        if since:
            params["viewedAt>"] = int(
                (datetime.fromisoformat(since) - timedelta(days=2)).timestamp()
            )
        for section in sorted(set(self.config.library_sections)):
            scoped = {**params, "librarySectionID": section}
            for node in self._pages(self.owner, "/status/sessions/history/all", scoped):
                try:
                    yield self._history_event(node, section)
                except PlexCompatibilityError:
                    raise
                except (ValueError, OverflowError) as exc:
                    raise PlexCompatibilityError("Unsupported history response") from exc

    @response_contract
    def _history_event(self, node: Element, section: int) -> ViewingEvent:
        if node.tag != "Video" or node.attrib.get("type") not in {"movie", "episode"}:
            raise PlexCompatibilityError("Unexpected history media type")
        a = node.attrib
        if int(a["accountID"]) != self.history_account_id:
            raise ValueError("History endpoint returned unauthorized identity")
        section_id = int(a["librarySectionID"]) if "librarySectionID" in a else None
        if section_id is not None and section_id != section:
            raise PlexCompatibilityError("History endpoint returned an unselected section")
        event = a.get("historyKey")
        if not event or not event.startswith("/status/sessions/history/"):
            raise ValueError("Stable history identifier unavailable")
        viewed = timestamp(a.get("viewedAt"))
        if not viewed:
            raise ValueError("Viewing timestamp unavailable")
        return ViewingEvent(
            event_id=event,
            server_id=self.server_id,
            rating_key=a.get("ratingKey"),
            account_id=self.history_account_id,
            viewed_at=viewed,
            media_type=a["type"],
            title=a.get("title", "")[:500] or None,
            section_id=section_id,
            **hierarchy(a),
        )

    def diagnose(self) -> dict[str, Any]:
        capabilities = self.connect()
        samples = []
        for section in self.config.library_sections:
            sample = next(self.media(section), None)
            samples.append(
                {"section_id": section, "sample": sample.model_dump() if sample else None}
            )
        try:
            event = next(self.history(None), None)
            capabilities["history_access"] = "verified"
            capabilities["sample_history_event"] = event.model_dump() if event else None
        except Exception:
            capabilities["history_access"] = "unavailable_or_unverified"
        return {"capabilities": capabilities, "samples_for_plex_ui_comparison": samples}
