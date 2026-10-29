import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import urlsplit

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from plex_advisor import security
from plex_advisor.mcp_server import create_app
from plex_advisor.security import ReadOnlySession, RequestLimits, TokenVerifier


def test_local_auth_and_host_validation(config, populated):
    with TestClient(create_app(config, populated[0]), base_url="http://localhost:8765") as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        headers = {"Accept": "application/json, text/event-stream"}
        assert client.post("/mcp", json=body, headers=headers).status_code == 401
        headers["Authorization"] = "Bearer wrong"
        assert client.post("/mcp", json=body, headers=headers).status_code == 401
        headers["Authorization"] = "Bearer " + config.local_bearer_token.get_secret_value()
        headers["Host"] = "attacker.example"
        assert client.post("/mcp", json=body, headers=headers).status_code == 421
        headers["Host"] = "localhost:8765"
        headers["Origin"] = "https://attacker.example"
        assert client.post("/mcp", json=body, headers=headers).status_code == 403


def test_public_local_mode_rejected(config):
    with pytest.raises(ValueError, match="loopback"):
        config.model_copy(update={"mcp_host": "0.0.0.0"}).require_auth()


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "http://127.0.0.1:32400/library/sections"),
        ("GET", "http://127.0.0.1:32400/:/scrobble?key=1"),
        ("GET", "http://127.0.0.1:32400/:/rate?key=1"),
        ("GET", "http://169.254.169.254/latest/meta-data"),
        ("GET", "https://evil.example/library/sections"),
        ("DELETE", "http://127.0.0.1:32400/status/sessions/history/all"),
    ],
)
def test_plex_request_policy_blocks_before_network(config, method, url):
    with pytest.raises(ValueError, match="denied"):
        ReadOnlySession(config).request(method, url)


def test_redirect_denied(config, monkeypatch):
    response = requests.Response()
    response.status_code = 302
    seen = []

    def fake(self, method, url, **kwargs):
        seen.append(kwargs)
        return response

    monkeypatch.setattr(requests.Session, "request", fake)
    with pytest.raises(ValueError, match="redirect"):
        ReadOnlySession(config).get(config.plex_url + "/")
    assert seen[0]["allow_redirects"] is False
    assert seen[0]["timeout"] == config.timeout_seconds


@pytest.fixture
def oauth(config, monkeypatch, request):
    config = config.model_copy(
        update={
            "auth_mode": "oauth",
            "oauth_issuer": "https://issuer.example/",
            "oauth_jwks_url": "https://issuer.example/keys",
            "oauth_resource": "https://advisor.example" + getattr(request, "param", "/mcp"),
            "oauth_subject": "owner",
        }
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        jwt.PyJWKClient,
        "get_signing_key_from_jwt",
        lambda self, token: SimpleNamespace(key=key.public_key()),
    )

    def token(**overrides):
        claims = {
            "iss": config.oauth_issuer,
            "aud": config.oauth_resource,
            "sub": "owner",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "scope": "library:read feedback:write",
        }
        claims.update(overrides)
        claims = {name: value for name, value in claims.items() if value is not None}
        return jwt.encode(
            claims, key, algorithm="RS256", headers={"typ": "at+jwt", "kid": "test-key"}
        )

    token.public_key = key.public_key()
    return config, token


@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": "other"},
        {"aud": "https://other.example/mcp"},
        {"iss": "https://evil.example/"},
        {"exp": 1},
        {"scope": "feedback:write"},
        {"nbf": 9999999999},
        {"iat": 9999999999},
        *[{claim: None} for claim in ["exp", "iat", "sub", "iss", "aud"]],
    ],
)
async def test_oauth_rejects_bad_claims(oauth, overrides):
    config, token = oauth
    assert await TokenVerifier(config).verify_token(token(**overrides)) is None


