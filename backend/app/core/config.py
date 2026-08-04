from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Novel Agent Studio"
    app_version: str = Field("2.2.10", alias="NAS_APP_VERSION")
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
    local_api_token: str = Field("", alias="NAS_LOCAL_API_TOKEN")
    log_dir: str = Field("./data/logs", alias="NAS_LOG_DIR")
    log_retention_days: int = Field(14, ge=1, le=365, alias="NAS_LOG_RETENTION_DAYS")
    workflow_delta_retention_days: int = Field(
        14, ge=1, le=365, alias="NAS_WORKFLOW_DELTA_RETENTION_DAYS"
    )
    workflow_event_retention_days: int = Field(
        90, ge=7, le=3_650, alias="NAS_WORKFLOW_EVENT_RETENTION_DAYS"
    )
    context_builds_per_project: int = Field(
        30, ge=5, le=500, alias="NAS_CONTEXT_BUILDS_PER_PROJECT"
    )
    storage_auto_gc: bool = Field(True, alias="NAS_STORAGE_AUTO_GC")
    runtime_maintenance_interval_seconds: float = Field(
        6 * 60 * 60,
        ge=60,
        le=7 * 24 * 60 * 60,
        alias="NAS_RUNTIME_MAINTENANCE_INTERVAL_SECONDS",
    )
    max_backup_bytes: int = Field(
        256 * 1024 * 1024, ge=1024, alias="NAS_MAX_BACKUP_BYTES"
    )
    max_backup_uncompressed_bytes: int = Field(
        1024 * 1024 * 1024,
        ge=1024,
        alias="NAS_MAX_BACKUP_UNCOMPRESSED_BYTES",
    )
    max_import_bytes: int = Field(
        10 * 1024 * 1024, ge=1024, alias="NAS_MAX_IMPORT_BYTES"
    )
    max_import_text_chars: int = Field(
        5_000_000, ge=1000, alias="NAS_MAX_IMPORT_TEXT_CHARS"
    )
    import_parse_timeout_seconds: float = Field(
        20.0, ge=1.0, le=300.0, alias="NAS_IMPORT_PARSE_TIMEOUT_SECONDS"
    )
    docx_max_entries: int = Field(2_048, ge=10, alias="NAS_DOCX_MAX_ENTRIES")
    docx_max_expanded_bytes: int = Field(
        64 * 1024 * 1024, ge=1024, alias="NAS_DOCX_MAX_EXPANDED_BYTES"
    )
    docx_max_member_bytes: int = Field(
        32 * 1024 * 1024, ge=1024, alias="NAS_DOCX_MAX_MEMBER_BYTES"
    )
    docx_max_compression_ratio: float = Field(
        200.0, ge=1.0, alias="NAS_DOCX_MAX_COMPRESSION_RATIO"
    )
    pdf_max_pages: int = Field(500, ge=1, alias="NAS_PDF_MAX_PAGES")
    workflow_stream_flush_interval_ms: int = Field(
        350, ge=50, le=5_000, alias="NAS_WORKFLOW_STREAM_FLUSH_INTERVAL_MS"
    )
    workflow_stream_flush_bytes: int = Field(
        8 * 1024, ge=1024, le=1024 * 1024, alias="NAS_WORKFLOW_STREAM_FLUSH_BYTES"
    )
    database_write_queue_size: int = Field(
        1024, ge=16, le=65_536, alias="NAS_DATABASE_WRITE_QUEUE_SIZE"
    )
    database_busy_retries: int = Field(
        5, ge=0, le=20, alias="NAS_DATABASE_BUSY_RETRIES"
    )
    sqlite_busy_timeout_ms: int = Field(
        5_000, ge=100, le=60_000, alias="NAS_SQLITE_BUSY_TIMEOUT_MS"
    )
    sqlite_wal_autocheckpoint_pages: int = Field(
        1_000, ge=100, le=100_000, alias="NAS_SQLITE_WAL_AUTOCHECKPOINT_PAGES"
    )
    workflow_max_parallel_nodes_global: int = Field(
        8, ge=1, le=128, alias="NAS_WORKFLOW_MAX_PARALLEL_NODES_GLOBAL"
    )
    workflow_max_parallel_nodes_per_run: int = Field(
        4, ge=1, le=64, alias="NAS_WORKFLOW_MAX_PARALLEL_NODES_PER_RUN"
    )
    workflow_max_parallel_nodes_per_provider: int = Field(
        4, ge=1, le=64, alias="NAS_WORKFLOW_MAX_PARALLEL_NODES_PER_PROVIDER"
    )
    workflow_max_parallel_context_builds: int = Field(
        3, ge=1, le=32, alias="NAS_WORKFLOW_MAX_PARALLEL_CONTEXT_BUILDS"
    )
    workflow_max_parallel_database_tasks: int = Field(
        1, ge=1, le=16, alias="NAS_WORKFLOW_MAX_PARALLEL_DATABASE_TASKS"
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
