import pytest
from pydantic import ValidationError

from plex_advisor.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "http://user:pass@127.0.0.1:32400",
        "http://evil.example:32400",
        "http://127.0.0.1:32400/?token=x",
    ],
)
def test_invalid_plex_urls(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, plex_url=url)


def test_timezone_and_secrets(config):
    config = Settings(**{**config.model_dump(), "timezone": "America/New_York"}, _env_file=None)
    assert "PRIVATE-DO-NOT-LEAK" not in repr(config)
    config.require_plex()
    config.require_auth()
