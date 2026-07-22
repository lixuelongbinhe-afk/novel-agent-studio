from __future__ import annotations

import re


NUMBER_CHARACTERS = "0-9０-９零〇○一二两兩三四五六七八九十百千万萬壹贰叁肆伍陆柒捌玖拾佰仟"


def clean_outline_label(value: str) -> str:
    label = value.strip()
    for opening, closing in (("**", "**"), ("__", "__"), ("`", "`")):
        while (
            label.startswith(opening)
            and label.endswith(closing)
            and len(label) > len(opening) + len(closing)
        ):
            label = label[len(opening) : -len(closing)].strip()
    return label


def parse_number_token(value: str) -> int | None:
    token = value.strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if token.isdigit():
        number = int(token)
        return number if number > 0 else None
    digits = {
        "零": 0,
        "〇": 0,
        "○": 0,
        "一": 1,
        "壹": 1,
        "二": 2,
        "两": 2,
        "兩": 2,
        "贰": 2,
        "三": 3,
        "叁": 3,
        "四": 4,
        "肆": 4,
        "五": 5,
        "伍": 5,
        "六": 6,
        "陆": 6,
        "七": 7,
        "柒": 7,
        "八": 8,
        "捌": 8,
        "九": 9,
        "玖": 9,
    }
    units = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1_000, "仟": 1_000}
    large_units = {"万": 10_000, "萬": 10_000}
    if token and all(character in digits for character in token):
        number = int("".join(str(digits[character]) for character in token))
        return number if number > 0 else None
    total = section = current = 0
    for character in token:
        if character in digits:
            current = digits[character]
        elif character in units:
            section += (current or 1) * units[character]
            current = 0
        elif character in large_units:
            total += (section + current or 1) * large_units[character]
            section = current = 0
        else:
            return None
    number = total + section + current
    return number if number > 0 else None


def numbered_heading(label: str, unit: str) -> int | None:
    clean = clean_outline_label(label)
    token_pattern = f"([{NUMBER_CHARACTERS}]+)"
    boundary = r"(?=$|[\s:：·—\-（(《【])"
    patterns = [
        rf"^第\s*{token_pattern}\s*{unit}{boundary}",
        rf"^{unit}\s*{token_pattern}{boundary}",
    ]
    if unit == "卷":
        patterns.extend(
            (rf"^第\s*{token_pattern}\s*部{boundary}", rf"^部\s*{token_pattern}{boundary}")
        )
        english = re.match(rf"^volume\s*(\d+){boundary}", clean, re.I)
    else:
        english = re.match(rf"^chapter\s*(\d+){boundary}", clean, re.I)
    if english:
        return int(english.group(1))
    for pattern in patterns:
        match = re.match(pattern, clean, re.I)
        if match:
            return parse_number_token(match.group(1))
    return None


def chapter_title_number(title: str) -> int | None:
    return numbered_heading(title, "章")


def volume_title_number(title: str) -> int | None:
    return numbered_heading(title, "卷")


def is_generic_volume_title(title: str) -> bool:
    clean = clean_outline_label(title)
    return bool(re.fullmatch(rf"第\s*[{NUMBER_CHARACTERS}]+\s*(?:卷|部)", clean, re.I))
