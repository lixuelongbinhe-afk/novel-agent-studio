from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator


ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,119}")
SAFE_SLUG = re.compile(r"[a-z][a-z0-9_-]{1,79}")
BLOCKED_STATIC_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "host",
    "content-length",
}
SECRET_QUERY_WORDS = ("api_key", "apikey", "token", "secret", "password", "credential")


class CredentialReferenceCreate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=120)
    env_var_name: str = Field(min_length=1, max_length=120)

    @field_validator("env_var_name")
    @classmethod
    def validate_env_name(cls, value: str) -> str:
        if not ENV_NAME.fullmatch(value):
            raise ValueError("env_var_name must be an environment variable name")
        return value


class CredentialReferenceUpdate(CredentialReferenceCreate):
    expected_revision: int = Field(ge=1)


class CredentialReferenceRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    env_var_name: str
    revision: int
    deleted_at: datetime | None


class GenericAuthConfig(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal[
        "none",
        "bearer",
        "api_key_header",
        "custom_header",
        "query",
        "basic",
    ] = "none"
    header_name: str | None = Field(default=None, min_length=1, max_length=120)
    query_name: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, max_length=200)
    prefix: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def validate_for_type(self) -> "GenericAuthConfig":
        if self.type in {"api_key_header", "custom_header"} and not self.header_name:
            raise ValueError("header_name is required for header authentication")
        if self.type == "query" and not self.query_name:
            raise ValueError("query_name is required for query authentication")
        if self.type == "basic" and not self.username:
            raise ValueError("username is required for basic authentication")
        if self.header_name and self.header_name.lower() in {"cookie", "set-cookie", "host"}:
            raise ValueError("Cookie and Host authentication headers are not supported")
        return self


class GenericHttpAdapterFields(BaseModel):
    model_config = {"extra": "forbid"}

    credential_reference_id: int | None = Field(default=None, ge=1)
    method: Literal["GET", "POST"] = "POST"
    endpoint: str = Field(default="/", min_length=1, max_length=500)
    content_type: str = Field(default="application/json", min_length=1, max_length=120)
    response_mode: Literal["json", "raw_text"] = "json"
    stream_format: Literal["sse", "ndjson", "chunked_json", "raw_text"] = "sse"
    security_mode: Literal["public_only", "local_private"] = "public_only"
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    request_template: Any = Field(default_factory=dict)
    parameter_mapping: dict[str, str] = Field(default_factory=dict)
    response_mapping: dict[str, Any] = Field(
        default_factory=lambda: {"text": "$.text", "model": "$.model"}
    )
    stream_mapping: dict[str, Any] = Field(
        default_factory=lambda: {"text_delta": "$.delta", "done": "$.done"}
    )
    error_mapping: dict[str, Any] = Field(
        default_factory=lambda: {"message": "$.error.message", "code": "$.error.code"}
    )
    auth: GenericAuthConfig = Field(default_factory=GenericAuthConfig)
    capability_defaults: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False

    @field_validator("endpoint")
    @classmethod
    def validate_relative_endpoint(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "://" in value:
            raise ValueError("endpoint must be a relative path beginning with one slash")
        return value

    @field_validator("headers")
    @classmethod
    def validate_static_headers(cls, value: dict[str, str]) -> dict[str, str]:
        for name, header_value in value.items():
            if name.lower() in BLOCKED_STATIC_HEADERS:
                raise ValueError(f"{name} must be configured through bound authentication")
            lowered_value = header_value.lower()
            if lowered_value.startswith(("bearer ", "basic ", "sk-")):
                raise ValueError("static header values must not contain credentials")
        return value

    @field_validator("query")
    @classmethod
    def validate_static_query(cls, value: dict[str, Any]) -> dict[str, Any]:
        for name, query_value in value.items():
            lowered = name.lower()
            if any(word in lowered for word in SECRET_QUERY_WORDS) and query_value not in (None, ""):
                raise ValueError(f"{name} must be configured through bound authentication")
        return value

    @model_validator(mode="after")
    def reject_static_secret_material(self) -> "GenericHttpAdapterFields":
        from app.services.safe_json import find_secret_material

        findings = find_secret_material(
            {
                "query": self.query,
                "headers": self.headers,
                "request_template": self.request_template,
            }
        )
        if findings:
            raise ValueError(
                "Static credentials are forbidden; use a bound CredentialReference"
            )
        return self


class GenericHttpAdapterCreate(GenericHttpAdapterFields):
    provider_account_id: int = Field(ge=1)


class GenericHttpAdapterSetupCreate(GenericHttpAdapterFields):
    provider_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("base_url is invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an http or https URL with a hostname")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain query parameters or fragments")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("base_url port is invalid")
        return value


class GenericHttpAdapterUpdate(GenericHttpAdapterFields):
    expected_revision: int = Field(ge=1)


class GenericHttpAdapterRead(GenericHttpAdapterFields):
    id: int
    provider_account_id: int
    credential_reference_name: str | None = None
    approved_origin: str | None
    approval_current: bool
    test_current: bool
    last_tested_at: datetime | None
    revision: int
    deleted_at: datetime | None


class GenericAdapterTestRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request: dict[str, Any]


class GenericAdapterTestRead(BaseModel):
    ok: bool
    redacted_request: dict[str, Any]
    response: dict[str, Any]
    error: dict[str, Any] | None = None


class OriginApprovalRequest(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: int = Field(ge=1)


class GenericAdapterManifestConfig(GenericHttpAdapterFields):
    credential_reference_id: None = None
    enabled: bool = False


class GenericAdapterManifest(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["novel-agent-studio.generic-json-http"] = (
        "novel-agent-studio.generic-json-http"
    )
    name: str = Field(min_length=1, max_length=120)
    provider_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    config: GenericAdapterManifestConfig


class ManifestImportRead(BaseModel):
    provider_id: int
    adapter: GenericHttpAdapterRead
