from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bs4 import BeautifulSoup, Tag

from ..identity import SourceIdentityValue
from ..models import Block, Message, Metadata


@dataclass(frozen=True)
class Detection:
    evidence: tuple[str, ...]


class ArticleAdapter(Protocol):
    name: str
    source_type: str
    document_prefix: str
    schema_version: str
    adapter_version: str
    body_selector: str

    def detect(self, soup: BeautifulSoup) -> Detection | None: ...

    def locate_body(self, soup: BeautifulSoup) -> Tag | None: ...

    def extract_metadata(
        self,
        soup: BeautifulSoup,
        raw_html: str,
        imported_at: str,
        messages: list[Message],
    ) -> Metadata: ...

    def source_identity(
        self,
        metadata: Metadata,
        html_sha256: str,
        messages: list[Message],
    ) -> SourceIdentityValue: ...

    def parse_blocks(
        self,
        root: Tag,
        image_handler: Callable[[Tag, str], str],
        messages: list[Message],
    ) -> list[Block]: ...

    def ancillary_resource_roles(
        self,
        root: Tag,
        resolve_local: Callable[[str | None], tuple[Path | None, bool]],
    ) -> dict[Path, str]: ...

    def excluded_regions(self) -> list[dict[str, str]]: ...
