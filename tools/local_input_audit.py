"""Read-only link/security check for explicitly scoped round15 browser evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    evidence = args.evidence.resolve(strict=True)
    output = args.output.resolve()
    if (
        not evidence.is_relative_to(project / ".work/round15")
        or not output.is_relative_to(project / ".work/round15")
        or output.exists()
    ):
        parser.error("use a new output within the scoped .work/round15 directory")
    missing, links = [], 0
    markdown = MarkdownIt()
    for path in [project / "README.md", *sorted((project / "docs").glob("*.md"))]:
        for token in markdown.parse(path.read_text(encoding="utf-8")):
            for child in token.children or []:
                if child.type not in {"link_open", "image"}:
                    continue
                raw = child.attrGet("href" if child.type == "link_open" else "src")
                url = urlsplit(raw or "")
                if url.scheme or url.netloc or not url.path:
                    continue
                links += 1
                if not (path.parent / unquote(url.path)).exists():
                    missing.append({"source": path.relative_to(project).as_posix(), "target": raw})
    checked, violations = 0, []
    pattern = re.compile(
        r"data:image/[^\s]+;base64,|Bearer\s+[A-Za-z0-9]|sk-[A-Za-z0-9]{16,}|Authorization",
        re.IGNORECASE,
    )
    for folder in ("data", "enrichment", "documents"):
        for path in (evidence / folder).rglob("*.json"):
            checked += 1
            if pattern.search(path.read_text(encoding="utf-8")):
                violations.append(path.relative_to(evidence).as_posix())
    result = {
        "localLinksChecked": links,
        "missingLinks": missing,
        "jsonChecked": checked,
        "sensitiveViolations": violations,
        "cloudCalls": 0,
        "mode": "read_only",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if missing or violations:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