@pytest.mark.parametrize("resource_path", ["/mcp", "/v1/mcp/tunnel_synthetic"])
@pytest.mark.parametrize(
    ("host", "metadata_origin"),
    [
        ("127.0.0.1", "http://127.0.0.1:8765"),
        ("localhost", "http://localhost:8765"),
        ("::1", "http://[::1]:8765"),
        ("0.0.0.0", "https://advisor.example"),
    ],
)
def test_oauth_anonymous_challenge_metadata(oauth, populated, resource_path, host, metadata_origin):
    config, _ = oauth
    config = config.model_copy(
        update={"oauth_resource": "https://advisor.example" + resource_path, "mcp_host": host}
    )
    expected = metadata_origin + "/.well-known/oauth-protected-resource" + resource_path
    with TestClient(create_app(config, populated[0]), base_url="http://localhost:8765") as client:
        for authorization in [None, "Bearer invalid"]:
            headers = {"Accept": "application/json, text/event-stream"}
            if authorization:
                headers["Authorization"] = authorization
            response = client.post(
                "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            )
            assert response.status_code == 401
            challenge = response.headers["www-authenticate"]
            assert 'error="invalid_token"' in challenge
            assert f'resource_metadata="{expected}"' in challenge
            # TestClient resolves the advertised loopback URL in process, without network access.
            discovery = client.get(expected if host != "0.0.0.0" else urlsplit(expected).path)
            assert discovery.status_code == 200
            assert discovery.json()["resource"] == config.oauth_resource
            assert discovery.json()["authorization_servers"] == [config.oauth_issuer]
            assert discovery.json()["scopes_supported"] == ["library:read", "feedback:write"]


@pytest.mark.parametrize("oauth", ["/mcp", "/v1/mcp/tunnel_synthetic"], indirect=True)
def test_oauth_http_write_scope_and_discovery(oauth, populated):
    config, token = oauth
    metadata_path = "/.well-known/oauth-protected-resource" + urlsplit(config.oauth_resource).path
    with TestClient(create_app(config, populated[0]), base_url="http://localhost:8765") as client:
        discovery = client.get(metadata_path)
        assert discovery.status_code == 200
        assert discovery.json()["resource"] == config.oauth_resource
        assert "feedback:write" in discovery.json()["scopes_supported"]
        tool_headers = {
            "Authorization": "Bearer " + token(),
            "Accept": "application/json, text/event-stream",
        }
        listed = client.post(
            "/mcp", headers=tool_headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
        )
        tools = listed.json()["result"]["tools"]
        write_tool = next(t for t in tools if t["name"] == "record_media_feedback")
        assert "feedback:write" in write_tool["securitySchemes"][0]["scopes"]
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "record_media_feedback",
                "arguments": {
                    "feedback": {
                        "media_id": "server-A:1",
                        "comments": "Explicit opinion",
                        "authorized": True,
                    }
                },
            },
        }
        headers = {
            "Authorization": "Bearer " + token(scope="library:read"),
            "Accept": "application/json, text/event-stream",
        }
        response = client.post("/mcp", json=body, headers=headers)
        assert response.json()["result"]["isError"]
        challenge = response.json()["result"]["_meta"]["mcp/www_authenticate"][0]
        assert 'error="insufficient_scope"' in challenge
        assert 'scope="feedback:write"' in challenge
        metadata_url = "http://127.0.0.1:8765" + metadata_path
        assert f'resource_metadata="{metadata_url}"' in challenge
        assert client.get(metadata_url).json()["resource"] == config.oauth_resource
        headers["Authorization"] = "Bearer " + token()
        assert not client.post("/mcp", json=body, headers=headers).json()["result"].get("isError")
        headers["Authorization"] = "Bearer " + token(sub="someone-else")
        assert client.post("/mcp", json=body, headers=headers).status_code == 401


def test_request_rate_limit(config, populated):
    config = config.model_copy(update={"requests_per_minute": 2})
    with TestClient(create_app(config, populated[0]), base_url="http://localhost:8765") as client:
        assert client.post("/mcp").status_code == 401
        assert client.post("/mcp").status_code == 401
        assert client.post("/mcp").status_code == 429


@pytest.mark.parametrize("use_oauth", [False, True])
def test_anonymous_flood_cannot_consume_owner_quota(config, oauth, populated, use_oauth):
    if use_oauth:
        config, token = oauth
        owner_token = token()
        invalid_tokens = [token(sub=f"other-{i}") for i in range(6)]
    else:
        owner_token = config.local_bearer_token.get_secret_value()
        invalid_tokens = [f"untrusted-{i}" for i in range(6)]
    config = config.model_copy(update={"requests_per_minute": 2})
    with TestClient(create_app(config, populated[0]), base_url="http://localhost:8765") as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        for i, invalid in enumerate(invalid_tokens):
            assert client.post(
                "/mcp", headers={"Authorization": "Bearer " + invalid}
            ).status_code == (401 if i < 2 else 429)
        headers = {
            "Authorization": "Bearer " + owner_token,
            "Accept": "application/json, text/event-stream",
        }
        for _ in range(2):
            assert client.post("/mcp", json=body, headers=headers).status_code == 200
        assert client.post("/mcp", json=body, headers=headers).status_code == 429


