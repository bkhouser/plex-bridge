# Connect Plex Bridge to ChatGPT

Plex Bridge requires a running instance for your profile. It is unofficial and is
not a hosted service or approved directory plugin. Features/workspace permissions
vary: follow [OpenAI's connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt)
and [authentication guide](https://developers.openai.com/plugins/build/auth).

## Verify local data first

Complete [enrollment](configuration.md), compare samples with Plex, sync, and run
the local MCP smoke test. Keep the application loopback-bound. Use an unused port
consistently in configuration, the client and the tunnel/proxy.

## Configure OAuth

Use an established provider and a client-supported authorization-code/PKCE flow.
This application is a resource server, not an authorization server. Configure
`PA_AUTH_MODE=oauth`, HTTPS issuer/JWKS/resource URLs, and one exact allowed
`PA_OAUTH_SUBJECT`. Require RS256 RFC 9068 access tokens with `typ=at+jwt`, matching
issuer/audience, expiry/issued-at, and `library:read`. Grant `feedback:write` only
for explicitly requested local feedback writes.

The audience must match the canonical MCP resource exactly, including its path.
Use the callback URI displayed by your client and its supported registration and
token-endpoint authentication method. Do not guess audience, callback or subject.
ID tokens, opaque tokens and other algorithms are unsupported. Provider dashboard
sign-in does not establish the allowed subject. Replace provider development
credentials before public deployment. Enter secrets directly in private provider
and client fields, never in chat.

## Choose transport

**Secure MCP Tunnel:** use the assigned tunnel in your OpenAI organization and the
[official guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
Download from [official releases](https://github.com/openai/tunnel-client/releases),
verify its checksum, and generate the HTTP profile using that release's help.
Forward the main MCP channel to the loopback `/mcp` endpoint. Do not infer the
external MCP hostname from the control-plane hostname. Transport authorization
does not replace application OAuth.

The optional Windows helpers expect version 0.0.16 at
`data/tunnel-client/v0.0.16/tunnel-client.exe` and a JSON-formatted profile (a valid
YAML subset) at `data/tunnel-client/profiles/plex-advisor.yaml`. The profile must
reference process environment variable `PA_TUNNEL_API_KEY`, not a literal secret.
Privately save a dedicated, minimally scoped runtime key with
`scripts/configure_tunnel_key.ps1`; DPAPI binds it to your Windows user.
`scripts/start_tunnel.ps1 -Doctor` and `scripts/start_tunnel.ps1` first verify that
the target rejects anonymous requests and advertises the configured OAuth issuer
and resource. Review compatibility before upgrading the client/helper version.

**HTTPS proxy:** use [deployment](deployment.md) and the example Caddyfile with
your own domain, TLS and timeout/rate limits. Add the exact external Host value to
`PA_MCP_ALLOWED_HOSTS`. Keep Authorization intact and restrict backend reachability.

## Install and verify

In ChatGPT plugin settings, add a custom MCP server named **Plex Bridge**, select
the tunnel or HTTPS endpoint, and configure OAuth. Sign in privately, inspect
permissions and connect. Keep feedback-write confirmation enabled. Test actual
authenticated calls using [verification](verification.md). Installation/discovery
alone does not prove calls work. Refresh tools after schema changes.

`plugin/plex-advisor/mcp.json` is a localhost template without credentials.
Uploading it does not provision hosting or configure OAuth. Directory submission
is separate; an unofficial integration may not meet eligibility requirements.
