from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PA_", env_file=".env", extra="ignore")
    plex_url: str = "http://127.0.0.1:32400"
    plex_allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1"])
    plex_token: SecretStr = SecretStr("")
    plex_account_id: int | None = Field(default=None, gt=0)
    plex_username: str = ""
    plex_server_id: str = ""
    plex_history_account_id: int | None = Field(default=None, gt=0)
    plex_history_account_name: str = ""
    history_binding_confirmed: bool = False
    library_sections: list[int] = Field(default_factory=list)
    database_path: Path = Path("data/plex-advisor.sqlite")
    sync_interval_seconds: int = Field(default=14400, ge=60)
    timeout_seconds: int = Field(default=20, ge=1, le=120)
    page_size: int = Field(default=200, ge=1, le=1000)
    timezone: str = "UTC"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8765, ge=1024, le=65535)
    mcp_allowed_hosts: list[str] = Field(default_factory=list)
    auth_mode: Literal["local", "oauth"] = "local"
    local_bearer_token: SecretStr = SecretStr("")
    oauth_issuer: str = ""
    oauth_jwks_url: str = ""
    oauth_resource: str = ""
    oauth_subject: str = ""
    requests_per_minute: int = Field(default=120, ge=1, le=10000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        url = urlsplit(self.plex_url)
        if (
            url.scheme not in {"http", "https"}
            or url.hostname not in self.plex_allowed_hosts
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Plex URL must be a configured allowed origin without credentials")
        if any(x < 1 for x in self.library_sections):
            raise ValueError("Library section IDs must be positive")
        ZoneInfo(self.timezone)
        return self

    def require_plex(self, *, identity: bool = True) -> None:
        if not self.plex_token.get_secret_value().strip():
            raise ValueError("Configure PA_PLEX_TOKEN privately in .env")
        if identity and (
            not self.plex_account_id or not self.plex_username or not self.plex_server_id
        ):
            raise ValueError(
                "Configure Plex account ID, exact username and server ID after discover"
            )

    def require_auth(self) -> None:
        if self.auth_mode == "local":
            if self.mcp_host not in {"127.0.0.1", "::1", "localhost"}:
                raise ValueError("Local bearer mode is restricted to loopback")
            secret = self.local_bearer_token.get_secret_value()
            if len(secret) < 32 or secret.startswith("replace-"):
                raise ValueError("Configure a random local bearer secret of at least 32 characters")
        else:
            for value in [self.oauth_issuer, self.oauth_jwks_url, self.oauth_resource]:
                url = urlsplit(value)
                if (
                    url.scheme != "https"
                    or not url.hostname
                    or url.username
                    or url.password
                    or url.query
                    or url.fragment
                ):
                    raise ValueError("OAuth issuer, JWKS, and resource must be clean HTTPS URLs")
            if not self.oauth_subject:
                raise ValueError("An exact authorized OAuth subject is required")
