from __future__ import annotations

from html.parser import HTMLParser


_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }
)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if tag in _BLOCK_TAGS:
            self._line_break()

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._line_break()

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def _line_break(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")


def extract_visible_text(value: str) -> str:
    """Convert editor HTML to readable text while preserving paragraph boundaries."""
    if "<" not in value:
        return value
    parser = _VisibleTextParser()
    parser.feed(value)
    parser.close()
    lines = [
        " ".join(line.replace("\xa0", " ").split())
        for line in "".join(parser.parts).splitlines()
    ]
    return "\n".join(line for line in lines if line)
