from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from .parser import clean_text


def selector_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            value = clean_text(node.get_text(" ", strip=True))
            if value:
                return value
    return None


def meta_value(soup: BeautifulSoup, keys: list[tuple[str, str]]) -> str | None:
    for attribute, value in keys:
        node = soup.find("meta", attrs={attribute: value})
        if node and node.get("content"):
            return clean_text(str(node["content"]))
    return None


def normalize_datetime(raw: str | None) -> str | None:
    if not raw:
        return None
    value = clean_text(raw)
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.isoformat()
    except ValueError:
        pass
    match = re.search(
        r"(20\d{2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})(?:\s*日)?"
        r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        value,
    )
    if match:
        year, month, day, hour, minute, second = match.groups()
        parsed = datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=timezone(timedelta(hours=8)),
        )
        return parsed.isoformat()
    return None
