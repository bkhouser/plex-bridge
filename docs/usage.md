# Using Plex Bridge: examples and everyday workflows

Plex Bridge connects your personal Plex library to ChatGPT, Codex, or another
compatible MCP assistant. You ask questions in conversation; the bridge supplies
cached library facts, and the assistant turns them into answers and suggestions.
It is an unofficial, self-hosted integration, not a standalone chat or web app.

The examples below are requests you can copy or adapt. They are not demonstrations
of a particular person's library: named titles may be absent, ratings may be
missing, and your results will differ.

## Before your first conversation

Complete the [installation steps](../README.md#quick-start),
[profile enrollment](configuration.md), and either
[local client setup](clients.md) or [ChatGPT setup](chatgpt_setup.md).
Keep the server running and enable Plex Bridge in your assistant.

Start with:

> What can I do with Plex Bridge? Show me a few examples, then check whether my
> library information is up to date.

The built-in `get_usage_guide` explains common tasks. `get_plex_status` reports the
last sync and capability warnings. Asking for status does not trigger a new sync.
The operator can refresh with `plex-advisor sync`; normal background refresh is
every four hours by default. For older retrievable history, use `sync --full`.

## Find something for tonight

> Find unwatched science-fiction movies from 2010 onward in my library, at most
> 100 minutes long. Show the title, year, runtime and my personal rating if present.

The assistant can search by title, genre, year, people, runtime, watched state,
and personal rating. “Unwatched” means not currently marked watched in Plex;
it does not prove you have never seen the film.

Continue naturally:

> Narrow that to at most 95 minutes. Keep the other requirements.

Or ask for a different combination:

> Find movies that are both Action and Thriller, with at least four recorded
> plays in Plex. Keep the runtime at most 120 minutes.

If there are too few matches, the assistant should say so and let you decide
which requirement to relax. Large result sets are paginated.

## Get suggestions based on your own tastes

> Suggest three movies already in my library, at most 120 minutes, based on what
> I've rated highly. Explain which facts support each suggestion.

The assistant can inspect your taste summary, retrieve factual movie candidates,
and use explicit ratings and saved opinions to explain fit. Viewing counts are
weaker evidence: replaying a movie does not necessarily mean liking it. The
bridge does not itself determine whether a film is well written or right for
your mood; that judgment belongs to the assistant.

> Give me three alternatives, excluding those suggestions, with the same constraints.

The candidate helper supports exclusions and varied selection. It is movie-only;
ordinary library search also supports shows, seasons and episodes.

## Check titles, editions and ratings

> Do I have The Thing from 1982 or the 2011 version? Show any matching editions.

Title and year help distinguish remakes. The assistant should resolve ambiguity
and use the exact returned IDs for follow-up details. A stale cache miss does
not establish that a title is currently absent from your server.

> What is my personal rating for Fargo (1996)? Compare it with the public ratings
> Plex provides, and identify their sources.

Personal, critic and audience ratings are separate evidence. Missing ratings
remain unknown; they are not interpreted as zero. An unknown public-rating
provider is not automatically labeled a critic score.

## Explore your library and TV viewing

> Give me an overview of my movie library, including genres, decades and watched state.

Statistics distinguish physical copies from unique titles where canonical IDs
allow it. Genre counts overlap because one film can have several genres.

> Find a show by title, then list its season-two episodes and their watched state.

Replace “a show” with a title in your collection. The assistant first identifies
the show, then searches its episodes using the exact show ID and season number.
Missing aggregate season state should remain unknown.

> Show my ten most recent viewing events, with dates in my timezone.

> For that show, show the viewing events from last month.

History uses the enrolled profile and selected libraries. Date ranges need an
unambiguous timezone, with an exclusive end boundary. History can be incomplete:
marking something watched need not create a playback event, and an old event
does not establish current availability or watched state.

## Remember, inspect and remove an opinion

> Remember that I liked Fargo's dialogue but found the pacing slow.

This explicitly requests saving local feedback. The assistant must identify the
intended film and resolve ambiguity before writing. It should preserve your
stated opinion without inventing a numeric rating. Casual comments do not grant
permission to save feedback; merely reading this example grants no permission.

> Show the opinions you've saved about that film.

> Delete the opinion about its pacing that you just showed me.

Deletion targets an inspected, specific record. If several records could match,
the assistant should clarify first. Keep client confirmation enabled for writes.
Remote writes require the `feedback:write` scope. None of these actions changes
your Plex ratings or other Plex data.

## What to expect when something is unavailable

| Situation | Expected behavior |
| --- | --- |
| Plex is temporarily offline | Use retained cached data with a freshness warning. |
| A title or rating is missing | Say what is missing; do not invent an answer. |
| History access or profile binding is unverified | Report the limitation rather than use another person's history. |
| Plex responses or the cache become incompatible | Return a redacted error with recovery guidance and preserve existing data. |

Plex Bridge cannot play or download media, administer Plex, change Plex ratings,
or switch to another person's profile. It does not search Plex's public streaming
catalog. See [limitations](limitations.md), [privacy](privacy.md),
[troubleshooting](troubleshooting.md), and [compatibility recovery](compatibility.md).

## Help inside your assistant

Ask “Show me the Plex Bridge usage guide” to use `get_usage_guide`. Resource-aware
clients can also read `plex-bridge://guides/getting-started`. After a server
upgrade, refresh the client's tool list or reconnect if the help tool is missing.

For tool names, parameters and bounds, see the [MCP tool reference](tools.md).
