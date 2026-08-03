from app.services.chapter_tree import ChapterNode, ChapterTreeInvariants


def _node(
    number: int,
    *,
    volume_index: int = 1,
    recycled: bool = False,
    is_placeholder: bool = False,
    has_manuscript: bool = False,
) -> ChapterNode:
    return ChapterNode(
        number=number,
        volume_index=volume_index,
        recycled=recycled,
        is_placeholder=is_placeholder,
        has_manuscript=has_manuscript,
    )


def test_invariants_accept_a_complete_tree_and_ignore_recycled_nodes() -> None:
    invariants = ChapterTreeInvariants(total_chapters=3, total_volumes=2)
    nodes = [
        _node(1),
        _node(2, volume_index=2),
        _node(3, volume_index=2),
        _node(99, recycled=True),
    ]

    assert invariants.violations(nodes) == []


def test_invariants_report_all_structural_problems_together() -> None:
    invariants = ChapterTreeInvariants(total_chapters=3, total_volumes=2)
    nodes = [
        _node(1),
        _node(3, volume_index=3, is_placeholder=True, has_manuscript=True),
    ]

    assert invariants.violations(nodes) == [
        "章号不连续：1、3",
        "章节数 2 不等于项目设定 3",
        "占位章不得含正文",
        "卷序号越界：3 > 2",
    ]
