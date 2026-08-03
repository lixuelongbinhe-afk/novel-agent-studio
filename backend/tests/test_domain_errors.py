import json
from pathlib import Path

from starlette.requests import Request
from starlette.types import Scope

from app.main import handle_domain_error
from app.services.errors import ConflictError, DomainError, ErrorKind, NotFoundError


def test_domain_error_keeps_transport_independent_detail_and_context() -> None:
    error = ConflictError("内容已更新", context={"resource": "chapter"})

    assert isinstance(error, DomainError)
    assert str(error) == "内容已更新"
    assert error.detail == "内容已更新"
    assert error.context == {"resource": "chapter"}
    assert error.kind is ErrorKind.CONFLICT


def test_domain_error_context_is_not_shared_between_instances() -> None:
    first = NotFoundError("章节不存在")
    second = NotFoundError("场景不存在")

    first.context["resource"] = "chapter"

    assert second.context == {}
    assert second.kind is ErrorKind.NOT_FOUND


async def test_api_boundary_translates_domain_error_without_changing_detail() -> None:
    scope: Scope = {"type": "http"}
    response = await handle_domain_error(
        Request(scope),
        ConflictError("内容已更新", context={"resource": "chapter"}),
    )

    assert response.status_code == 409
    assert json.loads(bytes(response.body)) == {
        "detail": "内容已更新",
        "kind": "conflict",
        "resource": "chapter",
    }


async def test_api_boundary_preserves_structured_revision_detail() -> None:
    scope: Scope = {"type": "http"}
    detail = {
        "message": "Record revision conflict",
        "current_revision": 2,
        "record_id": 7,
    }

    response = await handle_domain_error(Request(scope), ConflictError(detail))

    assert response.status_code == 409
    assert json.loads(bytes(response.body)) == {
        "detail": detail,
        "kind": "conflict",
    }


def test_services_layer_has_no_http_coupling() -> None:
    """服务层不得依赖 FastAPI 传输细节。"""
    offenders: list[str] = []
    app_root = Path(__file__).parent.parent / "app"
    for package in ("services", "repositories"):
        for path in (app_root / package).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "HTTPException" in source or "from fastapi" in source:
                offenders.append(str(path.relative_to(app_root)))
    assert offenders == [], f"服务层仍耦合 FastAPI：{offenders}"
