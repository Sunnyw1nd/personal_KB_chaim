from __future__ import annotations

import json
import shutil
from pathlib import Path

from wechat_html_normalizer import normalize_wechat_article, preview_legacy_outputs
from wechat_html_normalizer.cli import main


def test_migration_preview_groups_without_modifying_legacy_outputs(
    tmp_path: Path,
    saved_article: tuple[Path, Path, Path],
) -> None:
    html, resources, _ = saved_article
    html.write_text(
        html.read_text(encoding="utf-8").replace(
            "https://mp.weixin.qq.com/s?id=abc",
            "https://mp.weixin.qq.com/s/migration-token",
        ),
        encoding="utf-8",
    )
    generated = normalize_wechat_article(
        html,
        resources,
        tmp_path / "generated",
        imported_at="2026-01-01T00:00:00Z",
    )
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    shutil.copytree(generated.output_directory, legacy / "wechat-old-a")
    shutil.copytree(generated.output_directory, legacy / "wechat-old-b")
    before = {
        path.relative_to(legacy).as_posix(): path.read_bytes()
        for path in legacy.rglob("*")
        if path.is_file()
    }
    report_path = tmp_path / "migration-preview.json"
    preview = preview_legacy_outputs(legacy, report_path)
    assert preview["statistics"] == {
        "legacyDocuments": 2,
        "reliableGroups": 1,
        "possibleDuplicateGroups": 1,
        "unresolved": 0,
        "invalid": 0,
        "alreadyManaged": 0,
    }
    group = preview["groups"][0]
    assert group["legacyPaths"] == ["wechat-old-a", "wechat-old-b"]
    assert group["recommendedCurrentLegacyPath"] == "wechat-old-b"
    assert preview["migrationExecuted"] is False
    assert json.loads(report_path.read_text(encoding="utf-8")) == preview
    assert before == {
        path.relative_to(legacy).as_posix(): path.read_bytes()
        for path in legacy.rglob("*")
        if path.is_file()
    }


def test_migration_preview_cli_writes_only_the_report(
    tmp_path: Path,
    saved_article: tuple[Path, Path, Path],
    capsys,
) -> None:
    html, resources, _ = saved_article
    generated = normalize_wechat_article(
        html, resources, tmp_path / "generated", imported_at="2026-01-01T00:00:00Z"
    )
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    shutil.copytree(generated.output_directory, legacy / "old")
    report = tmp_path / "report.json"
    exit_code = main(["--preview-migration", str(legacy), "--migration-report", str(report)])
    captured = capsys.readouterr()
    assert exit_code == 0 and not captured.err
    assert report.is_file()
    assert json.loads(captured.out)["legacyDocuments"] == 1
