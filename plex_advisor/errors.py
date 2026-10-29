"""Stable, credential-free errors for changed external contracts."""


class PlexCompatibilityError(ValueError):
    """Plex/PlexAPI returned an unsupported or inconsistent response shape."""


class CacheCompatibilityError(ValueError):
    """The local cache is newer, structurally incompatible, or damaged."""


PLEX_COMPATIBILITY_HELP = (
    "Plex API compatibility check failed. Cached data was preserved. "
    "Check Plex/PlexAPI versions and enrollment, update Plex Bridge, then retry a full sync. "
    "Do not reset the cache or share raw responses/tokens."
)
CACHE_COMPATIBILITY_HELP = (
    "The cache is incompatible, damaged, or unavailable. Stop the service and preserve a "
    "consistent backup; check the application version, disk, permissions, and cache integrity. "
    "Restore a matching backup or upgrade the application. Do not delete the database."
)
