from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Novel Agent Studio"
    app_version: str = Field("2.1.2", alias="NAS_APP_VERSION")
    environment: str = Field("development", alias="NAS_ENV")
    database_url: str = Field("sqlite:///./data/novel_agent_studio.db", alias="NAS_DATABASE_URL")
    cors_origins: str = Field(
        "http://127.0.0.1:5173,http://localhost:5173", alias="NAS_CORS_ORIGINS"
    )
    mock_delay_ms: int = Field(40, alias="NAS_MOCK_DELAY_MS")
    gateway_connect_timeout: float = Field(10.0, alias="NAS_GATEWAY_CONNECT_TIMEOUT")
    gateway_read_timeout: float = Field(120.0, alias="NAS_GATEWAY_READ_TIMEOUT")
    gateway_write_timeout: float = Field(30.0, alias="NAS_GATEWAY_WRITE_TIMEOUT")
    gateway_pool_timeout: float = Field(10.0, alias="NAS_GATEWAY_POOL_TIMEOUT")
    gateway_max_connections: int = Field(50, alias="NAS_GATEWAY_MAX_CONNECTIONS")
    gateway_max_keepalive: int = Field(20, alias="NAS_GATEWAY_MAX_KEEPALIVE")
    gateway_max_response_bytes: int = Field(16 * 1024 * 1024, alias="NAS_GATEWAY_MAX_RESPONSE_BYTES")
    gateway_error_text_limit: int = Field(2000, alias="NAS_GATEWAY_ERROR_TEXT_LIMIT")
    allowed_hosts: str = Field(
        "127.0.0.1,localhost,testserver", alias="NAS_ALLOWED_HOSTS"
    )
    frontend_dist: str = Field("", alias="NAS_FRONTEND_DIST")
    log_dir: str = Field("./data/logs", alias="NAS_LOG_DIR")
    log_retention_days: int = Field(14, ge=1, le=365, alias="NAS_LOG_RETENTION_DAYS")
    max_backup_bytes: int = Field(
        256 * 1024 * 1024, ge=1024, alias="NAS_MAX_BACKUP_BYTES"
    )
    max_backup_uncompressed_bytes: int = Field(
        1024 * 1024 * 1024,
        ge=1024,
        alias="NAS_MAX_BACKUP_UNCOMPRESSED_BYTES",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def log_path(self) -> Path:
        return Path(self.log_dir).expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
