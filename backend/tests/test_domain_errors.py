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
