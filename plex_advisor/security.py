import asyncio
import hmac
import logging
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

import jwt
import requests
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from plex_advisor.config import Settings

# Process-owned pool: shared admission bounds queued and running work across app instances.
# Python's executor shutdown joins its workers when the process exits.
_VERIFICATION_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="plex-auth")
_VERIFICATION_SLOTS = threading.BoundedSemaphore(4)


class ReadOnlySession(requests.Session):
    """Deny even GET-based Plex mutations and redirects to non-configured origins."""

    def __init__(self, config: Settings):
        super().__init__()
        self.config = config
        self.trust_env = False

    def request(self, method: Any, url: Any, *args: Any, **kwargs: Any) -> requests.Response:
        if args or not isinstance(method, str) or not isinstance(url, str):
            raise ValueError("Only explicit keyword read requests are supported")
        target = urlsplit(url)
        origin = urlsplit(self.config.plex_url)
        pms = (target.scheme, target.netloc) == (origin.scheme, origin.netloc)
        cloud = target.scheme == "https" and target.netloc == "plex.tv"
        paths = (
            r"/(?:|identity|myplex/account|accounts|library/sections|"
            r"library/sections/\d+/all|status/sessions/history/all)"
        )
        cloud_paths = (
            r"/(?:api/v2/user|api/users/?|api/home/users|"
            r"api/servers/[^/]+/shared_servers)"
        )
        allowed = (pms and re.fullmatch(paths, target.path)) or (
            cloud and re.fullmatch(cloud_paths, target.path)
        )
        if method.upper() != "GET" or not allowed or target.username or target.password:
            raise ValueError("Plex request denied by read-only policy")
        kwargs["allow_redirects"] = False
        kwargs["timeout"] = self.config.timeout_seconds
        result = super().request(method, url, **kwargs)
        if 300 <= result.status_code < 400:
            raise ValueError("Plex redirect denied")
        return result


class _BackoffJWKClient(jwt.PyJWKClient):
    """Share failed-fetch backoff without caching tokens or accepting stale keys."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._refresh_lock = threading.Lock()
        self._retry_after = 0.0

    def fetch_data(self) -> Any:
        with self._refresh_lock:
            if time.monotonic() < self._retry_after:
                raise jwt.PyJWKClientConnectionError("Signing keys temporarily unavailable")
            try:
                data = super().fetch_data()
            except Exception:
                self._retry_after = time.monotonic() + 30
                raise
            self._retry_after = 0.0
            return data


class TokenVerifier:
    def __init__(self, config: Settings):
        self.config = config
        # No waiting queue before executor submission, including during a JWKS outage.
        self._verification_slots = _VERIFICATION_SLOTS
        self.jwks = (
            _BackoffJWKClient(
                config.oauth_jwks_url,
                timeout=config.timeout_seconds,
                cache_jwk_set=True,
                lifespan=300,
            )
            if config.auth_mode == "oauth"
            else None
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        if self.config.auth_mode == "local":
            if not hmac.compare_digest(token, self.config.local_bearer_token.get_secret_value()):
                return None
            return AccessToken(
                token=token,
                client_id="local-owner",
                subject="local-owner",
                scopes=["library:read", "feedback:write"],
                resource=f"http://localhost:{self.config.mcp_port}/mcp",
            )
        loop = asyncio.get_running_loop()
        slots = self._verification_slots
        if not slots.acquire(blocking=False):
            return None
        try:
            pending = _VERIFICATION_EXECUTOR.submit(self._verify_jwt, token)
        except BaseException:
            slots.release()
            raise
        # The concurrent future owns release, even if cancelled before its worker starts.
        # A cancelled caller cannot release admission while a running worker still uses it.
        pending.add_done_callback(lambda _: slots.release())
        return await asyncio.wrap_future(pending, loop=loop)

    def _verify_jwt(self, token: str) -> AccessToken | None:
        try:
            assert self.jwks is not None
            if jwt.get_unverified_header(token).get("typ") != "at+jwt":
                return None
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.config.oauth_issuer,
                audience=self.config.oauth_resource,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
            scopes = claims.get("scope", "").split()
            if claims["sub"] != self.config.oauth_subject or "library:read" not in scopes:
                return None
            return AccessToken(
                token=token,
                client_id=str(claims.get("azp", claims["sub"])),
                subject=claims["sub"],
                scopes=scopes,
                expires_at=int(claims["exp"]),
                resource=self.config.oauth_resource,
                claims={"iss": self.config.oauth_issuer},
            )
        except Exception:
            return None


class RequestLimits:
    """After authentication, keep exactly two bounded quotas, never token-keyed state."""

    def __init__(self, app: ASGIApp, requests_per_minute: int):
        self.app = app
        self.limit = requests_per_minute
        self.owner_arrivals: deque[float] = deque()
        self.anonymous_arrivals: deque[float] = deque()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            arrivals = (
                self.owner_arrivals
                if isinstance(scope.get("user"), AuthenticatedUser)
                else self.anonymous_arrivals
            )
            now = time.monotonic()
            while arrivals and arrivals[0] < now - 60:
                arrivals.popleft()
            if len(arrivals) >= self.limit:
                await JSONResponse(
                    {"error": "Rate limit exceeded"}, 429, headers={"Retry-After": "60"}
                )(scope, receive, send)
                return
            arrivals.append(now)
        await self.app(scope, receive, send)


class HTTPOnly:
    """Apply request authentication without intercepting ASGI lifespan startup/shutdown."""

    def __init__(self, app: ASGIApp, protected: ASGIApp):
        self.app, self.protected = app, protected

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        target = self.protected if scope["type"] == "http" else self.app
        await target(scope, receive, send)


def configure_logging(level: str) -> None:
    # No provider request URLs, headers, bodies, credentials, or raw exceptions are logged.
    logging.basicConfig(level=level, format="%(levelname)s %(name)s %(message)s")
    for name in ["plexapi", "urllib3", "requests", "httpx", "httpcore"]:
        logger = logging.getLogger(name)
        logger.disabled = True
        logger.propagate = False
