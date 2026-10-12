"""Offline local inventory and integrity evidence; never reads credential contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from pathlib import Path


def linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def files(root: Path):
    if linked(root):
        return
    if root.is_file():
        yield root
    elif root.is_dir():
        with os.scandir(root) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                if not linked(path):
                    yield from files(path)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args]).decode("utf-8")


def protected(root: Path) -> dict[str, str]:
    scopes = [
        "samples",
        "test_pictures",
        "schemas",
        "src/wechat_html_normalizer/enrichment/prompts",
    ]
    scopes += ["config", "output"]
    for experiment in (root / "experiments").iterdir():
        if experiment.is_dir() and not linked(experiment):
            scopes += [
                f"experiments/{experiment.name}/{part}" for part in ("runs", "cache", "reports")
            ]
    result = {}
    for scope in scopes:
        for path in files(root / scope):
            relative = path.relative_to(root).as_posix()
            if "observer" in path.relative_to(root).parts:
                continue
            if path.name == ".env" or path.name.startswith(".env."):
                continue
            result[relative] = digest(path)
    return dict(sorted(result.items()))


def inventory(root: Path) -> dict:
    tracked = set(filter(None, git(root, "ls-files", "-z").split("\0")))
    rows = []
    for child in sorted(root.iterdir()):
        if child.name == ".git":
            continue
        members = list(files(child))
        sizes = [(path.stat().st_size, path.relative_to(root).as_posix()) for path in members]
        rows.append(
            {
                "path": child.name,
                "files": len(members),
                "bytes": sum(size for size, _ in sizes),
                "trackedFiles": sum(name in tracked for _, name in sizes),
                "extensions": dict(
                    sorted(Counter(path.suffix.lower() for path in members).items())
                ),
                "largest": sorted(sizes, reverse=True)[:3],
                "linked": linked(child),
            }
        )
    experiments = []
    for child in sorted((root / "experiments").iterdir()):
        if not child.is_dir() or linked(child):
            continue
        members = list(files(child))
        experiments.append(
            {
                "path": child.relative_to(root).as_posix(),
                "files": len(members),
                "bytes": sum(path.stat().st_size for path in members),
                "trackedFiles": sum(
                    path.relative_to(root).as_posix() in tracked for path in members
                ),
                "readme": (child / "README.md").is_file(),
                "reports": [path.name for path in sorted((child / "reports").glob("*.md"))],
                "runs": sorted(path.name for path in (child / "runs").glob("*") if path.is_dir()),
            }
        )
    return {
        "root": str(root),
        "branch": git(root, "branch", "--show-current").strip(),
        "head": git(root, "rev-parse", "HEAD").strip(),
        "status": git(root, "status", "--short"),
        "untracked": git(root, "ls-files", "--others", "--exclude-standard").splitlines(),
        "rows": rows,
        "experiments": experiments,
        "credentialFiles": {name: (root / name).is_file() for name in (".env", ".env.example")},
        "notes": ["Credential contents were not opened.", "No symlinks/junctions were followed."],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    lexical_output = args.output.absolute()
    if any(linked(parent) for parent in (lexical_output, *lexical_output.parents)):
        parser.error("refusing to use a linked output path")
    output = lexical_output.resolve(strict=False)
    if not output.is_relative_to(root / ".work"):
        parser.error("reports must be placed in the project's .work directory")
    if output.exists():
        parser.error("refusing to overwrite a report")
    if args.verify:
        before = json.loads(args.verify.read_text(encoding="utf-8"))["protectedSha256"]
        now = protected(root)
        report = {
            "checked": len(before),
            "changed": [name for name, sha in before.items() if now.get(name) != sha],
            "added": sorted(set(now) - set(before)),
        }
    else:
        report = inventory(root)
        report["protectedSha256"] = protected(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"report": str(output), "protectedFiles": len(report.get("protectedSha256", {}))}
        )
    )
    if args.verify and report["changed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