async def test_quota_state_is_fixed_and_expires(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("plex_advisor.security.time.monotonic", lambda: now[0])
    reached = []

    async def downstream(scope, receive, send):
        reached.append(True)

    async def unused_receive():
        raise AssertionError("Request body should not be read")

    async def discard(message):
        pass

    limits = RequestLimits(downstream, 2)
    for i in range(100):
        await limits(
            {"type": "http", "headers": [(b"authorization", f"Bearer {i}".encode())]},
            unused_receive,
            discard,
        )
    assert len(reached) == 2
    assert len(limits.anonymous_arrivals) == 2
    assert len(limits.owner_arrivals) == 0
    now[0] += 61
    await limits({"type": "http"}, unused_receive, discard)
    assert len(reached) == 3
    assert len(limits.anonymous_arrivals) == 1


@pytest.mark.parametrize("algorithm", ["RS256", "HS256", "none"])
async def test_oauth_rejects_bad_signature_and_algorithms(oauth, algorithm):
    config, token = oauth
    claims = jwt.decode(token(), options={"verify_signature": False})
    signing_key = {
        "RS256": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "HS256": "synthetic-test-secret-" + "x" * 32,
        "none": None,
    }[algorithm]
    forged = jwt.encode(claims, signing_key, algorithm=algorithm, headers={"typ": "at+jwt"})
    verifier = TokenVerifier(config)
    assert await verifier.verify_token(forged) is None
    assert verifier.jwks._retry_after == 0
    assert await verifier.verify_token(token()) is not None


@pytest.mark.parametrize("chunked", [False, True])
def test_authenticated_body_cap_remains_64kib(config, populated, chunked):
    headers = {
        "Authorization": "Bearer " + config.local_bearer_token.get_secret_value(),
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    exact = body + b" " * (65536 - len(body))
    oversized = exact + b" "
    with TestClient(create_app(config, populated[0]), base_url="http://localhost:8765") as client:
        assert client.post("/mcp", content=exact, headers=headers).status_code == 200
        content = iter([oversized[:32768], oversized[32768:]]) if chunked else oversized
        assert client.post("/mcp", content=content, headers=headers).status_code == 413


async def _wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_verification_admission_and_queued_cancellation(oauth, monkeypatch):
    config, _ = oauth
    verifier = TokenVerifier(config)
    executor = ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    blocker_started = threading.Event()
    submitted = []
    completed = []

    def occupy_executor():
        blocker_started.set()
        release.wait(5)

    executor.submit(occupy_executor)

    def verify(value):
        completed.append(value)
        return None

    def submit_verification(function, *args):
        submitted.append(args)
        return executor.submit(function, *args)

    monkeypatch.setattr(verifier, "_verify_jwt", verify)
    monkeypatch.setattr(
        security, "_VERIFICATION_EXECUTOR", SimpleNamespace(submit=submit_verification)
    )
    tasks = []
    try:
        await _wait_until(blocker_started.is_set)
        tasks = [asyncio.create_task(verifier.verify_token(str(i))) for i in range(12)]
        await _wait_until(lambda: len(submitted) == 4 and sum(t.done() for t in tasks) == 8)
        assert await TokenVerifier(config).verify_token("another-app-instance") is None
        assert len(submitted) == 4  # All verifier instances share the process admission bound.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert not completed  # All four queued verifications were cancelled before starting.
        recovered = asyncio.create_task(verifier.verify_token("recovered"))
        tasks.append(recovered)
        await _wait_until(lambda: len(submitted) == 5)
        release.set()
        await recovered
        assert completed == ["recovered"]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        executor.shutdown(wait=True)


async def test_verification_running_cancellation_keeps_slot(oauth, monkeypatch):
    config, _ = oauth
    verifier = TokenVerifier(config)
    verifier._verification_slots = threading.BoundedSemaphore(1)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def verify(value):
        started.set()
        release.wait(5)
        finished.set()
        return None

    monkeypatch.setattr(verifier, "_verify_jwt", verify)
    task = asyncio.create_task(verifier.verify_token("running"))
    try:
        await _wait_until(started.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await verifier.verify_token("excess") is None
        assert not finished.is_set()
        release.set()
        await _wait_until(finished.is_set)
        # Worker return and its completion callback can race the event-loop wakeup.
        await _wait_until(lambda: verifier._verification_slots.acquire(blocking=False))
        verifier._verification_slots.release()
        started.clear()
        await verifier.verify_token("recovered")
        assert started.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_executor_submission_failure_releases_admission(oauth, monkeypatch):
    config, _ = oauth
    verifier = TokenVerifier(config)

    def fail(*args):
        raise RuntimeError("Synthetic executor shutdown")

    monkeypatch.setattr(security, "_VERIFICATION_EXECUTOR", SimpleNamespace(submit=fail))
    for _ in range(8):
        with pytest.raises(RuntimeError, match="Synthetic"):
            await verifier.verify_token("candidate")


async def test_executor_shutdown_cancels_queued_work_without_stranding_slot(oauth, monkeypatch):
    config, _ = oauth
    verifier = TokenVerifier(config)
    verifier._verification_slots = threading.BoundedSemaphore(1)
    executor = ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    started = threading.Event()
    queued = []

    def block():
        started.set()
        release.wait(5)

    executor.submit(block)

    def submit(function, *args):
        pending = executor.submit(function, *args)
        queued.append(pending)
        return pending

    monkeypatch.setattr(security, "_VERIFICATION_EXECUTOR", SimpleNamespace(submit=submit))
    task = asyncio.create_task(verifier.verify_token("queued"))
    try:
        await _wait_until(lambda: started.is_set() and len(queued) == 1)
        executor.shutdown(wait=False, cancel_futures=True)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert verifier._verification_slots.acquire(blocking=False)
        verifier._verification_slots.release()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        executor.shutdown(wait=True)


def test_jwks_failure_backoff_is_shared_and_recovers(oauth, monkeypatch):
    config, token = oauth
    verifier = TokenVerifier(config)
    # The fixture mocks lookup; restore real lookup to exercise fetch failure and its lock.
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt", _REAL_JWKS_LOOKUP)
    now = [100.0]
    monkeypatch.setattr("plex_advisor.security.time.monotonic", lambda: now[0])
    calls = []

    def failing_fetch(self):
        calls.append(True)
        raise jwt.PyJWKClientConnectionError("Synthetic unavailable issuer")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", failing_fetch)
    candidate = token()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(verifier._verify_jwt, [candidate] * 12)) == [None] * 12
    assert len(calls) == 1
    now[0] += 31
    assert verifier._verify_jwt(candidate) is None
    assert len(calls) == 2
    now[0] += 31

    def successful_fetch(self):
        calls.append(True)
        return {
            "keys": [
                {
                    **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(token.public_key)),
                    "kid": "test-key",
                }
            ]
        }

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", successful_fetch)
    assert verifier._verify_jwt(candidate) is not None
    assert verifier.jwks._retry_after == 0


def test_jwks_backoff_preserves_cached_keys_and_does_not_slide(oauth, monkeypatch):
    config, token = oauth
    verifier = TokenVerifier(config)
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt", _REAL_JWKS_LOOKUP)
    now = [100.0]
    monkeypatch.setattr("plex_advisor.security.time.monotonic", lambda: now[0])
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(token.public_key))
    jwks = json.dumps({"keys": [{**public, "kid": "test-key"}]}).encode()
    attempts = []
    unavailable = [False]

    def open_stub(self, request, timeout):
        attempts.append(True)
        if unavailable[0]:
            raise URLError("Synthetic unavailable issuer")
        return BytesIO(jwks)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", open_stub)
    owner = token()
    assert verifier._verify_jwt(owner) is not None
    assert len(attempts) == 1
    unavailable[0] = True
    now[0] += 31  # Past PyJWT's successful-fetch cooldown; cached keys are still fresh.
    unknown = jwt.encode(
        jwt.decode(owner, options={"verify_signature": False}),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        algorithm="RS256",
        headers={"typ": "at+jwt", "kid": "unknown"},
    )
    assert verifier._verify_jwt(unknown) is None
    retry_after = verifier.jwks._retry_after
    assert len(attempts) == 2
    now[0] += 10
    assert verifier._verify_jwt(unknown) is None
    assert verifier.jwks._retry_after == retry_after
    assert len(attempts) == 2
    assert verifier._verify_jwt(owner) is not None
    unavailable[0] = False
    now[0] = retry_after + 1
    assert verifier._verify_jwt(unknown) is None  # Fetch works; unknown keys still fail closed.
    assert verifier.jwks._retry_after == 0
    assert len(attempts) == 3
    assert verifier._verify_jwt(owner) is not None


_REAL_JWKS_LOOKUP = jwt.PyJWKClient.get_signing_key_from_jwt
