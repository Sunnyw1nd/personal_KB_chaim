from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image

from experiments.image_router_benchmark.router_benchmark import (
    DEFAULT_BENCHMARK_MAPPING,
    DEFAULT_CATEGORIES,
    DEFAULT_MODELS,
    apply_route_threshold,
    create_input_manifest,
    directory_size,
    load_categories,
    prediction_cache_key,
    read_json,
    run_inference,
    score_predictions,
    sha256_file,
    validate_run_name,
    write_jsonl,
)


def make_image(path: Path, image_format: str, color: str) -> None:
    Image.new("RGB", (12, 8), color).save(path, image_format)


def test_inventory_loads_jpg_png_webp_reports_bad_file_and_duplicates(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    image_root.mkdir()
    make_image(image_root / "one.jpg", "JPEG", "red")
    make_image(image_root / "two.png", "PNG", "green")
    make_image(image_root / "three.webp", "WEBP", "blue")
    shutil.copyfile(image_root / "two.png", image_root / "duplicate.png")
    (image_root / "bad.png").write_bytes(b"not an image")
    output = tmp_path / "input-manifest.json"
    manifest = create_input_manifest(image_root, None, output)
    assert manifest["statistics"] == {
        "files": 5,
        "uniqueImages": 4,
        "duplicateGroups": 1,
        "decodable": 4,
        "decodeFailures": 1,
        "formats": {"JPEG": 1, "PNG": 2, "UNKNOWN": 1, "WEBP": 1},
    }
    assert len(manifest["files"]) == 5
    assert sum(item["duplicateCount"] == 2 for item in manifest["files"]) == 2
    assert output.is_file()


def test_frozen_categories_have_exact_enums_and_three_prompts_each() -> None:
    config, digest = load_categories(DEFAULT_CATEGORIES)
    assert len(config["types"]) == 11
    assert len(config["routes"]) == 7
    assert all(len(items) >= 3 for items in config["types"].values())
    assert len(digest) == 64


def test_route_threshold_rejects_low_margin_and_is_stric_for_skip() -> None:
    categories, _ = load_categories(DEFAULT_CATEGORIES)
    low_margin = [
        {"label": "local_ocr", "comparisonScore": 0.7, "cosineSimilarity": 0.31},
        {"label": "cloud_structured_visual", "comparisonScore": 0.2, "cosineSimilarity": 0.30},
    ]
    assert apply_route_threshold(low_margin, categories["routeThresholds"])[0] == "uncertain"
    skip = [
        {"label": "skip_candidate", "comparisonScore": 0.7, "cosineSimilarity": 0.40},
        {"label": "uncertain", "comparisonScore": 0.2, "cosineSimilarity": 0.30},
    ]
    final, reasons = apply_route_threshold(skip, categories["routeThresholds"])
    assert final == "uncertain"
    assert reasons == ["comparison_score_below_threshold"]


def test_prediction_cache_key_covers_model_revision_preprocess_config_and_image() -> None:
    models = read_json(DEFAULT_MODELS)["models"]
    config = dict(models["openai_clip_vit_b32"])
    base = prediction_cache_key("1" * 64, "openai_clip_vit_b32", config, "2" * 64)
    changed = dict(config, revision="3" * 40)
    assert base != prediction_cache_key("1" * 64, "openai_clip_vit_b32", changed, "2" * 64)
    changed = dict(config, adapterRevision="different-adapter")
    assert base != prediction_cache_key("1" * 64, "openai_clip_vit_b32", changed, "2" * 64)
    assert base != prediction_cache_key("4" * 64, "openai_clip_vit_b32", config, "2" * 64)
    assert base != prediction_cache_key("1" * 64, "openai_clip_vit_b32", config, "5" * 64)


def test_missing_resource_measurement_is_unknown_not_zero(tmp_path: Path) -> None:
    assert directory_size(tmp_path / "missing") is None


def make_benchmark_workbook(path: Path, image_sha: str) -> None:
    workbook = Workbook()
    main = workbook.active
    main.title = "标注主表"
    files = workbook.create_sheet("文件与来源")
    main.append(["title"])
    main.append([])
    main.append([])
    main.append(
        [
            "序号",
            "短 imageId",
            "原文件名",
            "图片主类型",
            "知识价值",
            "推荐处理方式",
            "审核状态",
        ]
    )
    main.append([1, image_sha[:12], "one.png", "二维码", "中", "二维码解码", "未审核"])
    files.append(["title"])
    files.append([])
    files.append([])
    files.append(["序号", "完整 imageId / SHA-256"])
    files.append([1, image_sha])
    workbook.save(path)


def prediction(image_sha: str) -> dict:
    route_scores = [
        {"label": "local_qr", "comparisonScore": 0.9, "cosineSimilarity": 0.5},
        {"label": "uncertain", "comparisonScore": 0.1, "cosineSimilarity": 0.2},
    ]
    type_scores = [
        {"label": "qr_code", "comparisonScore": 0.9, "cosineSimilarity": 0.5},
        {"label": "other_or_uncertain", "comparisonScore": 0.1, "cosineSimilarity": 0.2},
    ]
    return {
        "status": "success",
        "imageSha256": image_sha,
        "relativePath": "one.png",
        "typePrediction": {"top1": type_scores[0], "scores": type_scores},
        "routePrediction": {
            "rawTop1": route_scores[0],
            "finalLabel": "local_qr",
            "rejectionReasons": [],
            "scores": route_scores,
        },
    }


def test_score_reads_benchmark_only_after_prediction_and_marks_unreviewed_provisional(
    tmp_path: Path,
) -> None:
    image_sha = "1" * 64
    run = tmp_path / "runs" / "fake-run"
    run.mkdir(parents=True)
    predictions = run / "predictions.jsonl"
    write_jsonl(predictions, [prediction(image_sha)])
    (run / "run-manifest.json").write_text(
        json.dumps(
            {
                "runId": "fake-run",
                "model": "fake",
                "timing": {},
                "memory": {},
                "snapshotSizeBytes": None,
            }
        ),
        encoding="utf-8",
    )
    workbook = tmp_path / "benchmark.xlsx"
    make_benchmark_workbook(workbook, image_sha)
    path, result = score_predictions(
        predictions, workbook, DEFAULT_BENCHMARK_MAPPING, tmp_path / "output", None
    )
    assert result["benchmarkStatus"] == "provisional_v1"
    assert result["typeMetrics"]["accuracy"] == 1
    assert result["routeFinalMetrics"]["accuracy"] == 1
    assert path.is_file()
    raw_prediction = predictions.read_text(encoding="utf-8")
    assert "humanActionRaw" not in raw_prediction
    assert "knowledgeValue" not in raw_prediction


def test_scoring_without_matching_human_label_fails_clearly(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "fake-run"
    run.mkdir(parents=True)
    predictions = run / "predictions.jsonl"
    write_jsonl(predictions, [prediction("1" * 64)])
    (run / "run-manifest.json").write_text(
        json.dumps({"runId": "fake-run", "model": "fake"}), encoding="utf-8"
    )
    workbook = tmp_path / "benchmark.xlsx"
    make_benchmark_workbook(workbook, "2" * 64)
    with pytest.raises(ValueError, match="no successful predictions align"):
        score_predictions(
            predictions, workbook, DEFAULT_BENCHMARK_MAPPING, tmp_path / "output", None
        )


def test_infer_command_has_no_workbook_argument() -> None:
    source = (Path(__file__).parents[1] / "router_benchmark.py").read_text(encoding="utf-8")
    infer_section = source.split('infer = subparsers.add_parser("infer")', 1)[1].split(
        'score = subparsers.add_parser("score")', 1
    )[0]
    assert "workbook" not in infer_section


@pytest.mark.parametrize("value", ["../escape", "bad/name", " bad", "a" * 81])
def test_run_and_score_names_are_safe_path_components(value: str) -> None:
    with pytest.raises(ValueError):
        validate_run_name(value)


def test_run_name_accepts_stable_ascii_component() -> None:
    assert validate_run_name("mobileclip2-s0-zero-shot-v1") == "mobileclip2-s0-zero-shot-v1"


def test_full_prediction_cache_skips_model_loading(tmp_path: Path, monkeypatch) -> None:
    image_root = tmp_path / "images"
    image_root.mkdir()
    image_path = image_root / "one.png"
    make_image(image_path, "PNG", "red")
    image_sha = sha256_file(image_path)
    manifest_path = tmp_path / "input.json"
    manifest_path.write_text(
        json.dumps(
            {
                "imageRoot": str(image_root),
                "datasetFingerprintSha256": "f" * 64,
                "files": [
                    {
                        "imageSha256": image_sha,
                        "relativePath": "one.png",
                        "decodable": True,
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    models = read_json(DEFAULT_MODELS)["models"]
    model_name = "openai_clip_vit_b32"
    categories_sha = sha256_file(DEFAULT_CATEGORIES)
    key = prediction_cache_key(image_sha, model_name, models[model_name], categories_sha)
    work_root = tmp_path / "work"
    cache_path = work_root / "prediction-cache" / model_name / f"{key}.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "status": "success",
                "cacheKey": key,
                "imageSha256": image_sha,
                "relativePath": "old-name.png",
            }
        ),
        encoding="utf-8",
    )

    def fail_if_loaded(*_args, **_kwargs):
        raise AssertionError("the model must not load on a complete cache hit")

    monkeypatch.setattr(
        "experiments.image_router_benchmark.router_benchmark.load_encoder", fail_if_loaded
    )
    path, result = run_inference(
        manifest_path,
        DEFAULT_CATEGORIES,
        DEFAULT_MODELS,
        model_name,
        "cache-check",
        tmp_path / "output",
        work_root,
        "cpu",
        1,
        True,
    )
    assert path.is_file()
    assert result["modelLoadSkippedDueToFullCache"] is True
    assert result["statistics"]["cacheHits"] == 1
    prediction_row = json.loads((path.parent / "predictions.jsonl").read_text(encoding="utf-8"))
    assert prediction_row["relativePath"] == "one.png"
